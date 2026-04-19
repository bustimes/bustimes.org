"""Various ways of getting live departures from some web service"""

import datetime

from django.db.models import Prefetch, prefetch_related_objects, Q
from django.utils import timezone

from busstops.models import Service, SIRISource, StopPoint
from bustimes.models import Route, StopTime
from bustimes.utils import get_stop_times
from vehicles import rtpi
from vehicles.tasks import log_vehicle_journey

from . import avl, gtfsr
from .sources import (
    SiriSmDepartures,
    TflDepartures,
    TimetableDepartures,
    get_departure_order,
)


def services_match(a, b):
    if type(a) is Service:
        a = a.line_name
    if type(b) is Service:
        b = b.line_name
    return a.lower() == b.lower()


def can_sort(departure):
    return (
        type(departure["time"]) is datetime.datetime
        or type(departure.get("live")) is datetime.datetime
    )


def rows_match(a, b):
    if services_match(a["service"], b["service"]):
        if a["time"] and b["time"]:
            if a.get("arrival") and b.get("arrival"):
                key = "arrival"
            else:
                key = "time"
            return abs(a[key] - b[key]) <= datetime.timedelta(minutes=2)


def blend(departures, live_rows, stop=None):
    added = False
    for live_row in live_rows:
        replaced = False
        for row in departures:
            if rows_match(row, live_row):
                if live_row.get("live"):
                    row["live"] = live_row["live"]
                if "data" in live_row:
                    row["data"] = live_row["data"]
                if "cancelled" in live_row:
                    row["cancelled"] = live_row["cancelled"]
                if "vehicle" in live_row and not row.get("vehicle"):
                    row["vehicle"] = live_row["vehicle"]
                replaced = True
                break
        if not replaced and (live_row.get("live") or live_row["time"]):
            departures.append(live_row)
            added = True
    if added and all(can_sort(departure) for departure in departures):
        departures.sort(key=get_departure_order)


def update_trip_ids(departures: list, live_rows: list) -> None:
    for live_row in live_rows:
        live_time = live_row["time"] or live_row["live"]
        for row in departures:
            if row["service"] == live_row["service"] and (
                abs(row["time"] - live_time) <= datetime.timedelta(minutes=2)
            ):
                live_row["link"] = row["link"]
                trip = row["stop_time"].trip
                if trip.ticket_machine_code != live_row["tripId"]:
                    trip.ticket_machine_code = live_row["tripId"]
                    trip.save(update_fields=["ticket_machine_code"])
                break


def get_departures(stop, services, when) -> dict:
    live_departures = None

    # Transport for London
    if not when and type(stop) is StopPoint:
        tfl_services = [s for s in services if s.service_code[:4] == "tfl_"]
        if tfl_services:
            live_departures = TflDepartures(stop, tfl_services).get_departures()
            if live_departures:
                # non-TfL services
                services = [s for s in services if s.service_code[:4] != "tfl_"]
                if not services:
                    return {
                        "departures": live_departures,
                        "today": timezone.localdate(),
                    }

    now = timezone.localtime()

    routes = Route.objects.filter(
        service__in=[s for s in services if not s.timetable_wrong]
    ).select_related("source")
    departures = None

    ntaie = any(route.source.name == "Realtime Transport Operators" for route in routes)
    ember = any(route.source.name == "Ember" for route in routes)
    gtfsr_available = ntaie or ember

    if not when and live_departures is None and not gtfsr_available:
        vehicle_locations = avl.get_tracking(stop, services)
        if vehicle_locations:
            by_trip = {
                item["trip_id"]: item for item in vehicle_locations if "trip_id" in item
            }

            if by_trip:
                departures = TimetableDepartures(
                    stop, services, now, routes, by_trip
                ).get_departures()

                if by_trip:
                    # prefetch stoptimes, for calculating delay
                    prefetch_related_objects(
                        [
                            departure["stop_time"].trip
                            for departure in departures
                            if departure["stop_time"].trip_id in by_trip
                        ],
                        Prefetch(
                            "stoptime_set",
                            StopTime.objects.select_related("stop").only(
                                "trip_id", "arrival", "departure", "stop__latlong"
                            ),
                        ),
                    )

                for departure in departures:
                    if departure["time"] - now > datetime.timedelta(hours=12):
                        break

                    trip_id = departure["stop_time"].trip_id

                    if trip_id in by_trip:
                        item = by_trip[trip_id]

                        if "progress" not in item:
                            rtpi.add_progress_and_delay(item, departure["stop_time"])

                        if "progress" not in item:
                            continue

                        if (
                            departure["time"] >= now
                            or item["progress"]["id"] < departure["stop_time"].id
                            or item["progress"]["id"] == departure["stop_time"].id
                            and item["progress"]["progress"] == 0
                        ):
                            delay = item["delay"]

                            if delay < 0 and item["progress"]["sequence"] == 0:
                                # early at first stop - assume won't leave early
                                delay = 0
                            departure["live"] = departure["time"] + datetime.timedelta(
                                seconds=delay
                            )

                # filter out departures that have already happened
                departures = [d for d in departures if d["time"] >= now or "live" in d]

    if departures is None:
        departures = TimetableDepartures(
            stop, services, when or now, routes
        ).get_departures()

    if live_departures:
        departures = live_departures + departures

    one_hour = datetime.timedelta(hours=1)
    one_hour_ago = now - one_hour

    if departures and gtfsr_available:
        gtfsr.update_stop_departures(departures, "ember" if ember else "ntaie")

    if when or live_departures or type(stop) is not StopPoint:
        pass
    elif not departures or (
        (departures[0]["time"] - now) < one_hour
        or get_stop_times(
            one_hour_ago.date(),
            datetime.timedelta(hours=one_hour_ago.hour, minutes=one_hour_ago.minute),
            stop,
            routes,
        ).exists()
    ):
        live_rows = None

        operator_names: set[str] = set()
        for service in services:
            if service.operators:
                operator_names.update(service.operators)

        if departures:
            source = None

            if stop.admin_area_id:
                for possible_source in SIRISource.objects.filter(
                    Q(admin_areas=stop.admin_area_id)
                    | Q(operators__service__in=list(services))
                ):
                    if not possible_source.is_poorly():
                        source = possible_source
                        break

            if source:
                live_rows = SiriSmDepartures(source, stop, services).get_departures()

            if live_rows:
                blend(departures, live_rows)

                if source:
                    # Record some information about the vehicle and journey,
                    # for enthusiasts,
                    # because the source doesn't support vehicle locations
                    for row in departures:
                        if "data" in row and "VehicleRef" in row["data"]:
                            log_vehicle_journey(
                                (
                                    row["service"].pk
                                    if type(row["service"]) is Service
                                    else None
                                ),
                                row["data"],
                                (
                                    str(row["origin_departure_time"])
                                    if "origin_departure_time" in row
                                    else None
                                ),
                                str(row["destination"]),
                                source.name,
                                source.url,
                                (
                                    row["stop_time"].trip_id
                                    if "stop_time" in row
                                    else None
                                ),
                            )

    return {
        "departures": departures,
        "today": now.date(),
        "now": now,
        "when": when or now,
    }
