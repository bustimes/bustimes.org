import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from difflib import Differ
from itertools import pairwise

from django.core.cache import cache
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

from .models import Calendar, Route, StopTime, Trip

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


def cached_ids(kind, routes, when, extra, get_ids) -> list[int]:
    """Cache ids that depend on some routes and a date,
    until the routes' services are next saved (e.g. reimported), or for an hour"""
    if not routes:
        return []
    key = f"{kind}:{when}:{extra}:" + ",".join(
        sorted(f"{route.id}:{route.service_modified_at}" for route in routes)
    )
    key = f"{kind}:{hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()}"
    ids = cache.get(key)
    if ids is None:
        ids = get_ids()
        cache.set(key, ids, 3600)
    return ids


def get_active_route_ids(routes, when, revisions=True) -> list[int]:
    """ids of the routes that are active on a date
    (routes must have service_modified_at - see Route.objects.with_service_modified_at)
    """
    return cached_ids(
        "routes",
        routes,
        when,
        revisions,
        lambda: list(
            Route.objects.filter(id__in=[route.id for route in routes])
            .active_on(when, revisions)
            .values_list("id", flat=True)
        ),
    )


def get_active_calendar_ids(routes, when, scotland, calendar_ids=None) -> list[int]:
    """ids of the calendars that are active on a date -
    either out of `calendar_ids`, or of the routes that are active on the date"""

    def get_ids():
        calendars = Calendar.objects.active_on(when, calendar_ids, scotland=scotland)
        if calendar_ids is None:
            calendars = calendars.for_routes(
                Route.objects.filter(id__in=[route.id for route in routes]).active_on(
                    when
                )
            )
        return list(calendars.values_list("id", flat=True))

    return cached_ids(
        "calendars", routes, when, (scotland, calendar_ids is None), get_ids
    )


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
        scotland = stop.pk[:1] == "6" and ":" not in stop.pk and stop.pk[:4].isdigit()

        times = times.filter(
            trip__route__in=get_active_route_ids(routes, date),
            trip__calendar__in=get_active_calendar_ids(routes, date, scotland),
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


GENERIC_WORDS = {
    "adj",
    "and",
    "bay",
    "bus",
    "centre",
    "circ",
    "circular",
    "city",
    "high",
    "interchange",
    "lane",
    "opp",
    "park",
    "rail",
    "railway",
    "road",
    "school",
    "square",
    "stance",
    "stand",
    "station",
    "stop",
    "street",
    "the",
    "town",
    "via",
}


def significant_words(name: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z]+", name.lower())
        if len(word) > 2 and word not in GENERIC_WORDS
    }


def is_wrong_way_round(origin, destination, actual_origin, actual_destination) -> bool:
    origin, destination, actual_origin, actual_destination = map(
        significant_words, (origin, destination, actual_origin, actual_destination)
    )
    same = bool(origin & actual_origin) + bool(destination & actual_destination)
    swapped = bool(origin & actual_destination) + bool(destination & actual_origin)
    return swapped > same


def descriptions_are_redundant(route) -> bool:
    o, d = route.origin, route.destination
    return bool(
        o
        and d
        and route.outbound_description in ("", f"{o} - {d}", f"{o} to {d}")
        and route.inbound_description in ("", f"{d} - {o}", f"{d} to {o}")
    )


def get_descriptions(routes):
    inbound_outbound_descriptions = {
        (route.outbound_description, route.inbound_description): None
        for route in routes
        if route.outbound_description
        and route.inbound_description
        and route.outbound_description != route.inbound_description
        and not descriptions_are_redundant(route)
    }.keys()

    origins_and_destinations = []
    for route in routes:
        if route.origin and route.destination:
            parts = tuple(filter(None, [route.origin, route.via, route.destination]))
            # ignore duplicates, and mirror images (ignoring vias) of ones we've already got
            if not any(
                parts == other or (parts[0], parts[-1]) == (other[-1], other[0])
                for other in origins_and_destinations
            ):
                origins_and_destinations.append(parts)

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
            for j, other_parts in enumerate(origins_and_destinations[i + 1 :], 1):
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

    entry = cache.get(service.id)
    if not entry or entry[0] != service.modified_at:
        entry = (service.modified_at, {})
        cache[service.id] = entry

    routes_by_date = entry[1]
    if when in routes_by_date:
        cache.hits += 1
    else:
        cache.misses += 1
        for stale in [date for date in routes_by_date if abs(date - when).days > 1]:
            del routes_by_date[stale]
        routes_by_date[when] = list(
            service.route_set.select_related("source").active_on(when)
        )

    return routes_by_date[when]


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

    routes = get_service_routes(journey.service, date)
    if timezone.localtime(departure_time or datetime).hour < 7:
        # might be timetabled as part of the previous day (start after 24:00)
        yesterday_routes = get_service_routes(journey.service, date - timedelta(days=1))
    else:
        yesterday_routes = []
    if routes or yesterday_routes:
        trips = Trip.objects.filter(
            Q(route__in=routes, start__lt=timedelta(days=1))
            | Q(route__in=yesterday_routes, start__gte=timedelta(days=1))
        )
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
        if start_time.hour < 7:
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
        if hours < 7:
            start |= Q(start=timedelta(days=1, hours=hours, minutes=minutes))
    else:
        start = Q()

    if arrival_time:
        arrival_time = timezone.localtime(arrival_time)
        end = Q(end=timedelta(hours=arrival_time.hour, minutes=arrival_time.minute))
        if arrival_time.hour < 7:
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

        if start_time < timedelta(hours=7):
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
