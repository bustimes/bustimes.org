import logging
import tempfile
from enum import IntEnum
from itertools import pairwise

import gtfs_kit
import pandas as pd
import shapely.ops as so
from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction
from django.db.models import Min, OuterRef, Subquery

from busstops.models import DataSource, Operator, Service, StopPoint

from .models import Calendar, CalendarDate, Route, RouteLink, StopTime, Trip

logger = logging.getLogger(__name__)


class RouteType(IntEnum):
    tram = 0
    rail = 2
    bus = 3
    ferry = 4
    cable_car = 6
    coach = 200
    air = 1100


MODES = {
    RouteType.tram: "tram",
    RouteType.rail: "rail",
    RouteType.bus: "bus",
    RouteType.ferry: "ferry",
    RouteType.cable_car: "cable car",
    RouteType.coach: "coach",
    RouteType.air: "air",
}


def get_calendars(feed, source) -> dict:
    if feed.calendar is not None:
        calendars = {
            row.service_id: Calendar(
                mon=row.monday,
                tue=row.tuesday,
                wed=row.wednesday,
                thu=row.thursday,
                fri=row.friday,
                sat=row.saturday,
                sun=row.sunday,
                start_date=row.start_date,
                end_date=row.end_date,
                source=source,
            )
            for row in feed.calendar.itertuples()
        }
    else:
        calendars = {}

    calendar_dates = []

    if feed.calendar_dates is not None:
        for row in feed.calendar_dates.itertuples():
            operation = row.exception_type == 1
            # 1: operates, 2: does not operate

            if (calendar := calendars.get(row.service_id)) is None:
                calendar = Calendar(
                    start_date=row.date,  # dummy date
                )
                calendars[row.service_id] = calendar
            calendar_dates.append(
                CalendarDate(
                    calendar=calendar,
                    start_date=row.date,
                    end_date=row.date,
                    operation=operation,
                    special=operation,  # additional date of operation
                )
            )

    Calendar.objects.bulk_create(calendars.values())
    CalendarDate.objects.bulk_create(calendar_dates)

    return calendars


