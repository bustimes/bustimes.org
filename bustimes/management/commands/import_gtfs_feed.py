import logging
import math
import re
from pathlib import Path

import gtfs_kit
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from busstops.models import DataSource, Operator, Service, ServiceColour, StopPoint

from ...download_utils import download_if_modified
from ...gtfs_feeds import FEEDS
from ...gtfs_utils import (
    MODES,
    do_route_links,
    finish_gtfs_import,
    get_arrival_and_departure,
    get_calendars,
    get_first_and_last_stop_times,
    get_str,
    save_trips,
    set_stop_time_defaults,
    set_trip_times,
)
from ...models import Route, StopTime, Trip

logger = logging.getLogger(__name__)


# all-caps town name, then a stop name with some lowercase letters
TOWN_IN_STOP_NAME = re.compile(r"([A-ZÀ-Þ'-]+(?: [A-ZÀ-Þ'-]+)*) (.*[a-zß-ÿ].*)")


def get_platform_code(stop) -> str:
    # may have been parsed as a number
    value = getattr(stop, "platform_code", None)
    if isinstance(value, float):
        return "" if math.isnan(value) else f"{value:g}"
    return "" if value is None else str(value)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "feed", choices=[name for name, feed in FEEDS.items() if feed.static_url]
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Import data even if the GTFS feeds haven't changed",
        )

    def handle(self, feed, force, *args, **options):
        path = settings.DATA_DIR / Path(f"{feed}_gtfs.zip")
        feed_config = FEEDS[feed]
        prefix = feed_config.stop_id_prefix
        timezone = feed_config.timezone

        source, _ = DataSource.objects.get_or_create(name=feed_config.source_name)
        source.url = feed_config.static_url

        modified, last_modified = download_if_modified(path, source)

        if not modified and not force:
            return  # no new data to import
        source.datetime = last_modified

        logger.info(f"{source} {last_modified}")

        feed = gtfs_kit.read_feed(path, dist_units="km")
        if "location_type" in feed.stops:
            # not stations, entrances etc
            feed.stops = feed.stops[feed.stops.location_type.fillna(0) == 0]

        set_stop_time_defaults(feed)

        agency = next(feed.agency.itertuples())
        operator = Operator.objects.get_or_create(
            noc=feed_config.noc,
            defaults={
                "name": agency.agency_name,
                "url": get_str(agency, "agency_url"),
                "timezone": timezone,
            },
        )[0]

        existing_services = {
            service.line_name: service for service in operator.service_set.all()
        }
        existing_routes = {route.code: route for route in source.route_set.all()}
        routes = []

        # upsert stops
        stops = {}
        new_stops = [
            StopPoint(
                atco_code=f"{prefix}{stop.stop_id}",
                common_name=stop.stop_name,
                indicator=get_platform_code(stop),
                active=True,
                source=source,
                latlong=f"POINT({stop.stop_lon} {stop.stop_lat})",
                timezone=timezone,
                bearing=stop.stop_name[0]
                if stop.stop_name[:3].upper() in ("NB ", "EB ", "SB ", "WB ")
                else "",
            )
            for stop in feed.stops.itertuples()
        ]
        for stop in new_stops:
            if feed_config.town_in_stop_names and (
                match := TOWN_IN_STOP_NAME.fullmatch(stop.common_name)
            ):
                stop.town, stop.common_name = match.groups()
            stop.common_name = stop.common_name[:48]
        StopPoint.objects.bulk_create(
            new_stops,
            update_conflicts=True,
            unique_fields=["atco_code"],
            update_fields=["common_name", "indicator", "latlong", "bearing", "town"],
        )
        for stop in new_stops:
            stops[stop.atco_code.removeprefix(prefix)] = stop

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
            route.timezone = timezone
            route.source = source
            route.service = service
            route.line_name = row.route_short_name
            service.source = source
            service.description = route.description = get_str(
                row, "route_long_name"
            ).removeprefix(f"{route.line_name} ")
            service.current = True

            if route_color := get_str(row, "route_color"):
                bg = f"#{route_color}"
                fg = f"#{get_str(row, 'route_text_color', '000000')}"
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
            headsign = get_str(row, "trip_headsign").removeprefix(f"{route.line_name} ")
            trip = Trip(
                route=route,
                calendar=calendars[row.service_id],
                inbound=row.direction_id == 1,
                ticket_machine_code=row.trip_id,
                vehicle_journey_code=row.trip_id,
                block=get_str(row, "block_id", None),
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

        for trip_id in set_trip_times(trips, first_stop_times, last_stop_times, stops):
            logger.warning(f"trip {trip_id} has no stop times")

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        stop_codes = {stop_id: stop.atco_code for stop_id, stop in stops.items()}
        do_route_links(feed, source, existing_routes, feed_stops, stop_codes)

        with transaction.atomic():
            trip_objs = [trip for trip in trips.values() if trip is not None]
            existing_trips = save_trips(
                trip_objs,
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

            finish_gtfs_import(source, operator, routes, trip_objs)

            source.save(update_fields=["url", "datetime"])
