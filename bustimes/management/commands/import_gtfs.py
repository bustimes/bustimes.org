import logging
from pathlib import Path
from zipfile import BadZipFile

import gtfs_kit
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.management.base import BaseCommand
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import Now
from shapely.errors import EmptyPartError

from busstops.models import AdminArea, DataSource, Operator, Region, Service, StopPoint

from ...download_utils import download_if_modified
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
from ...utils import log_time_taken

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("collections", nargs="*", type=str)

    def handle_operator(self, line):
        agency_id = line.agency_id
        agency_id = f"ie-{agency_id}"

        name = line.agency_name

        operator = Operator.objects.filter(
            Q(name__iexact=name) | Q(noc=agency_id)
        ).first()

        if not operator:
            operator = Operator(name=name, noc=agency_id, url=line.agency_url)
            operator.save()
        elif operator.url != line.agency_url:
            operator.url = line.agency_url
            operator.save(update_fields=["url"])

        return operator

    def do_stops(self, feed: gtfs_kit.feed.Feed) -> dict[str, StopPoint]:
        stops = {}
        admin_areas = {}
        for _, line in feed.stops.iterrows():
            stop_id = line.stop_id
            stop = StopPoint(
                atco_code=stop_id,
                common_name=line.stop_name,
                latlong=GEOSGeometry(f"POINT({line.stop_lon} {line.stop_lat})"),
                locality_centre=False,
                active=True,
                source=self.source,
            )
            if ", stop" in stop.common_name and stop.common_name.count(", ") == 1:
                stop.common_name, stop.indicator = stop.common_name.split(", ")
            stop.common_name = stop.common_name[:48]
            stops[stop_id] = stop
        existing_stops = StopPoint.objects.only(
            "atco_code", "common_name", "latlong", "source_id"
        ).in_bulk(stops)

        stops_to_create = [
            stop for stop in stops.values() if stop.atco_code not in existing_stops
        ]
        stops_to_update = [
            stop
            for stop in stops.values()
            if stop.atco_code in existing_stops
            and existing_stops[stop.atco_code].source_id in (self.source.id, None)
            and (
                existing_stops[stop.atco_code].latlong != stop.latlong
                or existing_stops[stop.atco_code].common_name != stop.common_name
            )
        ]
        StopPoint.objects.bulk_update(
            stops_to_update, ["common_name", "latlong", "indicator", "source"]
        )

        for stop in stops_to_create:
            admin_area_id = stop.atco_code[:3]
            if admin_area_id not in admin_areas:
                admin_areas[admin_area_id] = AdminArea.objects.filter(
                    id=admin_area_id
                ).exists()
            if admin_areas[admin_area_id]:
                stop.admin_area_id = admin_area_id

        StopPoint.objects.bulk_create(stops_to_create, batch_size=1000)
        return StopPoint.objects.only("atco_code", "latlong").in_bulk(stops)

    def handle_route(self, line):
        line_name = line.route_short_name if type(line.route_short_name) is str else ""
        description = line.route_long_name if type(line.route_long_name) is str else ""
        if not line_name and " " not in description:
            line_name = description
            if len(line_name) < 5:
                description = ""

        operator = self.operators.get(line.agency_id)
        services = Service.objects.filter(operator=operator)

        q = Exists(
            Route.objects.filter(code=line.route_id, service=OuterRef("id"))
        ) | Q(service_code=line.route_id)

        if line_name and line_name not in ("rail", "InterCity"):
            q |= Q(line_name__iexact=line_name)
        elif description:
            q |= Q(description=description)

        service = services.filter(q).order_by("id").first()
        if not service:
            service = Service(source=self.source)

        service.service_code = line.route_id
        service.line_name = line_name
        service.description = description
        if line.route_type in MODES:
            service.mode = MODES[line.route_type]
        else:
            logger.warning("unknown route type %s", line)
        service.current = True
        service.source = self.source
        service.save()

        if operator:
            if service.id in self.services:
                service.operator.add(operator)
            else:
                service.operator.set([operator])
        self.services[service.id] = service

        route, _ = Route.objects.update_or_create(
            {
                "line_name": service.line_name,
                "description": service.description,
                "service": service,
            },
            source=self.source,
            code=line.route_id,
        )
        self.routes[line.route_id] = route
        self.route_operators[line.route_id] = operator

    def handle_zipfile(self, path):
        feed = gtfs_kit.read_feed(path, dist_units="km")

        self.operators = {}
        self.routes = {}
        self.route_operators = {}
        self.services = {}

        for agency in feed.agency.itertuples():
            self.operators[agency.agency_id] = self.handle_operator(agency)

        for route in feed.routes.itertuples():
            self.handle_route(route)

        try:
            for route in feed.get_routes(as_gdf=True).itertuples():
                self.routes[route.route_id].service.geometry = route.geometry.wkt
                if route.geometry:
                    self.routes[route.route_id].service.save(update_fields=["geometry"])
        except (AttributeError, EmptyPartError, ValueError):
            pass

        stops = self.do_stops(feed)

        calendars = get_calendars(feed, source=self.source)

        # reuse existing trip ids where possible, so foreign keys elsewhere
        # (e.g. vehicle journeys) don't get orphaned by every reimport
        existing_trip_ids = dict(
            Trip.objects.filter(route__source=self.source)
            .order_by("id")
            .values_list("ticket_machine_code", "id")
        )

        trips = {}

        # line as in line in a spreadsheet, not as in the Elizabeth Line
        for line in feed.trips.itertuples():
            route = self.routes[line.route_id]
            trip = Trip(
                route=route,
                calendar=calendars[line.service_id],
                inbound=line.direction_id == 1,
                headsign=get_str(line, "trip_headsign", default=None),
                ticket_machine_code=line.trip_id,
                block=get_str(line, "block_id", default=None),
                vehicle_journey_code=get_str(line, "trip_short_name", default=None),
                operator=self.route_operators[line.route_id],
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
        Trip.objects.filter(route__in=self.routes.values()).exclude(
            id__in=kept_trip_ids
        ).delete()

        services = Service.objects.filter(id__in=self.services.keys())

        for service in services:
            service.do_stop_usages()

            region = (
                Region.objects.filter(adminarea__stoppoint__service=service)
                .annotate(Count("adminarea__stoppoint__service"))
                .order_by("-adminarea__stoppoint__service__count")
                .first()
            )
            if region and region != service.region:
                service.save(update_fields=["region"])

            service.update_search_vector()

        services.update(modified_at=Now())

        self.source.save(update_fields=["datetime"])

        for operator in self.operators.values():
            operator.region = (
                Region.objects.filter(adminarea__stoppoint__service__operator=operator)
                .annotate(Count("adminarea__stoppoint__service__operator"))
                .order_by("-adminarea__stoppoint__service__operator__count")
                .first()
            )
            if operator.region_id:
                operator.save(update_fields=["region"])

        old_routes = self.source.route_set.exclude(
            id__in=(route.id for route in self.routes.values())
        )
        logger.info(old_routes.update(service=None))

        current_services = self.source.service_set.filter(current=True)
        logger.info(
            current_services.exclude(route__in=self.routes.values()).update(
                current=False
            )
        )

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        do_route_links(feed, self.source, self.routes, feed_stops)

    def handle(self, *args, **options):
        collections = DataSource.objects.filter(
            url__startswith="https://www.transportforireland.ie/transitData/Data/GTFS_"
        )

        if options["collections"]:
            collections = collections.filter(name__in=options["collections"])

        for source in collections:
            path: Path = settings.DATA_DIR / Path(source.url).name

            modified, last_modified = download_if_modified(path, source)
            if modified or last_modified != source.datetime or options["collections"]:
                logger.info(f"{source} {last_modified}")
                if last_modified:
                    source.datetime = last_modified
                self.source = source
                try:
                    with log_time_taken(logger):
                        self.handle_zipfile(path)
                except (OSError, BadZipFile):
                    logger.exception("error handling zipfile")

            # sleep(2)
