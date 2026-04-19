# "Real Time Passenger Information"-ish stuff - calculating delays etc

from datetime import datetime, timedelta
from itertools import pairwise
from django.contrib.gis.geos import LineString, Point
from django.contrib.gis.db.models.functions import Distance, LineLocatePoint
from django.utils import timezone

from bustimes.models import RouteLink, StopTime, Trip
from bustimes.utils import contiguous_stoptimes_only
from vehicles.utils import calculate_bearing


def get_route_bearing(geometry: LineString, progress: float):
    """Get the bearing of the route at a given progress point (0-1)."""
    delta = 0.01
    p1 = geometry.interpolate_normalized(max(0, progress - delta))
    p2 = geometry.interpolate_normalized(min(1, progress + delta))
    return calculate_bearing(p1, p2)


def get_stop_times(item):
    trip = Trip.objects.select_related("calendar").get(pk=item["trip_id"])
    trips = trip.get_trips()

    stop_times = (
        StopTime.objects.filter(trip__in=trips)
        .filter(stop__latlong__isnull=False)
        .select_related("stop")
        .only("arrival", "departure", "stop__latlong")
        .order_by("trip__start", "id")
    )

    if len(trips) > 1:
        return contiguous_stoptimes_only(stop_times, trip.id)

    return stop_times


class Progress:
    def __init__(self, stop_times, prev_stop_time, next_stop_time, progress, distance):
        self.stop_times = list(stop_times)
        self.sequence = self.stop_times.index(prev_stop_time)
        self.prev_stop_time = prev_stop_time
        self.next_stop_time = next_stop_time
        self.progress = round(progress, 3)
        self.distance = distance

    def to_json(self):
        return {
            "id": self.prev_stop_time.id,
            "sequence": self.sequence,
            "prev_stop": self.prev_stop_time.stop_id,
            "next_stop": self.next_stop_time.stop_id,
            "progress": self.progress,
        }


def get_progress(item, stop_time=None):
    point = Point(*item["coordinates"], srid=4326)
    point_3857 = point.transform(3857, clone=True)

    if stop_time:
        stop_times = [
            st
            for st in stop_time.trip.stoptime_set.all()  # prefetched earlier
            if st.stop_id and st.stop.latlong
        ]
    else:
        try:
            stop_times = get_stop_times(item)
        except Trip.DoesNotExist:
            return

    route_links = {}
    if "service_id" in item:
        for rl in RouteLink.objects.filter(
            service=item["service_id"],
            geometry__dwithin=(point, 0.01),  # ~1km in degrees
        ).annotate(
            progress=LineLocatePoint("geometry", point),
            distance=Distance("geometry", point),
        ):
            rl.distance = rl.distance.m  # convert to meters
            route_links[(rl.from_stop_id, rl.to_stop_id)] = rl

    nearby_pairs = []
    for a, b in pairwise(stop_times):
        key = (a.stop_id, b.stop_id)
        if key in route_links:
            rl = route_links[key]
            if rl.distance < 1000:  # within ~1km
                nearby_pairs.append((a, b, rl))
        else:
            geometry = LineString([a.stop.latlong, b.stop.latlong], srid=4326)
            geometry_3857 = geometry.transform(3857, clone=True)
            distance = geometry_3857.distance(point_3857)  # in meters
            if distance < 1000:  # within ~1km
                rl = RouteLink(from_stop=a.stop, to_stop=b.stop, geometry=geometry)
                rl.distance = distance
                rl.progress = geometry.project_normalized(point)
                nearby_pairs.append((a, b, rl))

    if not nearby_pairs:
        return

    nearby_pairs.sort(key=lambda p: p[2].distance)

    closest = nearby_pairs[0]

    if len(nearby_pairs) >= 2 and item["heading"] is not None:
        vehicle_heading = int(item["heading"])

        route_bearing = get_route_bearing(closest[2].geometry, closest[2].progress)

        difference = (vehicle_heading - route_bearing + 180) % 360 - 180
        next_closest = nearby_pairs[1]

        if not (abs(difference) < 90) and next_closest[2].distance < 100:
            # bus seems to be heading the wrong way - does the bus go both ways on this road?
            # try the next closest pair of stops:
            route_bearing = get_route_bearing(
                next_closest[2].geometry, next_closest[2].progress
            )

            difference = (vehicle_heading - route_bearing + 180) % 360 - 180
            if abs(difference) < 90:
                closest = next_closest
                distance = next_closest[2].distance

    return Progress(
        stop_times, closest[0], closest[1], closest[2].progress, closest[2].distance
    )


def add_progress_and_delay(item, stop_time=None):
    progress = get_progress(item, stop_time)
    if not progress:
        return

    item["progress"] = progress.to_json()
    when = datetime.fromisoformat(item["datetime"])
    when = timezone.localtime(when)
    when = timedelta(hours=when.hour, minutes=when.minute, seconds=when.second)

    prev_time = progress.prev_stop_time.departure_or_arrival()
    next_time = progress.next_stop_time.arrival_or_departure()

    # correct for timetable times being > 24 hours:
    if when - prev_time < -timedelta(hours=12):
        when += timedelta(hours=24)
    elif when - prev_time > timedelta(hours=12):
        when -= timedelta(hours=24)

    # TODO: cope with waittimes better

    expected_time = prev_time + (next_time - prev_time) * progress.progress
    delay = int((when - expected_time).total_seconds())
    item["delay"] = delay