def get_first_and_last_stop_times(
    stop_times: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sorted_stop_times = stop_times.sort_values(["trip_id", "stop_sequence"])
    first_stop_times = sorted_stop_times.drop_duplicates(
        "trip_id", keep="first"
    ).set_index("trip_id")
    last_stop_times = sorted_stop_times.drop_duplicates(
        "trip_id", keep="last"
    ).set_index("trip_id")
    return sorted_stop_times, first_stop_times, last_stop_times


def get_arrival_and_departure(arrival, departure, is_last: bool):
    """Mirror import_transxchange.py: only store an arrival time if it
    differs from the departure time, and don't store a departure time for a
    trip's last stop, since it doesn't depart from there.
    """
    if arrival == departure:
        arrival = None
    if is_last and arrival is None:
        arrival = departure
        departure = None
    return arrival, departure


def set_trip_times(
    trips: dict,
    first_stop_times: pd.DataFrame,
    last_stop_times: pd.DataFrame,
    stops: dict,
) -> list:
    """For each trip, work out its start time (the first stop's departure
    time), end time (the last stop's arrival time) and destination, using
    stop_times.txt. Trips with no stop times are set to None, and their ids
    are returned so callers can log a warning with their own logger.
    """
    missing_trip_ids = []
    for trip_id, trip in trips.items():
        start = first_stop_times.departure_time.get(trip_id)
        end = last_stop_times.arrival_time.get(trip_id)
        if pd.isna(start) or pd.isna(end):
            trips[trip_id] = None
            missing_trip_ids.append(trip_id)
        else:
            trip.start = start
            trip.end = end
            trip.destination = stops.get(last_stop_times.stop_id.get(trip_id))
    return missing_trip_ids


def do_route_links(
    feed: gtfs_kit.feed.Feed,
    source,
    routes: dict,
    stops: dict,
    stop_codes: dict | None = None,
):
    try:
        trips = feed.get_trips(as_gdf=True).drop_duplicates("shape_id")
    except ValueError:
        return

    existing_route_links = {
        (rl.service_id, rl.from_stop_id, rl.to_stop_id): rl
        for rl in RouteLink.objects.filter(service__source=source)
    }
    route_links = {}

    stop_times_by_trip = dict(tuple(feed.stop_times.groupby("trip_id", sort=False)))

    for trip in trips.itertuples():
        if trip.geometry is None:
            continue

        service = routes[trip.route_id].service_id

        trip_stop_times = stop_times_by_trip.get(trip.trip_id)
        if trip_stop_times is None:
            continue

        start_dist = None

        for a, b in pairwise(trip_stop_times.itertuples()):
            from_stop_id = (
                stop_codes.get(a.stop_id, a.stop_id) if stop_codes else a.stop_id
            )
            to_stop_id = (
                stop_codes.get(b.stop_id, b.stop_id) if stop_codes else b.stop_id
            )
            key = (service, from_stop_id, to_stop_id)

            if key in route_links:
                start_dist = None
                continue

            stop_a = stops[a.stop_id]
            point_a = so.Point(stop_a.stop_lon, stop_a.stop_lat)
            if not start_dist:
                start_dist = trip.geometry.project(point_a)
            stop_b = stops[b.stop_id]
            point_b = so.Point(stop_b.stop_lon, stop_b.stop_lat)
            end_dist = trip.geometry.project(point_b)

            # skip if either stop is too far from the route geometry (~1km at UK latitudes)
            projected_a = trip.geometry.interpolate(start_dist)
            projected_b = trip.geometry.interpolate(end_dist)
            if (
                point_a.distance(projected_a) > 0.01
                or point_b.distance(projected_b) > 0.01
            ):
                start_dist = None
                continue

            geom = so.substring(trip.geometry, start_dist, end_dist)
            if type(geom) is so.LineString and len(geom.coords) > 2:
                if key in existing_route_links:
                    rl = existing_route_links[key]
                else:
                    rl = RouteLink(
                        service_id=key[0],
                        from_stop_id=key[1],
                        to_stop_id=key[2],
                    )
                rl.geometry = geom.wkt
                route_links[key] = rl

            start_dist = end_dist

    RouteLink.objects.bulk_update(
        [rl for rl in route_links.values() if rl.id], fields=["geometry"]
    )
    RouteLink.objects.bulk_create([rl for rl in route_links.values() if not rl.id])


def do_stops(feed: gtfs_kit.feed.Feed, source) -> dict:
    stops = {
        row.stop_id: StopPoint(
            atco_code=row.stop_id,
            common_name=row.stop_name[:48],
            latlong=GEOSGeometry(f"POINT({row.stop_lon} {row.stop_lat})"),
            active=True,
            source=source,
        )
        for row in feed.stops.itertuples()
    }

    existing_stops = StopPoint.objects.in_bulk(stops)
    stops_to_create = [
        stop for stop_id, stop in stops.items() if stop_id not in existing_stops
    ]
    StopPoint.objects.bulk_create(stops_to_create)

    return StopPoint.objects.in_bulk(stops)


def get_operators(feed: gtfs_kit.feed.Feed) -> dict:
    operators = {}
    for row in feed.agency.itertuples():
        operator, created = Operator.objects.get_or_create(
            noc=row.agency_id,
            defaults={"name": row.agency_name, "url": row.agency_url},
        )
        if not created and operator.name != row.agency_name:
            operator.name = row.agency_name
            operator.save(update_fields=["name"])
        operators[row.agency_id] = operator
    return operators


def save_trips(trip_objs: list, fields: list, batch_size: int = 1000) -> list:
    """Bulk create new Trips (those with no id) and bulk update existing ones
    (reused from a previous import, so foreign keys elsewhere don't get
    orphaned by every reimport). Returns the existing ones, so callers can
    clear out their now-stale StopTimes before recreating them.
    """
    new_trips = [trip for trip in trip_objs if trip.id is None]
    existing_trips = [trip for trip in trip_objs if trip.id is not None]

    Trip.objects.bulk_create(new_trips, batch_size=batch_size)
    Trip.objects.bulk_update(existing_trips, fields=fields, batch_size=batch_size)

    return existing_trips


def set_route_start_dates(source) -> None:
    source.route_set.update(
        start_date=Subquery(
            Route.objects.filter(pk=OuterRef("pk"))
            .annotate(min_date=Min("trip__calendar__start_date"))
            .values("min_date")[:1]
        )
    )


def finish_gtfs_import(
    source, operator, routes: list, trip_objs: list, update_geometry: bool = False
) -> None:
    """Common cleanup once a GTFS import's routes, trips and stop times have
    been saved: refresh affected services' stop usages and search vectors,
    remove routes/trips that weren't in this import, mark the operator's
    other services as not current, and work out each route's start date.
    """
    for service in source.service_set.filter(current=True):
        service.do_stop_usages()
        service.update_search_vector()
        if update_geometry:
            service.update_geometry()

    logger.info(
        source.route_set.exclude(id__in=[route.id for route in routes]).delete()
    )
    logger.info(
        operator.trip_set.exclude(id__in=[trip.id for trip in trip_objs]).delete()
    )
    logger.info(
        operator.service_set.filter(current=True, route__isnull=True).update(
            current=False
        )
    )

    set_route_start_dates(source)


def handle_gtfs_upload(source_name, file):
    source, _ = DataSource.objects.get_or_create(name=source_name)

    with (
        tempfile.NamedTemporaryFile(suffix=".zip") as temp_file,
        transaction.atomic(),
    ):
        for chunk in file.chunks():
            temp_file.write(chunk)
        temp_file.flush()

        feed = gtfs_kit.read_feed(temp_file.name, dist_units="km")

        calendars = get_calendars(feed, source)
        operators = get_operators(feed)
        stops = do_stops(feed, source)

        existing_services = {
            service.line_name: service for service in source.service_set.all()
        }

        existing_routes = {route.code: route for route in source.route_set.all()}

        routes = {}
        route_operators = {}

        for row in feed.routes.itertuples():
            line_name = row.route_short_name

            if line_name in existing_services:
                service = existing_services[line_name]
            else:
                service = Service(line_name=line_name, source=source)

            if row.route_id in existing_routes:
                route = existing_routes[row.route_id]
            else:
                route = Route(code=row.route_id, source=source)
            route.service = service
            route.line_name = line_name
            service.description = route.description = row.route_long_name
            service.current = True
            service.mode = MODES[row.route_type]

            service.save()
            route.save()

            operator = operators.get(row.agency_id)
            if operator:
                service.operator.set([operator])

            routes[row.route_id] = route
            route_operators[row.route_id] = operator

        # reuse existing trip ids where possible, so foreign keys elsewhere
        # (e.g. vehicle journeys) don't get orphaned by every re-upload
        existing_trip_ids = dict(
            Trip.objects.filter(route__source=source)
            .order_by("id")
            .values_list("ticket_machine_code", "id")
        )

        trips = {}

        for row in feed.trips.itertuples():
            route = routes[row.route_id]
            trip = Trip(
                route=route,
                calendar=calendars[row.service_id],
                inbound=row.direction_id == 1,
                headsign=row.trip_headsign,
                ticket_machine_code=row.trip_id,
                operator=route_operators[row.route_id],
            )
            if row.trip_id in existing_trip_ids:
                trip.id = existing_trip_ids[row.trip_id]
            trips[row.trip_id] = trip

        # use stop_times.txt to work out trips' start times, end times and destinations
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
                "operator",
                "start",
                "end",
                "destination",
            ],
        )
        # clear out old stop times for reused trips, they'll be recreated below
        StopTime.objects.filter(trip__in=existing_trips).delete()

        stop_times = []

        for row in feed.stop_times.itertuples():
            trip = trips[row.trip_id]
            if trip is None:
                continue

            pick_up = None
            match row.pickup_type:
                case 0:  # Regularly scheduled pickup
                    pick_up = True
                case 1:  # "No pickup available"
                    pick_up = False

            set_down = None
            match row.drop_off_type:
                case 0:  # Regularly scheduled drop off
                    set_down = True
                case 1:  # "No drop off available"
                    set_down = False

            is_last = row.stop_sequence == last_stop_times.stop_sequence.get(
                row.trip_id
            )
            arrival_time, departure_time = get_arrival_and_departure(
                row.arrival_time, row.departure_time, is_last
            )

            stop_times.append(
                StopTime(
                    trip=trip,
                    stop=stops.get(row.stop_id),
                    arrival=arrival_time,
                    departure=departure_time,
                    sequence=row.stop_sequence,
                    timing_point=bool(getattr(row, "timepoint", 1)),
                    pick_up=pick_up,
                    set_down=set_down,
                )
            )

        StopTime.objects.bulk_create(stop_times, batch_size=1000)

        kept_trip_ids = {trip.pk for trip in trips.values() if trip}

        # remove trips that used to belong to these routes but weren't in this upload
        Trip.objects.filter(route__in=routes.values()).exclude(
            id__in=kept_trip_ids
        ).delete()

        for service in Service.objects.filter(
            id__in={route.service_id for route in routes.values()}
        ):
            service.do_stop_usages()
            service.update_search_vector()

        set_route_start_dates(source)

    return source
