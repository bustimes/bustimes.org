# "Real Time Passenger Information"-ish stuff - calculating delays etc

import datetime
import logging
import math
from collections import Counter, OrderedDict
from itertools import pairwise

import numpy as np
import sentry_sdk
import shapely

from bustimes.models import RouteLink, StopTime, Trip
from bustimes.utils import contiguous_stoptimes_only

logger = logging.getLogger(__name__)

EARTH_RADIUS = 6371008.8  # mean radius - the sphere ST_DistanceSphere uses

# how close (in metres) a bus has to be to count as between a pair of stops.
# a route link follows the road, so a bus on it is only as far away as its GPS
# is wrong. a straight line between the stops can be much further from the road
# the bus is actually on, and the further apart the stops the worse it gets -
# 10% of real route links stray more than 0.35 x the distance between their stops
NEARBY_ROUTE_LINK = 300
NEARBY_STRAIGHT_LINE = 600
STRAY_PER_METRE = 0.5


def local_metres(coords, cos_latitude):
    """project WGS84 (longitude, latitude) coordinates to metres near a
    reference latitude.

    an equirectangular projection - only good over a few km, but that's all
    we measure, and unlike EPSG:3857 the units are actual metres
    """
    metres = np.radians(coords) * EARTH_RADIUS
    metres[..., 0] *= cos_latitude
    return metres


# service_id -> (service modified_at, {(from_stop_id, to_stop_id): (line, cos_latitude)})
# - shared by all the trips of a service, but only the pairs of stops they need
ROUTE_LINKS_CACHE_MAXSIZE = 2_000

_route_links_cache: OrderedDict[int, tuple] = OrderedDict()


def _fetch_route_links(service_id, keys) -> dict:
    route_links = dict.fromkeys(keys)  # None - no route link
    for from_stop_id, to_stop_id, geometry in RouteLink.objects.filter(
        service=service_id,
        from_stop__in={key[0] for key in keys},
        to_stop__in={key[1] for key in keys},
    ).values_list("from_stop", "to_stop", "geometry"):
        if (key := (from_stop_id, to_stop_id)) in route_links:
            coords = shapely.get_coordinates(shapely.from_wkb(bytes(geometry.wkb)))
            cos_latitude = math.cos(math.radians(coords[0][1]))
            route_links[key] = (
                shapely.LineString(local_metres(coords, cos_latitude)),
                cos_latitude,
            )
    return route_links


def get_route_links(service_id, keys, modified_at=None) -> dict:
    """route links projected to local metres, each near its own start -
    reused until the service is reimported (if modified_at is known)"""
    if not service_id or not keys:
        return {}
    if modified_at is None:
        return _fetch_route_links(service_id, keys)

    entry = _route_links_cache.get(service_id)
    if not entry or not (modified_at is UNKNOWN or entry[0] == modified_at):
        entry = _route_links_cache[service_id] = (
            None if modified_at is UNKNOWN else modified_at,
            {},
        )
    _route_links_cache.move_to_end(service_id)
    if len(_route_links_cache) > ROUTE_LINKS_CACHE_MAXSIZE:
        _route_links_cache.popitem(last=False)

    route_links = entry[1]
    if missing := [key for key in keys if key not in route_links]:
        route_links.update(_fetch_route_links(service_id, missing))
    return route_links


def pair_keys(stop_times):
    return [(a.stop_id, b.stop_id) for a, b in pairwise(stop_times)]


class Pairs:
    """each pair of consecutive stops, and the road between them -
    the route link if there is one, or else a straight line.

    each line is projected to metres near its own start,
    so a point must be projected with the same cos_latitudes to compare
    """

    def __init__(self, stop_times, route_links):
        self.pairs = list(pairwise(stop_times))
        lines = np.empty(len(self.pairs), dtype=object)
        self.cos_latitudes = np.empty(len(self.pairs))
        self.thresholds = np.empty(len(self.pairs))

        for i, (a, b) in enumerate(self.pairs):
            if route_link := route_links.get((a.stop_id, b.stop_id)):
                lines[i], self.cos_latitudes[i] = route_link
                self.thresholds[i] = NEARBY_ROUTE_LINK
            else:
                a_coords = a.stop.latlong.coords
                cos_latitude = math.cos(math.radians(a_coords[1]))
                coords = np.array((a_coords, b.stop.latlong.coords))
                lines[i] = shapely.LineString(local_metres(coords, cos_latitude))
                self.cos_latitudes[i] = cos_latitude
                self.thresholds[i] = (
                    NEARBY_STRAIGHT_LINE + STRAY_PER_METRE * lines[i].length
                )

        self.lines = lines

    def nearby(self, longitude, latitude):
        """(index, line, progress along it from 0 to 1, distance in metres)
        of each pair close enough, closest first"""
        points = shapely.points(
            math.radians(longitude) * EARTH_RADIUS * self.cos_latitudes,
            math.radians(latitude) * EARTH_RADIUS,
        )
        distances = shapely.distance(self.lines, points)
        (indices,) = np.nonzero(distances < self.thresholds)
        indices = indices[np.argsort(distances[indices], kind="stable")]
        progresses = shapely.line_locate_point(
            self.lines[indices], points[indices], normalized=True
        )
        return [
            (i, self.lines[i], 0 if math.isnan(progress) else progress, distances[i])
            for i, progress in zip(indices.tolist(), progresses.tolist())
        ]


