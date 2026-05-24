import datetime
import logging
from zoneinfo import ZoneInfo

import requests
import xmltodict
from django.conf import settings
from django.core.cache import cache
from django.db.models import Prefetch, prefetch_related_objects, IntegerField
from django.db.models.functions import Coalesce
from django.db.models import F, ExpressionWrapper
from django.utils import timezone
from xmltodict import unparse

from bustimes.utils import get_stop_times
from vehicles.models import VehicleJourney


TIMEZONE = ZoneInfo("Europe/London")


def get_departure_order(departure):
    if departure.get("live") and (
        not departure["time"] or departure["time"].date() == departure["live"].date()
    ):
        time = departure["live"]
    else:
        time = departure["time"]
    if timezone.is_naive(time):
        return time
    return timezone.make_naive(time, TIMEZONE)


class Departures:
    def __init__(self, stop, services, now=None):
        self.stop = stop
        self.now = now
        self.services = services


class RemoteDepartures(Departures):
    """Abstract class for getting departures from a source"""

    def __init__(self, stop, services, now=None):
        super().__init__(stop, services, now)

        self.services_by_name = {}
        duplicate_names = set()

        for service in services:
            for line_name in service.get_line_names():
                line_name = line_name.lower()
                if line_name in self.services_by_name:
                    duplicate_names.add(line_name)
                else:
                    self.services_by_name[line_name] = service

        for line_name in duplicate_names:
            del self.services_by_name[line_name]

    def get_request_url(self) -> str:
        """Return a URL string to pass to get_response"""
        return self.request_url

    def get_request_params(self):
        """Return a dictionary of HTTP GET parameters"""
        pass

    def get_request_headers(self):
        """Return a dictionary of HTTP headers"""
        pass

    def get_request_kwargs(self):
        return {
            "params": self.get_request_params(),
            "headers": self.get_request_headers(),
            "timeout": 5,
        }

    def get_response(self):
        return requests.get(self.get_request_url(), **self.get_request_kwargs())

    def get_service(self, line_name: str):
        if line_name:
            # try to find matching service (case-insensitively)
            line_name_lower = line_name.lower()
            if service := self.services_by_name.get(line_name_lower):
                return service

            # FlixBus
            elif service := self.services_by_name.get(f"uk{line_name_lower}"):
                return service

        # fallback
        return line_name

    def departures_from_response(self, res):
        """Given a Response object from the requests module,
        returns a list of departures
        """
        raise NotImplementedError

    def get_poorly_key(self):
        pass

    def set_poorly(self, timeout: int):
        key = self.get_poorly_key()
        if key:
            return cache.set(key, True, timeout)

    def get_departures(self):
        key = f"{self.__class__.__name__}:{self.stop.pk}"

        response = cache.get(key)

        if not response:
            try:
                response = self.get_response()
            except requests.exceptions.ReadTimeout:
                self.set_poorly(60)  # back off for 1 minute
                return
            except requests.exceptions.RequestException as e:
                self.set_poorly(60)  # back off for 1 minute
                logger = logging.getLogger(__name__)
                logger.exception(e)
                return

            if response.ok:
                cache.set(key, response, 60)
            else:
                self.set_poorly(1800)  # back off for 30 minutes
                return

        return self.departures_from_response(response)


class TflDepartures(RemoteDepartures):
    """Departures from the Transport for London API"""

    @staticmethod
    def get_request_params() -> dict:
        return settings.TFL

    def get_request_url(self) -> str:
        if self.stop.stop_type == "FBT" or self.stop.stop_type == "PLT":
            assert self.stop.stop_area_id
            return f"https://api.tfl.gov.uk/StopPoint/{self.stop.stop_area_id}/arrivals"
        return f"https://api.tfl.gov.uk/StopPoint/{self.stop.pk}/arrivals"

    def get_request_headers(self):
        return {"User-Agent": "bustimes.org"}

    def get_row(self, item):
        if item["modeName"] == "tube":
            vehicle = None
            link = None
        else:
            vehicle = item["vehicleId"]
            link = f"/vehicles/tfl/{vehicle}"
        return {
            "live": parse_datetime(item.get("expectedArrival")),
            "service": self.get_service(item.get("lineName")),
            "destination": item.get("destinationName"),
            "link": link,
            "vehicle": vehicle,
        }

    def departures_from_response(self, res) -> list:
        return sorted(
            [self.get_row(item) for item in res.json()], key=lambda row: row["live"]
        )


