import hashlib
from datetime import date, datetime, timedelta
from difflib import Differ
from itertools import pairwise

from django.db.models import (
    Case,
    DateTimeField,
    ExpressionWrapper,
    F,
    Q,
    Value,
    When,
    OuterRef,
    IntegerField,
)
from django.db.models.functions import Abs
from django.utils import timezone
from sql_util.utils import Exists

from .models import (
    Calendar,
    CalendarBankHoliday,
    CalendarDate,
    StopTime,
    Trip,
    Route,
    Version,
)

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
        self.start = datetime.now()

    def __exit__(self, _, __, ___):
        self.logger.info(f"  ⏱️ {datetime.now() - self.start}")


def get_routes(routes, when):
    filter_by_revision_number = True
    if type(routes) is list:
        filter_by_revision_number = any(route.revision_number for route in routes)

        routes = Route.objects.filter(
            id__in=[route.id for route in routes]
        ).select_related("source")

    if filter_by_revision_number:
        routes = routes.filter(
            Q(start_date=None) | Q(start_date__lte=when),
            ~Exists(
                Route.objects.filter(
                    service__isnull=False,
                    source=OuterRef("source"),
                    service_code=OuterRef("service_code"),
                    revision_number_context=OuterRef("revision_number_context"),
                    start_date__lte=when,
                    revision_number__gt=OuterRef("revision_number"),
                )
            ),
        ).order_by("id")

    # complicated way of working out which Passenger .zip applies.
    # When multiple versions share the same start_date, prefer the one with the
    # greater name - filenames are like 'lynxbus_<unix-timestamp>.zip' so this
    # picks the most recently published.
    routes = routes.filter(
        Q(version=None)
        | Q(
            ~Exists(
                Version.objects.filter(
                    source=OuterRef("version__source"),
                    start_date__lte=when,
                    end_date__gte=when,
                ).filter(
                    Q(start_date__gt=OuterRef("version__start_date"))
                    | Q(
                        start_date=OuterRef("version__start_date"),
                        name__gt=OuterRef("version__name"),
                    )
                )
            ),
            version__start_date__lte=when,
            version__end_date__gte=when,
        )
    )

    routes = routes.filter(
        Q(start_date=None) | Q(start_date__lte=when),
        Q(end_date=None) | Q(end_date__gte=when),
    )

    # TfL: try to pick the file with the highest Service Change Number, if there are multiple
    # https://techforum.tfl.gov.uk/t/duplicate-files-in-journey-planner-datastore-is-there-a-way-to-choose-the-right-one/2571
    # (actually using service_code order, which assumes that the SCNs have the same number of digits)
    routes = routes.filter(
        ~Q(code__contains="tfl_")
        | ~Exists(
            Route.objects.filter(
                Q(end_date__gte=when) | Q(end_date__isnull=True),
                service=OuterRef("service"),
                source=OuterRef("source"),
                service_code__gt=OuterRef("service_code"),
                start_date__lte=when,
            )
        )
    ).filter(
        ~Exists(
            Route.objects.filter(
                file_hash=OuterRef("file_hash"),
                file_hash__isnull=False,
                code=OuterRef("code"),
                id__gt=OuterRef("id"),
            )
        )
    )
    return routes


