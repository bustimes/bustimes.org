"""Various ways of getting live departures from some web service"""

import datetime

from django.db.models import Q
from django.utils import timezone

from busstops.models import Service, SIRISource, StopPoint
from bustimes.models import Route, Trip
from bustimes.utils import get_stop_times
from vehicles import rtpi
from vehicles.models import VehicleJourney
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
    if services_match(a["service"], b["service"]) and a["time"] and b["time"]:
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
                    }

    routes = (
        Route.objects.filter(service__in=[s for s in services if not s.timetable_wrong])
        .select_related("source")
        .with_service_modified_at()
    )

    now = None
    for route in routes:
        if route.timezone:
            now = timezone.localtime(timezone=route.timezone)
            break
    if not now:
        now = timezone.localtime()

    if when and timezone.is_naive(when):
        # treat the user-chosen date and time as stop-local
        when = timezone.make_aware(when, now.tzinfo)

    departures = None

    gtfsr_feed_name = next(
        (
            feed_name
            for route in routes
            if (feed_name := gtfsr.FEED_NAMES.get(route.source.name))
            and feed_name != "flixbus"
        ),
        None,
    )
    gtfsr_available = gtfsr_feed_name is not None

    if not when and live_departures is None and not gtfsr_available:
        vehicle_locations = avl.get_tracking(stop, services)
        if vehicle_locations:
            by_trip = rtpi.by_trip_id(vehicle_locations)
            by_block = {
                (item["date"], item.get("operator_id"), item["block"]): item
                for item in vehicle_locations
                if item.get("block")
            }

            if by_trip:
                departures = TimetableDepartures(
                    stop, services, now, routes, by_trip
                ).get_departures()

                for departure in departures:
                    if departure["time"] - now > datetime.timedelta(hours=12):
                        break

                    if departure["time"] < now:
                        departure["overdue"] = True

                    stop_time = departure["stop_time"]
                    date = str(departure["date"])

                    if (item := by_trip.get(stop_time.trip_id)) and item[
                        "date"
                    ] == date:
                        if "delay" not in item:
                            continue

                        if (
                            "overdue" not in departure
                            or "progress" in item
                            and rtpi.is_before_stop(item, stop_time)
                        ):
                            delay = item["delay"]

                            if (
                                delay < 0
                                and "progress" in item
                                and item["progress"]["sequence"] == 0
                            ):
                                # early at first stop - assume won't leave early
                                delay = 0
                            departure["live"] = departure["time"] + datetime.timedelta(
                                seconds=delay
                            )

                    elif item := by_block.get(
                        (date, stop_time.trip.operator_id, stop_time.trip.block)
                    ):
                        departure["journey_id"] = item["journey_id"]

                if journey_ids := [
                    departure["journey_id"]
                    for departure in departures
                    if "journey_id" in departure
                ]:
                    journeys = VehicleJourney.objects.select_related("vehicle").in_bulk(
                        journey_ids
                    )
                    for departure in departures:
                        if journey := journeys.get(departure.get("journey_id")):
                            departure["vehicle"] = journey.vehicle

                # filter out departures that have already happened
                departures = [
                    d for d in departures if "overdue" not in d or "live" in d
                ]

    trip_updates = None
    if gtfsr_available and (trip_updates := gtfsr.get_trip_updates(gtfsr_feed_name)):
        if not when and live_departures is None:
            # include late-running trips' already-scheduled departures, like for AVL
            trips = Trip.objects.filter(
                route__in=routes, ticket_machine_code__in=trip_updates.keys()
            ).values_list("id", flat=True)
            departures = TimetableDepartures(
                stop, services, now, routes, list(trips)
            ).get_departures()
            gtfsr.update_stop_departures(departures, trip_updates)

            # filter out departures that have already happened
            minute = now.replace(second=0, microsecond=0)
            departures = [
                d
                for d in departures
                if d["time"] >= minute or d.get("live") and d["live"] >= now
            ]
        else:
            departures = None

    if departures is None:
        departures = TimetableDepartures(
            stop, services, when or now, routes
        ).get_departures()
        if trip_updates:
            gtfsr.update_stop_departures(departures, trip_updates)

    if live_departures:
        departures = live_departures + departures

    one_hour = datetime.timedelta(hours=1)
    one_hour_ago = now - one_hour

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
                                service=(
                                    row["service"].pk
                                    if type(row["service"]) is Service
                                    else None
                                ),
                                data=row["data"],
                                time=row.get("origin_departure_time"),
                                date=row.get("date"),
                                destination=str(row["destination"]),
                                source_name=source.name,
                                url=source.url,
                                trip_id=(
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
