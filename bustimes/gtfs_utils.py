from enum import IntEnum
from itertools import pairwise

import gtfs_kit
import pandas as pd
import shapely.ops as so

from .models import Calendar, CalendarDate, RouteLink


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