def get_route_bearing(line, progress: float):
    """Get the bearing of a line (in local metres) at a given progress point (0-1)."""
    delta = 0.01
    p1, p2 = shapely.get_coordinates(
        shapely.line_interpolate_point(
            line, [max(0, progress - delta), min(1, progress + delta)], normalized=True
        )
    )
    return math.degrees(math.atan2(p2[0] - p1[0], p2[1] - p1[1])) % 360


# trip_id -> ((service modified_at, date), trip, stop_times, pairs)
STOP_TIMES_CACHE_MAXSIZE = 6_000

_stop_times_cache: OrderedDict[int, tuple[tuple, Trip, list, Pairs]] = OrderedDict()
stop_times_cache_stats = Counter()

# service modified_at not known - reuse whatever is cached
UNKNOWN = object()


def _fetch_stop_times(trip_id, date, modified_at=None):
    trip = (
        Trip.objects.select_related("calendar", "route__service")
        .defer("route__service__geometry", "route__service__search_vector")
        .get(pk=trip_id)
    )
    trips = trip.get_parts(date)

    stop_times = (
        StopTime.objects.filter(trip__in=trips)
        .filter(stop__latlong__isnull=False)
        .select_related("stop")
        .only("trip_id", "arrival", "departure", "pick_up", "stop__latlong")
        .order_by("trip__start", "id")
    )

    if len(trips) > 1:
        stop_times = contiguous_stoptimes_only(stop_times, trip.id)
    else:
        stop_times = list(stop_times)

    route_links = get_route_links(
        trip.route and trip.route.service_id, pair_keys(stop_times), modified_at
    )

    return trip, stop_times, Pairs(stop_times, route_links)


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
        stop_times_cache_stats["hits"] += 1
        return entry[1:]

    stop_times_cache_stats["misses"] += 1

    trip, stop_times, pairs = _fetch_stop_times(trip_id, date, modified_at)

    if modified_at is UNKNOWN:
        modified_at = None
    version = (modified_at, date)

    if stop_times:
        stop_field = StopTime._meta.get_field("stop")
        for stop_time in stop_times:
            # save a bit of memory
            stop_field.delete_cached_value(stop_time)
        _stop_times_cache[trip_id] = (version, trip, stop_times, pairs)
        _stop_times_cache.move_to_end(trip_id)
        if len(_stop_times_cache) > STOP_TIMES_CACHE_MAXSIZE:
            _stop_times_cache.popitem(last=False)

    return trip, stop_times, pairs


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
            "prev_time": int(scheduled_time(self.prev_stop_time).total_seconds()),
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
    tzinfo=None,
    modified_at=None,
    stop_times=None,
    pairs=None,
) -> Progress | None:
    """`stop_times` and `pairs` (optional) - for a journey without a real Trip
    (see ImportLiveVehiclesCommand.get_progress_journey)"""
    when = datetime.datetime.fromisoformat(item["datetime"])
    date = datetime.date.fromisoformat(item["date"])

    if pairs is None and "trip_id" in item:
        try:
            trip, stop_times, pairs = get_stop_times(item["trip_id"], date, modified_at)
        except Trip.DoesNotExist:
            return
        if tzinfo is None and trip.route:
            tzinfo = trip.route.timezone

    if not stop_times:
        return

    with sentry_sdk.start_span(name="nearby pairs"):
        nearby_pairs = pairs.nearby(*item["coordinates"])
        if not nearby_pairs:
            return

        # the data source says which stops the vehicle is between -
        # prefer pairs matching both, then the next stop, then the previous stop
        if stops := item.get("stops"):
            prev_stop, next_stop = stops

            def score(pair):
                a, b = pairs.pairs[pair[0]]
                return (a.stop_id == prev_stop) + 2 * (b.stop_id == next_stop)

            if best := max(map(score, nearby_pairs)):
                nearby_pairs = [pair for pair in nearby_pairs if score(pair) == best]

    with sentry_sdk.start_span(name="closest pairs"):
        closest = nearby_pairs[0]
        next_closest = nearby_pairs[1] if len(nearby_pairs) > 1 else None

        if next_closest and item["heading"] is not None:
            vehicle_heading = int(item["heading"])

            route_bearing = get_route_bearing(closest[1], closest[2])

            difference = (vehicle_heading - route_bearing + 180) % 360 - 180

            if not (abs(difference) < 90) and next_closest[3] < 100:
                # bus seems to be heading the wrong way - does the bus go both ways on this road?
                # try the next closest pair of stops:
                route_bearing = get_route_bearing(next_closest[1], next_closest[2])

                difference = (vehicle_heading - route_bearing + 180) % 360 - 180
                if abs(difference) < 90:
                    closest = next_closest

    with sentry_sdk.start_span(name="delay"):
        prev_stop_time, next_stop_time = pairs.pairs[closest[0]]
        progress = Progress(
            stop_times, prev_stop_time, next_stop_time, closest[2], closest[3]
        )
        progress.delay = get_delay(progress, date, when, tzinfo)

        # if closest and next_closest involve the same stop
        # and are about as close as each other
        # (e.g. it's a circular route, or out and back along the same road),
        # choose the one with the smaller delay
        if next_closest and next_closest[3] - closest[3] < 50:
            alt_prev, alt_next = pairs.pairs[next_closest[0]]
            if (
                prev_stop_time.stop_id == alt_next.stop_id
                or next_stop_time.stop_id == alt_prev.stop_id
            ):
                alt = Progress(
                    stop_times, alt_prev, alt_next, next_closest[2], next_closest[3]
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
    tzinfo=None,
    modified_at=None,
    stop_times=None,
    pairs=None,
):
    progress = get_progress(item, tzinfo, modified_at, stop_times, pairs)
    if not progress:
        return

    item["progress"] = progress.to_json()
    # (keep any delay the source itself told us)
    if progress.delay is not None and "delay" not in item:
        item["delay"] = progress.delay

    # a journey split into parts (see Trip.get_parts)
    trip_ids = list(dict.fromkeys(st.trip_id for st in progress.stop_times))
    if len(trip_ids) > 1:
        item["trip_ids"] = trip_ids
        item["progress"]["trip_id"] = progress.prev_stop_time.trip_id


