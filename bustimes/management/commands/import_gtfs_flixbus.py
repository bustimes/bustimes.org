import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gtfs_kit
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Min
from django.utils.dateparse import parse_duration

from busstops.models import DataSource, Operator, Service, StopPoint

from ...download_utils import download_if_modified
from ...gtfs_utils import (
    MODES,
    RouteType,
    do_route_links,
    get_arrival_and_departure,
    get_calendars,
    get_first_and_last_stop_times,
)
from ...models import Route, StopTime, Trip

logger = logging.getLogger(__name__)


MODES = {**MODES, RouteType.bus: "coach"}


def get_stoppoint(stop, source):
    stoppoint = StopPoint(
        atco_code=stop.stop_id,
        naptan_code=stop.stop_code,
        common_name=stop.stop_name,
        active=True,
        source=source,
        latlong=f"POINT({stop.stop_lon} {stop.stop_lat})",
    )

    if len(stoppoint.common_name) > 48:
        if " (" in stoppoint.common_name and stoppoint.common_name[-1] == ")":
            stoppoint.common_name, stoppoint.indicator = stoppoint.common_name.split(
                " (", 1
            )
            stoppoint.indicator = stoppoint.indicator[:-1]
        else:
            stoppoint.common_name = stoppoint.common_name[:48]

    return stoppoint


