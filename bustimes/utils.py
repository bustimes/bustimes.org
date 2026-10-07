import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from difflib import Differ
from itertools import pairwise

from django.db.models import (
    Case,
    DateTimeField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Value,
    When,
)
from django.db.models.functions import Abs, Least
from django.utils import timezone
from sql_util.utils import Exists

from .models import Calendar, StopTime, Trip

differ = Differ(charjunk=lambda _: True)


def get_sha1(path):
    sha1 = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as open_file:
        while data := open_file.read(65536):
            sha1.update(data)
    return sha1.hexdigest()


class log_time_taken:
    def __init__(self, logger):
        self.logger = logger

    def __enter__(self):
        self.start = datetime.now(UTC)

    def __exit__(self, _, __, ___):
        self.logger.info(f"  ⏱️ {datetime.now(UTC) - self.start}")


def get_stop_times(date: date, time: timedelta | None, stop, routes, trip_ids=None):
    times = StopTime.objects.filter(pick_up=True)

    try:
        times = times.filter(stop__stop_area=stop)
    except ValueError:
        times = times.filter(stop=stop)

    if trip_ids:
        times = times.filter(trip__in=trip_ids)
        one_day = timedelta(1)
        times = times.filter(
            Q(departure__lt=time)
            | Q(departure__gte=one_day, departure__lt=time + one_day)
        )
        times = times.annotate(
            date=Case(
                When(
                    departure__gte=one_day,
                    then=Value(date - one_day),
                ),
                default=date,
            )
        )
    else:
        routes = list(routes.active_on(date))

        scotland = stop.pk[:1] == "6" and ":" not in stop.pk and stop.pk[:4].isdigit()

        times = times.filter(
            trip__route__in=routes,
            trip__calendar__in=list(
                Calendar.objects.active_on(date, scotland=scotland)
                .for_routes(routes)
                .values_list("id", flat=True)
            ),
        )

        if time is not None:
            times = times.filter(trip__end__gte=time, departure__gte=time)

            midnight = datetime.fromisoformat(f"{date}T12:00:00") - timedelta(hours=12)

            times = times.annotate(
                departure_time=ExpressionWrapper(
                    F("departure") + midnight.timestamp(),
                    output_field=DateTimeField(),
                )
            ).order_by("departure_time", "id")
        else:
            times = times.filter(departure__isnull=False)

        times = times.annotate(date=Value(date))

    return times


def get_descriptions(routes):
    inbound_outbound_descriptions = {
        (route.outbound_description, route.inbound_description): None
        for route in routes
        if route.outbound_description != route.inbound_description
    }.keys()

    origins_and_destinations = list(
        {
            tuple(filter(None, [route.origin, route.via, route.destination])): None
            for route in routes
            if route.origin and route.destination
        }.keys()
    )

    if len(origins_and_destinations) > 1:
        # if all have the same via
        if all(
            len(parts) == 3 and parts[1] == origins_and_destinations[0][1]
            for parts in origins_and_destinations
        ):
            # remove vias
            origins_and_destinations = [
                (o, d) for (o, v, d) in origins_and_destinations
            ]

        # join "Holt - Sheringham" and "Sheringham - Cromer" for example
        # (like dominoes)
        for i, parts in enumerate(origins_and_destinations):
            for j, other_parts in enumerate(origins_and_destinations[i:]):
                if parts[0] == other_parts[-1]:
                    origins_and_destinations[i + j] = other_parts + parts[1:]
                    origins_and_destinations[i] = None
                    break
                if parts[-1] == other_parts[0]:
                    origins_and_destinations[i + j] = parts + other_parts[1:]
                    origins_and_destinations[i] = None
                    break
        origins_and_destinations = list(filter(None, origins_and_destinations))
        inbound_outbound_descriptions = ()

        # "or"
        if (
            len(origins_and_destinations) == 2
            and len(origins_and_destinations[0]) == 2
            and len(origins_and_destinations[1]) == 2
        ):
            if origins_and_destinations[0][1] == origins_and_destinations[1][1]:
                origins_and_destinations = [
                    (
                        f"{origins_and_destinations[0][0]} or {origins_and_destinations[1][0]}",
                        origins_and_destinations[0][1],
                    )
                ]
            elif origins_and_destinations[0][0] == origins_and_destinations[1][0]:
                origins_and_destinations = [
                    (
                        origins_and_destinations[0][0],
                        f"{origins_and_destinations[0][1]} or {origins_and_destinations[1][1]}",
                    )
                ]

    return inbound_outbound_descriptions, origins_and_destinations


class RoutesCache(dict):
    hits = 0
    misses = 0


_routes: ContextVar[RoutesCache | None] = ContextVar("routes_cache", default=None)
_routes_cache = RoutesCache()


@contextmanager
def cache_routes():
    """Reuse Route.objects.active_on results until the service is reimported"""
    _routes_cache.hits = _routes_cache.misses = 0
    token = _routes.set(_routes_cache)
    try:
        yield _routes_cache
    finally:
        _routes.reset(token)


def get_service_routes(service, when) -> list:
    """Route.objects.active_on for a service, maybe reusing an earlier result"""
    cache = _routes.get()
    if cache is None:
        return list(service.route_set.select_related("source").active_on(when))

    version = (service.modified_at, when)
    entry = cache.get(service.id)

    if entry and entry[0] == version:
        cache.hits += 1
    else:
        cache.misses += 1
        entry = (
            version,
            list(service.route_set.select_related("source").active_on(when)),
        )
        cache[service.id] = entry

    return entry[1]