def get_calendars(when: date | datetime, calendar_ids=None, scotland=None):
    between_dates = Q(start_date__lte=when) & (Q(end_date__gte=when) | Q(end_date=None))

    calendars = Calendar.objects.filter(between_dates)
    calendar_calendar_dates = CalendarDate.objects.filter(calendar=OuterRef("id"))
    calendar_dates = calendar_calendar_dates.filter(between_dates)

    if calendar_ids is not None:
        # cunningly make the query faster
        calendars = calendars.filter(id__in=calendar_ids)
        calendar_dates = calendar_dates.filter(calendar__in=calendar_ids)
    exclusions = calendar_dates.filter(operation=False)
    inclusions = calendar_dates.filter(operation=True)
    special_inclusions = Exists(inclusions.filter(special=True))
    only_certain_dates = Exists(
        calendar_calendar_dates.filter(special=False, operation=True)
    )

    if scotland is None:
        calendar_bank_holidays = CalendarBankHoliday.objects.filter(
            bank_holiday__bankholidaydate__date=when,
            calendar=OuterRef("id"),
        )
    else:
        calendar_bank_holidays = CalendarBankHoliday.objects.filter(
            Q(bank_holiday__bankholidaydate__scotland=None)
            | Q(bank_holiday__bankholidaydate__scotland=scotland),
            bank_holiday__bankholidaydate__date=when,
            calendar=OuterRef("id"),
        )

    bank_holiday_inclusions = Exists(calendar_bank_holidays.filter(operation=True))

    return calendars.annotate(
        bank_holiday_exclusions=Exists(calendar_bank_holidays.filter(operation=False))
    ).filter(
        Q(
            Q(**{f"{when:%a}".lower(): True}),  # day of week
            ~only_certain_dates | Exists(inclusions),  # special dates of operation
            bank_holiday_exclusions=False,
        )
        | special_inclusions
        | bank_holiday_inclusions & Q(bank_holiday_exclusions=False),
        ~Exists(exclusions),
    )


def get_other_trips_in_block(trip, date):
    if not trip.route_id:
        return Trip.objects.none()

    trips = Trip.objects.filter(
        block=trip.block,
        route__source=trip.route.source_id,
        route__version=trip.route.version_id,
        garage=trip.garage_id,
        operator=trip.operator_id,
    )
    if trip.route.service_id:
        trips = trips.filter(route__service__isnull=False)

    routes = Route.objects.filter(trip__in=trips).select_related("source")

    calendars = get_calendars(date, [trip.calendar_id for trip in trips])
    routes = get_routes(routes, date)
    return trips.filter(calendar__in=calendars, route__in=routes).order_by("start")


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
        routes = list(get_routes(routes, date))

        scotland = stop.pk[:1] == "6" and ":" not in stop.pk and stop.pk[:4].isdigit()

        if not routes:
            times = times.none()

        times = times.filter(
            trip__route__in=routes,
            trip__calendar__in=get_calendars(date, scotland=scotland),
        )

        if time is not None:
            times = times.filter(trip__end__gte=time, departure__gte=time)

            midnight = datetime.fromisoformat(f"{date}T12:00:00") - timedelta(hours=12)

            times = times.annotate(
                departure_time=ExpressionWrapper(
                    F("departure") + midnight.timestamp(),
                    output_field=DateTimeField(),
                )
            ).order_by("departure_time")
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
                elif parts[-1] == other_parts[0]:
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
    routes = get_routes(journey.service.route_set.select_related("source"), date)
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
                trips = trips.filter(calendar__in=get_calendars(date))
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

    if approximate_datetime and next_stop:
        start_time = timezone.localtime(datetime)
        start_time = timedelta(hours=start_time.hour, minutes=start_time.minute)
        start_range = (
            start_time - timedelta(minutes=10),
            start_time + timedelta(minutes=5),
        )
        condition = Q(
            Exists(
                "stoptime",
                filter=Q(
                    stop__naptan_code=next_stop,
                    departure__range=start_range,
                ),
            ),
            start__range=start_range,
        )
        # score = F("start")
        score = ExpressionWrapper(
            -Abs(int(start_time.total_seconds()) - F("start")),
            output_field=IntegerField(),
        )
    else:
        condition = code | start

    if direction:
        condition &= destination | direction

    trips = trips.filter(condition).annotate(score=score).order_by("-score")

    if trips:
        if trips[0].start >= timedelta(days=1):
            date -= timedelta(days=1)
        if len(trips) > 1 and trips[0].score == trips[1].score:
            filtered_trips = trips.filter(calendar__in=get_calendars(date))
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
            else:
                # merge a and b - they describe the same stop
                a.departure_time = b.departure_time
                a.pick_up = b.pick_up
                stoptimes_list.remove(b)

    # trips were contiguous, return all stops
    return stoptimes_list