class Command(BaseCommand):
    def handle(self, *args, **options):
        operator = Operator.objects.get(name="FlixBus")
        source, _ = DataSource.objects.get_or_create(name="FlixBus")

        path = settings.DATA_DIR / Path("flixbus_eu.zip")

        source.url = "https://gtfs.gis.flix.tech/gtfs_generic_eu.zip"

        modified, last_modified = download_if_modified(path, source)

        if not modified:
            return

        logger.info(f"{source} {last_modified}")

        feed = gtfs_kit.read_feed(path, dist_units="km")

        mask = feed.routes.route_id.str.startswith(
            "UK"
        ) | feed.routes.route_long_name.str.contains("London")
        feed = feed.restrict_to_routes(feed.routes[mask].route_id)

        stops_data = {row.stop_id: row for row in feed.stops.itertuples()}
        stop_codes = {
            stop_code.code: stop_code.stop_id for stop_code in source.stopcode_set.all()
        }
        missing_stops = {}

        existing_services = {
            service.line_name: service for service in operator.service_set.all()
        }
        existing_routes = {route.code: route for route in source.route_set.all()}
        routes = []

        calendars = get_calendars(feed, source)

        # get UTC offset (0 or 1 hours) at midday at the start of each calendar
        # (the data uses UTC times but we want local times)
        tzinfo = ZoneInfo("Europe/London")
        utc_offsets = {
            calendar.start_date: datetime.strptime(
                f"{calendar.start_date} 12", "%Y%m%d %H"
            )
            .replace(tzinfo=tzinfo)
            .utcoffset()
            for calendar in calendars.values()
        }

        for row in feed.routes.itertuples():
            line_name = row.route_id

            if line_name in existing_services:
                service = existing_services[line_name]
            elif line_name.removeprefix("UK") in existing_services:
                service = existing_services[line_name.removeprefix("UK")]
            else:
                service = Service()

            if row.route_id in existing_routes:
                route = existing_routes[row.route_id]
            else:
                route = Route(code=row.route_id, source=source)
            route.service = service
            route.line_name = line_name
            service.line_name = line_name
            service.description = route.description = row.route_long_name
            service.current = True
            service.colour_id = operator.colour_id
            service.source = source
            service.region_id = "GB"
            service.mode = MODES[row.route_type]

            service.save()
            service.operator.add(operator)
            route.save()

            routes.append(route)

            existing_routes[route.code] = route  # deals with duplicate rows

        existing_trips = {
            trip.vehicle_journey_code: trip for trip in operator.trip_set.all()
        }
        trips = {}
        for row in feed.trips.itertuples():
            # evenness of the number after the first hyphen
            # (e.g. "3" in "UK070-3-1910012026-...")
            # determines direction
            journey_number = int(row.trip_id.split("-")[1])
            trip = Trip(
                route=existing_routes[row.route_id],
                calendar=calendars[row.service_id],
                inbound=journey_number % 2 == 0,
                vehicle_journey_code=row.trip_id,
                headsign=row.trip_headsign if pd.notna(row.trip_headsign) else None,
                operator=operator,
                journey_pattern=row.shape_id,
            )
            if trip.vehicle_journey_code in existing_trips:
                # reuse existing trip id
                trip.id = existing_trips[trip.vehicle_journey_code].id
            trips[trip.vehicle_journey_code] = trip
        del existing_trips

        sorted_stop_times, first_stop_times, last_stop_times = (
            get_first_and_last_stop_times(
                pd.merge(feed.stop_times, feed.trips, on="trip_id")
            )
        )

        stop_times = []
        for row in sorted_stop_times.itertuples():
            trip = trips[row.trip_id]
            offset = utc_offsets[trip.calendar.start_date]

            is_first = row.stop_sequence == first_stop_times.stop_sequence[row.trip_id]
            is_last = row.stop_sequence == last_stop_times.stop_sequence[row.trip_id]

            arrival_time = parse_duration(row.arrival_time) + offset
            departure_time = parse_duration(row.departure_time) + offset

            if is_first:
                trip.start = departure_time

            arrival_time, departure_time = get_arrival_and_departure(
                arrival_time, departure_time, is_last
            )

            stop_time = StopTime(
                arrival=arrival_time,
                departure=departure_time,
                sequence=row.stop_sequence,
                trip=trip,
                # can't be picked up at the last stop, or set down at the first stop
                pick_up=(row.pickup_type != 1) and not is_last,
                set_down=(row.drop_off_type != 1) and not is_first,
            )

            # (a bit pointless as I think all their stops are timing points and/or they leave this column blank)
            if pd.notna(row.timepoint) and row.timepoint == 1:
                stop_time.timing_point = True
            else:
                stop_time.timing_point = False

            if row.stop_id in stop_codes:
                stop_time.stop_id = stop_codes[row.stop_id]
            else:
                stop = stops_data[row.stop_id]
                stop_time.stop_id = row.stop_id

                # create new StopPoint
                if row.stop_id not in missing_stops:
                    missing_stops[row.stop_id] = get_stoppoint(stop, source)

                    if stop.stop_timezone == "Europe/London":
                        # stop appears to be in the UK,
                        # so we might want to link it to the corresponding NaPTAN stop
                        logger.info(f"{stop.stop_name} {stop.stop_code}")
                        logger.info(
                            f"    https://bustimes.org/map#16/{stop.stop_lat}/{stop.stop_lon}"
                        )
                        logger.info(
                            f"    https://bustimes.org/admin/busstops/stopcode/add/?code={row.stop_id}"
                        )

            stop_times.append(stop_time)

            if is_last:
                trip.end = stop_time.arrival
                trip.destination_id = stop_time.stop_id

        StopPoint.objects.bulk_create(
            missing_stops.values(),
            update_conflicts=True,
            update_fields=["common_name", "indicator", "naptan_code", "latlong"],
            unique_fields=["atco_code"],
        )

        # if no timing points specified (because FlixBus), set all stops as timing points
        if not any(stop_time.timing_point for stop_time in stop_times):
            for stop_time in stop_times:
                stop_time.timing_point = True

        with transaction.atomic():
            existing_trips = [trip for trip in trips.values() if trip.id]
            Trip.objects.bulk_create([trip for trip in trips.values() if not trip.id])
            Trip.objects.bulk_update(
                existing_trips,
                fields=[
                    "route",
                    "calendar",
                    "inbound",
                    "start",
                    "end",
                    "destination",
                    "vehicle_journey_code",
                    "headsign",
                ],
            )

            StopTime.objects.filter(trip__in=existing_trips).delete()
            StopTime.objects.bulk_create(stop_times)

            for service in source.service_set.filter(current=True):
                service.do_stop_usages()
                service.update_search_vector()
                service.update_geometry()

            logger.info(
                source.route_set.exclude(id__in=[route.id for route in routes]).delete()
            )

            for route in source.route_set.annotate(
                start=Min("trip__calendar__start_date")
            ):
                route.start_date = route.start
                route.save(update_fields=["start_date"])

            logger.info(
                operator.trip_set.exclude(
                    id__in=[trip.id for trip in trips.values()]
                ).delete()
            )
            logger.info(
                operator.service_set.filter(current=True, route__isnull=True).update(
                    current=False
                )
            )
            if last_modified:
                source.datetime = last_modified
                source.save(update_fields=["datetime"])

        do_route_links(feed, source, existing_routes, stops_data, stop_codes)
