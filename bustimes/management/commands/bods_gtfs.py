import logging
from pathlib import Path

import gtfs_kit
import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Min, OuterRef, Subquery

from busstops.models import DataSource, Operator, Service, StopPoint

from ...gtfs_utils import MODES, do_route_links, get_calendars
from ...models import Route, StopTime, Trip

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    for experimental purposes

    `./manage.py bod_gtfs
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
                url=o.agency_url,
                timezone=o.agency_timezone,
                phone=o.agency_phone,
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

        existing_trips = {
            trip.vehicle_journey_code: trip for trip in operator.trip_set.all()
        }
        trips = {}
        for row in feed.trips.itertuples():
            trip = Trip(
                route=existing_routes[row.route_id],
                calendar=calendars[row.service_id],
                inbound=row.direction_id == 1,
                vehicle_journey_code=row.trip_id,
                headsign=row.trip_headsign,
            )
            if trip.vehicle_journey_code in existing_trips:
                # reuse existing trip id
                trip.id = existing_trips[trip.vehicle_journey_code].id
            trips[trip.vehicle_journey_code] = trip
        del existing_trips

        # logger.info("fillna")
        # feed.stop_times = feed.stop_times.fillna(
        #     {"timepoint": 1, "pickup_type": 0, "drop_off_type": 0}
        # )

        stop_times = []
        for row in feed.stop_times.itertuples():
            trip = trips[row.trip_id]

            arrival_time = row.arrival_time
            departure_time = row.departure_time

            if arrival_time[0] == " ":
                arrival_time = "0" + arrival_time[1:]
            if departure_time[0] == " ":
                departure_time = "0" + departure_time[1:]

            if not trip.start:
                trip.start = departure_time
            trip.end = arrival_time

            stop_time = StopTime(
                arrival=arrival_time,
                departure=departure_time,
                sequence=row.stop_sequence,
                trip=trip,
                timing_point=bool(row.timepoint),
                pick_up=(row.pickup_type != 1),
                set_down=(row.drop_off_type != 1),
            )

            stop_time.stop = trip.destination = stops[row.stop_id]

            stop_times.append(stop_time)

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        stop_codes = {stop_id: stop.atco_code for stop_id, stop in stops.items()}
        do_route_links(feed, source, existing_routes, feed_stops, stop_codes)

        with transaction.atomic():
            existing_trips = [trip for trip in trips.values() if trip.id]
            Trip.objects.bulk_create([trip for trip in trips.values() if not trip.id])
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
