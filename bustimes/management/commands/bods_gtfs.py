import logging
from pathlib import Path

import gtfs_kit
import pandas as pd
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Min, OuterRef, Subquery
from django.utils.dateparse import parse_duration

from busstops.models import DataSource, Operator, Service, StopPoint

from ...gtfs_utils import MODES, do_route_links, get_calendars
from ...models import Route, Trip

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    for experimental purposes.

    first, use the AMAZING gtfstidy to make the feed less massive:

        ~/go/bin/gtfstidy --min-shapes --minimize-stoptimes --minimize-services --show-warnings --keep-additional-fields itm_all_gtfs.zip

    then:

        ./manage.py bods_gtfs gtfs_out
    """

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("path", type=str)

    def handle(self, path, *args, **options):
        path = Path(path)

        source, _ = DataSource.objects.get_or_create(name="BODS GTFS")

        logger.info("reading feed")
        feed = gtfs_kit.read_feed(path, dist_units="km")

        logger.info("operators")
        # upsert agencies (operators)
        operators = {
            o.agency_id: Operator(
                noc=o.agency_noc or o.agency_id,
                slug=o.agency_noc or o.agency_id,
                name=o.agency_name,
                url=o.agency_url if pd.notna(o.agency_url) else "",
                timezone=o.agency_timezone,
                phone=o.agency_phone if pd.notna(o.agency_phone) else "",
            )
            for o in feed.agency.itertuples()
        }
        Operator.objects.bulk_create(
            operators.values(),
            update_conflicts=True,
            unique_fields=["noc"],
            update_fields=["name", "phone"],
        )

        logger.info("stops")
        # upsert stops
        stops = {
            stop.stop_id: StopPoint(
                atco_code=stop.stop_id,
                naptan_code=stop.stop_code if pd.notna(stop.stop_code) else None,
                common_name=stop.stop_name,
                active=True,
                source=source,
                latlong=f"POINT({stop.stop_lon} {stop.stop_lat})",
            )
            for stop in feed.stops.itertuples()
        }
        StopPoint.objects.bulk_create(
            stops.values(),
            update_conflicts=True,
            unique_fields=["atco_code"],
            update_fields=["common_name", "naptan_code", "latlong", "bearing"],
        )

        calendars = get_calendars(feed, source)

        logger.info("routes")

        existing_routes = {
            route.code: route for route in source.route_set.select_related("service")
        }
        routes = []

        for row in feed.get_routes(as_gdf=True).itertuples():
            operator = operators[row.agency_id]

            if row.route_id in existing_routes:
                route = existing_routes[row.route_id]
                service = route.service
            else:
                route = Route(code=row.route_id)
                service = Service()
                service.slug = f"{operator.noc}-{row.route_short_name}-{row.route_id}"

            route.source = source
            route.service = service
            route.line_name = row.route_short_name
            service.source = source
            service.current = True
            service.line_name = route.line_name

            if not service.line_name:
                print(row)
            assert service.line_name

            try:
                service.mode = MODES[row.route_type]
            except KeyError:
                logger.exception("unknown route type in %s", row)
            if row.geometry:
                service.geometry = row.geometry.wkt

            service.save()
            service.operator.add(operator)
            route.save()

            routes.append(route)

            existing_routes[route.code] = route  # deals with duplicate rows

        logger.info("trips")

        trips = {}

        # line as in line in a spreadsheet, not as in the Elizabeth Line
        for line in feed.trips.itertuples():
            trips[line.trip_id] = Trip(
                route=existing_routes[line.route_id],
                calendar=calendars[line.service_id],
                inbound=line.direction_id == 1,
                headsign=line.trip_headsign,
                ticket_machine_code=line.trip_id,
                block=""
                if pd.isna(block_id := getattr(line, "block_id", ""))
                else block_id,
                # operator=self.route_operators[line.route_id],
            )

        # use stop_times.txt to calculate trips' start times, end times and destinations:

        trip = None
        previous_line = None

        for line in feed.stop_times.itertuples():
            if not previous_line or previous_line.trip_id != line.trip_id:
                if trip:
                    trip.destination = stops.get(previous_line.stop_id)
                    trip.end = previous_line.arrival_time

                trip = trips[line.trip_id]
                trip.start = line.departure_time

            previous_line = line

        if previous_line:
            # last trip:
            trip.destination = stops.get(line.stop_id)
            trip.end = line.arrival_time

        for trip_id, trip in trips.items():
            if pd.isna(trip.start) or pd.isna(trip.end):
                logger.warning(f"trip {trip_id} has no stop times")
                trips[trip_id] = None

        Trip.objects.bulk_create(
            [trip for trip in trips.values() if isinstance(trip, Trip)],
            batch_size=1000,
        )
        logger.info("fillna")
        feed.stop_times = feed.stop_times.fillna(
            {"timepoint": 1, "pickup_type": 0, "drop_off_type": 0}
        )

        logger.info("stop times")
        with (
            connection.cursor() as cursor,
            cursor.copy(
                "COPY bustimes_stoptime (stop_id, arrival, departure, sequence, trip_id, timing_point, pick_up, set_down) FROM STDIN"
            ) as copy,
        ):
            for line in feed.stop_times.itertuples():
                if trips[line.trip_id] is None:
                    continue

                timing_point = bool(getattr(line, "timepoint", 1))

                pick_up = None
                match line.pickup_type:
                    case 0:  # Regularly scheduled pickup
                        pick_up = True
                    case 1:  # "No pickup available"
                        pick_up = False

                set_down = None
                match line.drop_off_type:
                    case 0:  # Regularly scheduled drop off
                        set_down = True
                    case 1:  # "No drop off available"
                        set_down = False

                departure = int(parse_duration(line.departure_time).total_seconds())
                arrival = None
                if line.arrival_time != departure:
                    arrival = int(parse_duration(line.arrival_time).total_seconds())

                copy.write_row(
                    (
                        line.stop_id,
                        arrival,
                        departure,
                        line.stop_sequence,
                        trips[line.trip_id].pk,
                        timing_point,
                        pick_up,
                        set_down,
                    )
                )

        del trips

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        stop_codes = {stop_id: stop.atco_code for stop_id, stop in stops.items()}
        do_route_links(feed, source, existing_routes, feed_stops, stop_codes)

        with transaction.atomic():
            for service in source.service_set.filter(current=True):
                service.do_stop_usages()
                service.update_search_vector()

            logger.info(
                source.route_set.exclude(id__in=[route.id for route in routes]).delete()
            )
            # logger.info(
            #     operator.trip_set.exclude(
            #         id__in=[trip.id for trip in trips.values()]
            #     ).delete()
            # )
            # logger.info(
            #     operator.service_set.filter(current=True, route__isnull=True).update(
            #         current=False
            #     )
            # )

            source.route_set.update(
                start_date=Subquery(
                    Route.objects.filter(pk=OuterRef("pk"))
                    .annotate(min_date=Min("trip__calendar__start_date"))
                    .values("min_date")[:1]
                )
            )

            # source.save(update_fields=["url", "datetime"])
