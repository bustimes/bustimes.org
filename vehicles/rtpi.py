# "Real Time Passenger Information"-ish stuff - calculating delays etc

import datetime
import logging
import math
from collections import OrderedDict
from itertools import pairwise

import sentry_sdk
from django.contrib.gis.db.models.functions import Distance, LineLocatePoint
from django.contrib.gis.geos import LineString, Point

from bustimes.models import RouteLink, StopTime, Trip
from bustimes.utils import contiguous_stoptimes_only
from vehicles.utils import calculate_bearing

logger = logging.getLogger(__name__)

EARTH_RADIUS = 6371008.8  # mean radius - the sphere ST_DistanceSphere uses

# how close (in metres) a bus has to be to count as between a pair of stops.
# a route link follows the road, so a bus on it is only as far away as its GPS
# is wrong. a straight line between the stops can be much further from the road
# the bus is actually on - 10% of real route links stray more than 190 metres
NEARBY_ROUTE_LINK = 300
NEARBY_STRAIGHT_LINE = 600


def local_metres(point: Point, cos_latitude: float) -> tuple[float, float]:
    """project a WGS84 point to metres near a reference latitude.

    an equirectangular projection - only good over a few km, but that's all
    we measure, and unlike EPSG:3857 the units are actual metres, comparable
    with the ST_DistanceSphere distances that route links come back with
    """
    return (
        math.radians(point.x) * EARTH_RADIUS * cos_latitude,
        math.radians(point.y) * EARTH_RADIUS,
    )


def distance_to_segment(px, py, ax, ay, bx, by) -> float:
    """distance from a point to a line segment (all in the same projection)"""
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared:
        # how far along the segment the closest point is, from 0 to 1
        along = ((px - ax) * dx + (py - ay) * dy) / length_squared
        along = min(1, max(0, along))
        ax, ay = ax + along * dx, ay + along * dy
    return math.hypot(px - ax, py - ay)


def get_route_bearing(geometry: LineString, progress: float):
    """Get the bearing of the route at a given progress point (0-1)."""
    delta = 0.01
    p1 = geometry.interpolate_normalized(max(0, progress - delta))
    p2 = geometry.interpolate_normalized(min(1, progress + delta))
    return calculate_bearing(p1, p2)


# trip_id -> ((service modified_at, date), trip, stop_times)
STOP_TIMES_CACHE_MAXSIZE = 25_000

_stop_times_cache: OrderedDict[int, tuple[tuple, Trip, list]] = OrderedDict()

# service modified_at not known - reuse whatever is cached
UNKNOWN = object()


def _fetch_stop_times(trip_id, date):
    trip = Trip.objects.select_related("calendar", "route").get(pk=trip_id)
    trips = trip.get_parts(date)

    stop_times = (
        StopTime.objects.filter(trip__in=trips)
        .filter(stop__latlong__isnull=False)
        .select_related("stop")
        .only("arrival", "departure", "stop__latlong")
        .order_by("trip__start", "id")
    )

    if len(trips) > 1:
        stop_times = contiguous_stoptimes_only(stop_times, trip.id)
    else:
        stop_times = list(stop_times)

    return trip, stop_times


def get_stop_times(trip_id, date, modified_at=None):
    """Reuse stop times until the service is reimported (if modified_at is known)"""
    if modified_at is None:
        return _fetch_stop_times(trip_id, date)

    entry = _stop_times_cache.get(trip_id)
    if (
        entry
        and entry[0][1] == date
        and (modified_at is UNKNOWN or entry[0][0] == modified_at)
    ):
        _stop_times_cache.move_to_end(trip_id)
        return entry[1], entry[2]

    if modified_at is UNKNOWN:
        modified_at = None
    version = (modified_at, date)

    trip, stop_times = _fetch_stop_times(trip_id, date)

    if stop_times:
        _stop_times_cache[trip_id] = (version, trip, stop_times)
        _stop_times_cache.move_to_end(trip_id)
        if len(_stop_times_cache) > STOP_TIMES_CACHE_MAXSIZE:
            _stop_times_cache.popitem(last=False)

    return trip, stop_times


class Progress:
    def __init__(self, stop_times, prev_stop_time, next_stop_time, progress, distance):
        self.stop_times = stop_times
        self.sequence = self.stop_times.index(prev_stop_time)
        self.prev_stop_time = prev_stop_time
        self.next_stop_time = next_stop_time
        self.progress = round(progress, 3)
        self.distance = distance
        self.delay = None

    def to_json(self):
        return {
            "id": self.prev_stop_time.id,
            "sequence": self.sequence,
            "prev_stop": self.prev_stop_time.stop_id,
            "next_stop": self.next_stop_time.stop_id,
            "progress": self.progress,
        }


def get_delay(progress, date, when, tzinfo=None) -> int | None:
    prev = progress.prev_stop_time
    next_ = progress.next_stop_time

    # when the bus is scheduled to leave prev / arrive at next
    # (arrival/departure can be None when the two would be equal)
    prev_dep = prev.departure_datetime(date, tzinfo)
    if prev_dep is None:
        prev_dep = prev.arrival_datetime(date, tzinfo)
    next_arr = next_.arrival_datetime(date, tzinfo)
    if next_arr is None:
        next_arr = next_.departure_datetime(date, tzinfo)
    if prev_dep is None or next_arr is None:
        return None

    # if the bus is at prev stop and within its scheduled dwell, it's on time
    if progress.progress <= 0.1:
        prev_arr = prev.arrival_datetime(date, tzinfo)
        if prev_arr and prev_arr < prev_dep and prev_arr <= when <= prev_dep:
            return 0

    # likewise if the bus is at next stop and within its scheduled dwell
    elif progress.progress >= 0.9:
        next_dep = next_.departure_datetime(date, tzinfo)
        if next_dep and next_arr < next_dep and next_arr <= when <= next_dep:
            return 0

    expected_time = prev_dep + (next_arr - prev_dep) * progress.progress
    return int((when - expected_time).total_seconds())