class TimetableDepartures(Departures):
    per_page = 12

    def get_row(self, stop_time):
        trip = stop_time.trip

        if stop_time.arrival is not None:
            arrival = stop_time.arrival_datetime(stop_time.date)
        else:
            arrival = None

        departure = stop_time.departure_datetime(stop_time.date)
        time = departure

        return {
            "origin_departure_time": trip.start_datetime(stop_time.date),
            "time": time,
            "date": stop_time.date,
            "arrival": arrival,
            "departure": departure,
            "destination": stop_time.destination,
            "link": trip.get_absolute_url(),
            "stop_time": stop_time,
            # "cancelled": stop_time.cancelled,
        }

    def get_times(self, date, time=None, trips=None, day_shift=0):
        return (
            get_stop_times(date, time, self.stop, self.routes, trips)
            .select_related("trip")
            .annotate(
                destination=Coalesce(
                    "trip__headsign",
                    "trip__destination__locality__name",
                    "trip__destination__common_name",
                ),
                order=ExpressionWrapper(
                    F("departure") + day_shift * 86400, output_field=IntegerField()
                ),
                # cancelled=Exists(
                #     Call.objects.filter(
                #         journey__situation__current=True,
                #         journey__trip=OuterRef("trip"),
                #         stop_time=OuterRef("id"),
                #         condition="notStopping",
                #     )
                # ),
            )
        ).order_by("departure")

    def get_departures(self):
        time_since_midnight = datetime.timedelta(
            hours=self.now.hour, minutes=self.now.minute
        )
        date = self.now.date()
        one_day = datetime.timedelta(1)
        yesterday_date = (self.now - one_day).date()
        yesterday_time = time_since_midnight + one_day

        all_today_times = (
            self.get_times(yesterday_date, yesterday_time)
            .union(self.get_times(date, time_since_midnight, day_shift=1), all=True)
            .order_by("order", "id")
        )
        today_times = list(all_today_times[: self.per_page])

        if self.trips:
            late_times = self.get_times(date, time_since_midnight, self.trips)
            today_times = list(late_times) + today_times

        # for eg Victoria Coach Station where there are so many departures at the same time:
        if len(today_times) == self.per_page:
            while all(
                today_times[0].departure == time.departure for time in today_times[1:]
            ) and (
                more_times := all_today_times[len(today_times) : len(today_times) + 8]
            ):
                today_times += more_times

        times = [self.get_row(stop_time) for stop_time in today_times]

        # prefetch journeys to show which vehicle is operating journey
        dates = {time["date"] for time in times}
        prefetch_related_objects(
            [time["stop_time"].trip for time in times],
            Prefetch(
                "vehiclejourney_set",
                VehicleJourney.objects.filter(date__in=dates).select_related("vehicle"),
                to_attr="vehicle_journeys",
            ),
        )
        for time in times:
            trip_date = time["date"]
            for journey in time["stop_time"].trip.vehicle_journeys:
                if journey.date == trip_date:
                    time["vehicle"] = journey.vehicle
                    time["link"] = f"/journeys/{journey.id}"
                    break

        # # add tomorrow's times until there are 10, or the next day until there more than 0
        # i = 0
        # while not times and i < 3 or len(times) < 10 and i == 0:
        #     i += 1
        #     date += one_day
        #     times += [
        #         self.get_row(stop_time)
        #         for stop_time in self.get_times(date)[: 10 - len(times)]
        #     ]

        routes = {route.id: route for route in self.routes}
        services = {s.id: s for s in self.services}
        for trip in times:
            trip["route"] = routes.get(trip["stop_time"].trip.route_id)
            if trip["route"]:
                trip["service"] = services.get(trip["route"].service_id)

        return times

    def __init__(self, stop, services, now, routes, trips=None):
        self.routes = routes
        self.tracking = any(service.tracking for service in services)
        self.trips = trips
        super().__init__(stop, services, now)


def parse_datetime(string):
    return datetime.datetime.fromisoformat(string).astimezone(TIMEZONE)


class SiriSmDepartures(RemoteDepartures):
    ns = {"s": "http://www.siri.org.uk/siri"}
    data_source = None

    def __init__(self, source, stop, services):
        self.source = source
        super().__init__(stop, services)

    def get_row(self, item):
        journey = item["MonitoredVehicleJourney"]

        call = journey["MonitoredCall"]
        aimed_time = call.get("AimedDepartureTime")
        expected_time = call.get("ExpectedDepartureTime")
        if aimed_time:
            aimed_time = parse_datetime(aimed_time)
        if expected_time:
            expected_time = parse_datetime(expected_time)

        departure_status = call.get("DepartureStatus")
        arrival_status = call.get("ArrivalStatus")

        line_name = journey.get("LineName") or journey.get("LineRef")
        destination = journey.get("DestinationName") or journey.get(
            "DestinationDisplay"
        )

        service = self.get_service(line_name)

        return {
            "time": aimed_time,
            "live": expected_time,
            "service": service,
            "destination": destination,
            "data": journey,
            "cancelled": departure_status == "cancelled"
            or arrival_status == "cancelled",
            "vehicle": journey.get("VehicleRef"),
        }

    def get_poorly_key(self):
        return self.source.get_poorly_key()

    def departures_from_response(self, response):
        if not response.text or "Client.AUTHENTICATION_FAILED" in response.text:
            self.set_poorly(1800)  # back off for 30 minutes
            return
        data = xmltodict.parse(response.text)
        try:
            data = data["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"][
                "MonitoredStopVisit"
            ]
        except (KeyError, TypeError):
            return
        if type(data) is list:
            return [self.get_row(item) for item in data]
        return [self.get_row(data)]

    def get_response(self):
        now = datetime.datetime.utcnow().isoformat()
        request_xml = unparse(
            {
                "Siri": {
                    "@version": "1.3",
                    "@xmlns": "http://www.siri.org.uk/siri",
                    "ServiceRequest": {
                        "RequestTimestamp": now,
                        "RequestorRef": self.source.requestor_ref,
                        "StopMonitoringRequest": {
                            "@version": "1.3",
                            "RequestTimestamp": now,
                            "MonitoringRef": self.stop.atco_code,
                        },
                    },
                }
            },
        )

        headers = {"Content-Type": "application/xml"}
        return requests.post(
            self.source.url, data=request_xml, headers=headers, timeout=5
        )
