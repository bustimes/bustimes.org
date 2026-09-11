import logging
from pathlib import Path

import gtfs_kit
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Min, OuterRef, Subquery

from busstops.models import DataSource, Operator, Service, StopPoint

from ...gtfs_utils import (
    MODES,
    copy_stop_times,
    do_route_links,
    get_calendars,
    get_first_and_last_stop_times,
    get_str,
    save_trips,
    set_trip_times,
)
from ...models import Route, StopTime, Trip

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    for experimental purposes.

    1. download GTFS timetable from BODS

    2. (optional) use the AMAZING gtfstidy to make the feed less massive:

        ~/go/bin/gtfstidy --min-shapes --minimize-stoptimes --minimize-services --show-warnings --keep-additional-fields itm_all_gtfs.zip

    3.

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
                noc=get_str(o, "agency_noc", default=o.agency_id),
                slug=get_str(o, "agency_noc", default=o.agency_id),
                name=o.agency_name,
                url=get_str(o, "agency_url"),
                timezone=o.agency_timezone,
                phone=get_str(o, "agency_phone"),
                email=get_str(o, "agency_email"),
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
                naptan_code=get_str(stop, "stop_code", default=None),
                common_name=stop.stop_name[:48],
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
        route_operators = {}

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

            route_operators[row.route_id] = operator

        logger.info("trips")

        # reuse existing trip ids where possible, so foreign keys elsewhere
        # (e.g. vehicle journeys) don't get orphaned by every reimport
        existing_trip_ids = dict(
            Trip.objects.filter(route__source=source)
            .order_by("id")
            .values_list("ticket_machine_code", "id")
        )

        trips = {}

        # line as in line in a spreadsheet, not as in the Elizabeth Line
        for line in feed.trips.itertuples():
            trip = Trip(
                route=existing_routes[line.route_id],
                calendar=calendars[line.service_id],
                inbound=line.direction_id == 1,
                headsign=get_str(line, "trip_headsign", default=None),
                ticket_machine_code=line.trip_id,
                block=get_str(line, "block_id", default=None),
                vehicle_journey_code=get_str(line, "trip_short_name", default=None),
                operator=route_operators[line.route_id],
            )
            if line.trip_id in existing_trip_ids:
                trip.id = existing_trip_ids[line.trip_id]
            trips[line.trip_id] = trip

        _, first_stop_times, last_stop_times = get_first_and_last_stop_times(
            feed.stop_times
        )

        for trip_id in set_trip_times(trips, first_stop_times, last_stop_times, stops):
            logger.warning(f"trip {trip_id} has no stop times")

        trip_objs = [trip for trip in trips.values() if trip is not None]
        existing_trips = save_trips(
            trip_objs,
            fields=[
                "route",
                "calendar",
                "inbound",
                "headsign",
                "ticket_machine_code",
                "block",
                "vehicle_journey_code",
                "operator",
                "start",
                "end",
                "destination",
            ],
        )
        StopTime.objects.filter(trip__in=existing_trips).delete()

        copy_stop_times(feed, trips, last_stop_times)

        kept_trip_ids = {trip.pk for trip in trips.values() if trip}
        del trips

        # remove trips that used to belong to these routes but weren't in this import
        Trip.objects.filter(route__in=routes).exclude(id__in=kept_trip_ids).delete()

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        do_route_links(feed, source, existing_routes, feed_stops)

        with transaction.atomic():
            for service in source.service_set.filter(current=True):
                service.do_stop_usages()
                service.update_search_vector()

            logger.info(
                source.route_set.exclude(id__in=[route.id for route in routes]).delete()
            )

            source.route_set.update(
                start_date=Subquery(
                    Route.objects.filter(pk=OuterRef("pk"))
                    .annotate(min_date=Min("trip__calendar__start_date"))
                    .values("min_date")[:1]
                )
            )