def get_progress(
    item: dict,
    stop_time=None,
    stop_times=None,
    tzinfo=None,
    use_route_links=True,
    modified_at=None,
) -> Progress | None:
    when = datetime.datetime.fromisoformat(item["datetime"])
    date = datetime.date.fromisoformat(item["date"])

    point = Point(*item["coordinates"], srid=4326)
    cos_latitude = math.cos(math.radians(point.y))
    point_x, point_y = local_metres(point, cos_latitude)

    if stop_times is not None:
        stop_times = [st for st in stop_times if st.stop_id and st.stop.latlong]
    elif stop_time:
        stop_times = [
            st
            for st in stop_time.trip.stoptime_set.all()  # prefetched earlier
            if st.stop_id and st.stop.latlong
        ]
    elif "trip_id" in item:
        try:
            trip, stop_times = get_stop_times(item["trip_id"], date, modified_at)
        except Trip.DoesNotExist:
            return
        stop_times = list(stop_times)
        if tzinfo is None and trip.route:
            tzinfo = trip.route.timezone

    if not stop_times:
        return

    route_links = {}
    if use_route_links and "service_id" in item:
        for rl in RouteLink.objects.filter(
            service=item["service_id"],
            geometry__dwithin=(point, 0.01),  # ~1km in degrees
        ).annotate(
            progress=LineLocatePoint("geometry", point),
            distance=Distance("geometry", point),
        ):
            rl.distance = rl.distance.m  # convert to meters
            route_links[(rl.from_stop_id, rl.to_stop_id)] = rl

    with sentry_sdk.start_span(name="nearby pairs"):
        # each stop is in two pairs, and the same stops recur between calls
        coordinates = {}

        nearby_pairs = []
        for a, b in pairwise(stop_times):
            key = (a.stop_id, b.stop_id)
            if key in route_links:
                rl = route_links[key]
                if rl.distance < NEARBY_ROUTE_LINK:
                    nearby_pairs.append((a, b, rl))
                continue

            if (a_xy := coordinates.get(a.stop_id)) is None:
                a_xy = coordinates[a.stop_id] = local_metres(
                    a.stop.latlong, cos_latitude
                )
            if (b_xy := coordinates.get(b.stop_id)) is None:
                b_xy = coordinates[b.stop_id] = local_metres(
                    b.stop.latlong, cos_latitude
                )

            distance = distance_to_segment(point_x, point_y, *a_xy, *b_xy)  # in metres

            if distance < NEARBY_STRAIGHT_LINE:
                # only now is it worth building an actual geometry
                geometry = LineString([a.stop.latlong, b.stop.latlong], srid=4326)
                rl = RouteLink(from_stop=a.stop, to_stop=b.stop, geometry=geometry)
                rl.distance = distance
                rl.progress = geometry.project_normalized(point)
                nearby_pairs.append((a, b, rl))

        if not nearby_pairs:
            return

        nearby_pairs.sort(key=lambda p: p[2].distance)

    with sentry_sdk.start_span(name="closest pairs"):
        closest = nearby_pairs[0]
        next_closest = nearby_pairs[1] if len(nearby_pairs) > 1 else None

        if next_closest and item["heading"] is not None:
            vehicle_heading = int(item["heading"])

            route_bearing = get_route_bearing(closest[2].geometry, closest[2].progress)

            difference = (vehicle_heading - route_bearing + 180) % 360 - 180

            if not (abs(difference) < 90) and next_closest[2].distance < 100:
                # bus seems to be heading the wrong way - does the bus go both ways on this road?
                # try the next closest pair of stops:
                route_bearing = get_route_bearing(
                    next_closest[2].geometry, next_closest[2].progress
                )

                difference = (vehicle_heading - route_bearing + 180) % 360 - 180
                if abs(difference) < 90:
                    closest = next_closest

    with sentry_sdk.start_span(name="delay"):
        progress = Progress(
            stop_times, closest[0], closest[1], closest[2].progress, closest[2].distance
        )
        progress.delay = get_delay(progress, date, when, tzinfo)

        # if closest and next_closest involve the same stop
        # (e.g. it's a circular route),
        # choose the one with the smaller delay
        if next_closest and (
            closest[0].stop_id == next_closest[1].stop_id
            or closest[1].stop_id == next_closest[0].stop_id
        ):
            alt = Progress(
                stop_times,
                next_closest[0],
                next_closest[1],
                next_closest[2].progress,
                next_closest[2].distance,
            )
            alt.delay = get_delay(alt, date, when, tzinfo)
            if progress.delay is None or (
                alt.delay is not None and abs(alt.delay) < abs(progress.delay)
            ):
                progress = alt

        if (
            progress.delay is not None and abs(progress.delay) > 43200
        ):  # more than 12 hours
            logger.warning("%s delay is %s", item, progress.delay)

    return progress


def add_progress_and_delay(
    item,
    stop_time=None,
    stop_times=None,
    tzinfo=None,
    use_route_links=True,
    modified_at=None,
):
    progress = get_progress(
        item, stop_time, stop_times, tzinfo, use_route_links, modified_at
    )
    if not progress:
        return

    item["progress"] = progress.to_json()
    if progress.delay is not None:
        item["delay"] = progress.delay