def get_trip(
    journey,
    datetime=None,
    date=None,
    operator_ref=None,
    origin_ref=None,
    destination_ref=None,
    departure_time=None,
    arrival_time=None,
    journey_code="",
    block_ref=None,
    approximate_datetime=False,
    next_stop=None,
):
    if not journey.service:
        return

    if not datetime:
        datetime = journey.datetime
    if not date:
        date = timezone.localdate(departure_time or datetime)

    # TODO: get routes for previous day, in case journey starts after midnight
    routes = get_service_routes(journey.service, date)
    if routes:
        trips = Trip.objects.filter(route__in=routes)
    else:
        trips = Trip.objects.filter(route__service=journey.service)

    if destination_ref and " " not in destination_ref and destination_ref[:3].isdigit():
        destination = Q(destination=destination_ref)
    else:
        destination = Q()

    if origin_ref and " " not in origin_ref and origin_ref[:3].isdigit():
        origin = Exists("stoptime", filter=Q(stop=origin_ref))
    else:
        origin = Q()

    if journey.direction == "outbound":
        direction = Q(inbound=False)
    elif journey.direction == "inbound":
        direction = Q(inbound=True)
    else:
        direction = Q()

    if departure_time:
        start_time = timezone.localtime(departure_time)
        start_timedelta = timedelta(hours=start_time.hour, minutes=start_time.minute)
        start = Q(start=start_timedelta)
        if origin:
            start |= Exists(
                "stoptime", filter=Q(stop=origin_ref, departure=start_timedelta)
            )
        if start_time.hour < 6:
            start |= Q(start=start_timedelta + timedelta(days=1))
            if origin:
                start |= Exists(
                    "stoptime",
                    filter=Q(
                        stop=origin_ref, departure=start_timedelta + timedelta(days=1)
                    ),
                )

    elif len(journey_code) == 4 and journey_code.isdigit() and int(journey_code) < 2400:
        hours = int(journey_code[:-2])
        minutes = int(journey_code[-2:])
        start = Q(start=timedelta(hours=hours, minutes=minutes))
    else:
        start = Q()

    if arrival_time:
        arrival_time = timezone.localtime(arrival_time)
        end = Q(end=timedelta(hours=arrival_time.hour, minutes=arrival_time.minute))
        if arrival_time.hour < 6:
            end |= Q(
                end=timedelta(
                    days=1, hours=arrival_time.hour, minutes=arrival_time.minute
                )
            )

    # special strategy for TfL data
    if operator_ref == "TFLO" and departure_time and origin_ref and destination:
        try:
            try:
                trips = trips.filter(
                    Exists("stoptime", filter=Q(stop=origin_ref)),
                    Exists("stoptime", filter=Q(stop=destination_ref)),
                    start,
                )
                return trips.get()
            except Trip.MultipleObjectsReturned:
                trips = trips.active_on(date)
                return trips.get()
        except (Trip.DoesNotExist, Trip.MultipleObjectsReturned):
            return

    if journey.code:
        code = Q(ticket_machine_code=journey.code)
    else:
        code = Q()

    score = 0
    if code:
        score += Case(When(code, then=1), default=0)
    if block_ref:
        score += Case(When(block=block_ref, then=1), default=0)
    if start:
        score += Case(When(start, then=1), default=0)
    if arrival_time:
        score += Case(When(end, then=1), default=0)
    if direction:
        score += Case(When(direction, then=1), default=0)
    if destination:
        score += Case(When(destination, then=1), default=0)
    if origin:
        score += Case(When(origin, then=1), default=0)

    if approximate_datetime:
        start_time = timezone.localtime(datetime)
        start_time = timedelta(hours=start_time.hour, minutes=start_time.minute)

        if start_time < timedelta(hours=6):
            # might be timetabled as part of the previous day
            start_times = (start_time, start_time + timedelta(days=1))
        else:
            start_times = (start_time,)

        if next_stop:
            if next_stop[3:4] == "0":
                stop_q = Q(stop=next_stop)  # translink
            else:
                stop_q = Q(stop__naptan_code=next_stop)  # lothian
        else:
            stop_q = None

        condition = Q()
        score = None
        for start_time in start_times:
            start_range = (
                start_time - timedelta(minutes=10),
                start_time + timedelta(minutes=5),
            )
            start_condition = Q(start__range=start_range)
            if stop_q:
                start_condition &= Q(
                    Exists(
                        "stoptime",
                        filter=Q(
                            stop_q,
                            departure__range=start_range,
                        ),
                    )
                )
            condition |= start_condition

            distance = Abs(int(start_time.total_seconds()) - F("start"))
            score = distance if score is None else Least(score, distance)

        score = ExpressionWrapper(-score, output_field=IntegerField())
    else:
        condition = code | start

    trips = trips.filter(condition).annotate(score=score).order_by("-score")

    if trips:
        if (
            trips[0].start >= timedelta(days=1)
            and timezone.localtime(departure_time or datetime).hour < 12
        ):
            date -= timedelta(days=1)
        if len(trips) > 1 and trips[0].score == trips[1].score:
            filtered_trips = trips.active_on(date)
            if filtered_trips:
                trips = filtered_trips

        journey.date = date

        return trips[0]


def contiguous_stoptimes_only(stoptimes, trip_id):
    stoptimes_list = list(stoptimes)
    for a, b in pairwise(stoptimes):
        if a.trip_id != b.trip_id:
            if a.stop_id != b.stop_id:
                # trips are not contiguous, return only the stops for trip_id
                return [stop for stop in stoptimes if stop.trip_id == trip_id]
            # merge a and b - they describe the same stop
            a.departure = b.departure
            a.pick_up = b.pick_up
            stoptimes_list.remove(b)

    # trips were contiguous, return all stops
    return stoptimes_list
