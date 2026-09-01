import logging
from pathlib import Path

import gtfs_kit
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Min, OuterRef, Subquery

from busstops.models import DataSource, Operator, Service, ServiceColour, StopPoint

from ...download_utils import download_if_modified
from ...gtfs_utils import (
    MODES,
    do_route_links,
    get_arrival_and_departure,
    get_calendars,
    get_first_and_last_stop_times,
)
from ...models import Route, StopTime, Trip

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Import data even if the GTFS feeds haven't changed",
        )

    def handle(self, force, *args, **options):
        path = settings.DATA_DIR / Path("rtcsnv_gtfs.zip")

        source, _ = DataSource.objects.get_or_create(name="RTCSNV")
        source.url = "https://developer.rtcsnv.com/transitData/google_transit.zip"

        modified, last_modified = download_if_modified(path, source)

        if not modified and not force:
            return  # no new data to import
        source.datetime = last_modified

        logger.info(f"{source} {last_modified}")

        feed = gtfs_kit.read_feed(path, dist_units="km")

        feed.stop_times = feed.stop_times.fillna(
            {"timepoint": 1, "pickup_type": 0, "drop_off_type": 0}
        )

        operator = Operator.objects.get_or_create(noc="RTCSNV")[0]

        existing_services = {
            service.line_name: service for service in operator.service_set.all()
        }
        existing_routes = {route.code: route for route in source.route_set.all()}
        routes = []

        # upsert stops
        stops = {}
        new_stops = [
            StopPoint(
                atco_code=f"rtcsnv-{stop.stop_id}",
                common_name=stop.stop_name,
                active=True,
                source=source,
                latlong=f"POINT({stop.stop_lon} {stop.stop_lat})",
                timezone="America/Los_Angeles",
                bearing=stop.stop_name[0]
                if stop.stop_name[:2].upper() in ("NB", "EB", "SB", "WB")
                else "",
            )
            for stop in feed.stops.itertuples()
        ]
        StopPoint.objects.bulk_create(
            new_stops,
            update_conflicts=True,
            unique_fields=["atco_code"],
            update_fields=["common_name", "latlong", "bearing"],
        )
        for stop in new_stops:
            stops[stop.atco_code.removeprefix("rtcsnv-")] = stop

        calendars = get_calendars(feed, source)

        colours = {
            (colour.background, colour.foreground): colour
            for colour in ServiceColour.objects.all()
        }

        for row in feed.get_routes(as_gdf=True).itertuples():
            if row.route_short_name in existing_services:
                service = existing_services[row.route_short_name]
            else:
                service = Service(line_name=row.route_short_name)
                existing_services[row.route_short_name] = service

            if row.route_id in existing_routes:
                route = existing_routes[row.route_id]
            else:
                route = Route(code=row.route_id)
            route.timezone = "America/Los_Angeles"
            route.source = source
            route.service = service
            route.line_name = row.route_short_name
            service.source = source
            service.description = route.description = row.route_long_name
            service.current = True

            bg, fg = (f"#{row.route_color}", f"#{row.route_text_color}")
            if (bg, fg) not in colours:
                colours[(bg, fg)] = ServiceColour.objects.create(
                    background=bg, foreground=fg
                )
            service.colour = colours[(bg, fg)]

            service.mode = MODES[row.route_type]
            if row.geometry:
                service.geometry = row.geometry.wkt

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
            route = existing_routes[row.route_id]
            headsign = row.trip_headsign.removeprefix(f"{route.line_name} ")
            trip = Trip(
                route=route,
                calendar=calendars[row.service_id],
                inbound=row.direction_id == 1,
                ticket_machine_code=row.trip_id,
                vehicle_journey_code=row.trip_id,
                operator=operator,
                headsign=headsign,
            )
            if trip.vehicle_journey_code in existing_trips:
                # reuse existing trip id
                trip.id = existing_trips[trip.vehicle_journey_code].id
            trips[trip.vehicle_journey_code] = trip
        del existing_trips

        stop_times_df = feed.stop_times.copy()
        # trim leading " " from times
        stop_times_df["arrival_time"] = stop_times_df.arrival_time.str.lstrip()
        stop_times_df["departure_time"] = stop_times_df.departure_time.str.lstrip()
        sorted_stop_times, first_stop_times, last_stop_times = (
            get_first_and_last_stop_times(stop_times_df)
        )

        stop_times = []
        for row in sorted_stop_times.itertuples():
            trip = trips[row.trip_id]

            is_last = row.stop_sequence == last_stop_times.stop_sequence[row.trip_id]
            arrival, departure = get_arrival_and_departure(
                row.arrival_time, row.departure_time, is_last
            )

            stop_time = StopTime(
                arrival=arrival,
                departure=departure,
                sequence=row.stop_sequence,
                trip=trip,
                timing_point=bool(row.timepoint),
                pick_up=(row.pickup_type != 1),
                set_down=(row.drop_off_type != 1),
            )

            stop_time.stop = stops[row.stop_id]

            stop_times.append(stop_time)

        for trip_id, trip in trips.items():
            start = first_stop_times.departure_time.get(trip_id)
            end = last_stop_times.arrival_time.get(trip_id)
            if pd.isna(start) or pd.isna(end):
                logger.warning(f"trip {trip_id} has no stop times")
                trips[trip_id] = None
            else:
                trip.start = start
                trip.end = end
                trip.destination = stops.get(last_stop_times.stop_id.get(trip_id))

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        stop_codes = {stop_id: stop.atco_code for stop_id, stop in stops.items()}
        do_route_links(feed, source, existing_routes, feed_stops, stop_codes)

        with transaction.atomic():
            trip_objs = [trip for trip in trips.values() if trip is not None]
            existing_trips = [trip for trip in trip_objs if trip.id]
            Trip.objects.bulk_create([trip for trip in trip_objs if not trip.id])
            Trip.objects.bulk_update(
                existing_trips,
                fields=[
                    "route",
                    "calendar",
                    "start",
                    "end",
                    "destination",
                    "block",
                    "vehicle_journey_code",
                    "ticket_machine_code",
                    "inbound",
                    "headsign",
                ],
            )

            StopTime.objects.filter(trip__in=existing_trips).delete()
            StopTime.objects.bulk_create(stop_times)

            for service in source.service_set.filter(current=True):
                service.do_stop_usages()
                service.update_search_vector()

            logger.info(
                source.route_set.exclude(id__in=[route.id for route in routes]).delete()
            )
            logger.info(
                operator.trip_set.exclude(
                    id__in=[trip.id for trip in trip_objs]
                ).delete()
            )
            logger.info(
                operator.service_set.filter(current=True, route__isnull=True).update(
                    current=False
                )
            )

            source.route_set.update(
                start_date=Subquery(
                    Route.objects.filter(pk=OuterRef("pk"))
                    .annotate(min_date=Min("trip__calendar__start_date"))
                    .values("min_date")[:1]
                )
            )

            source.save(update_fields=["url", "datetime"])