def by_trip_id(items) -> dict:
    """{trip_id: item} - including the other parts of split trips,
    but preferring a vehicle that's actually on the trip"""
    by_trip = {}
    for item in items:
        for trip_id in item.get("trip_ids", ()):
            by_trip.setdefault(trip_id, item)
    for item in items:
        if "trip_id" in item:
            by_trip[item["trip_id"]] = item
    return by_trip


def scheduled_time(stop_time):
    if stop_time.departure is not None:
        return stop_time.departure
    return stop_time.arrival


def is_before_stop(item, stop_time) -> bool:
    """is the vehicle yet to leave this stop?"""
    progress = item["progress"]

    # compare scheduled times first - stop time ids can be stale
    # (if the timetable has been reimported since progress was calculated)
    if "prev_time" in progress:
        time = int(scheduled_time(stop_time).total_seconds())
        if progress["prev_time"] != time:
            return progress["prev_time"] < time
        if progress["prev_stop"] == stop_time.stop_id:
            return progress["progress"] == 0
        if progress["next_stop"] == stop_time.stop_id:
            return True

    stop_time_id = stop_time.id
    trip_id = stop_time.trip_id
    trip_ids = item.get("trip_ids")
    if (
        trip_ids
        and trip_id != progress.get("trip_id")
        and trip_id in trip_ids
        and progress.get("trip_id") in trip_ids
    ):
        # different parts of a split trip - stop time ids aren't comparable
        return trip_ids.index(progress["trip_id"]) < trip_ids.index(trip_id)
    return (
        progress["id"] < stop_time_id
        or progress["id"] == stop_time_id
        and progress["progress"] == 0
    )
