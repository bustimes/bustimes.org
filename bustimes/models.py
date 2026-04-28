from datetime import timedelta
from itertools import pairwise

from django.contrib.gis.db import models
from django.db.models import Q
from django.db.models.functions import Upper
from django.urls import reverse
from django.utils.timezone import localdate
from timezone_field import TimeZoneField

from .fields import SecondsField
from .formatting import format_timedelta, time_datetime


class TimetableDataSource(models.Model):
    name = models.CharField(max_length=255)
    search = models.CharField(
        max_length=255,
        blank=True,
        help_text="for BODS sources, usually one of the operator's NOCs. But remember that searching for 'ANEA' for example will return *all* Arriva datasets, not just the ANEA one",
    )
    url = models.URLField(
        blank=True,
        verbose_name="URL",
        help_text="for non-BODS sources, i.e. Stagecoach, Passenger, or Ticketer",
    )
    modified_at = models.DateTimeField(null=True, blank=True, auto_now=True)
    operators = models.ManyToManyField("busstops.Operator", blank=True)
    settings = models.JSONField(null=True, blank=True)
    complete = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    region = models.ForeignKey(
        "busstops.Region", models.SET_NULL, null=True, blank=True
    )
    notes = models.CharField(null=True, blank=True)

    def __str__(self):
        return self.name


class Version(models.Model):
    source = models.ForeignKey(TimetableDataSource, models.CASCADE)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    datetime = models.DateTimeField(null=True, blank=True)
    name = models.CharField(null=True, blank=True)
    url = models.URLField(null=True, blank=True)
    current = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=("source", "start_date", "end_date")),
        ]

    def __str__(self):
        return self.name


class Route(models.Model):
    source = models.ForeignKey("busstops.DataSource", models.CASCADE)
    version = models.ForeignKey(Version, models.CASCADE, null=True, blank=True)
    code = models.CharField(max_length=255, blank=True)  # qualified filename
    service_code = models.CharField(max_length=255, blank=True)
    revision_number_context = models.CharField(max_length=48, blank=True)
    line_id = models.CharField(max_length=255, blank=True)
    registration = models.ForeignKey(
        "vosa.Registration", models.SET_NULL, null=True, blank=True
    )
    line_brand = models.CharField(max_length=255, blank=True)
    line_name = models.CharField(max_length=255, blank=True)
    revision_number = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    outbound_description = models.CharField(max_length=255, blank=True)
    inbound_description = models.CharField(max_length=255, blank=True)
    origin = models.CharField(max_length=255, blank=True)
    destination = models.CharField(max_length=255, blank=True)
    via = models.CharField(max_length=255, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    service = models.ForeignKey(
        "busstops.Service", models.CASCADE, null=True, blank=True
    )
    public_use = models.BooleanField(null=True)
    file_hash = models.CharField(max_length=40, null=True, blank=True, db_index=True)
    timezone = TimeZoneField(null=True, blank=True)

    class Meta:
        unique_together = ("source", "code")
        indexes = [
            models.Index(fields=("start_date", "end_date")),
            models.Index(
                fields=("source", "service_code"),
                condition=Q(service__isnull=False),
                name="route_source_service_code",
            ),
            models.Index(Upper("line_name"), name="route_line_name"),
        ]

    def __str__(self):
        return " – ".join(
            part for part in (self.line_name, self.line_brand, self.description) if part
        )

    def get_absolute_url(self):
        return reverse("route_xml", args=(self.source_id, self.code.split("#")[0]))


class RouteLink(models.Model):
    service = models.ForeignKey("busstops.Service", models.CASCADE)
    from_stop = models.ForeignKey(
        "busstops.StopPoint", models.CASCADE, related_name="link_from"
    )
    to_stop = models.ForeignKey(
        "busstops.StopPoint", models.CASCADE, related_name="link_to"
    )
    distance_metres = models.PositiveSmallIntegerField(null=True, blank=True)
    geometry = models.LineStringField()
    override = models.BooleanField(default=False)

    class Meta:
        unique_together = ("service", "from_stop", "to_stop")

    def __repr__(self):
        return f"<RouteLink: {self.pk} {self.service_id} {self.from_stop_id} {self.to_stop_id}>"

    def get_absolute_url(self):
        return reverse("routelink_detail", args=(self.id,))


class BankHoliday(models.Model):
    id = models.SmallAutoField(primary_key=True)
    name = models.CharField(unique=True, max_length=255)

    def __str__(self):
        return self.name


class BankHolidayDate(models.Model):
    bank_holiday = models.ForeignKey(BankHoliday, models.CASCADE)
    date = models.DateField()
    scotland = models.BooleanField(
        null=True, help_text="Yes = Scotland only, No = not Scotland, Unknown = both"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bank_holiday", "date"], name="unique_bank_holiday_date"
            )
        ]


class CalendarBankHoliday(models.Model):
    operation = models.BooleanField()
    bank_holiday = models.ForeignKey(BankHoliday, models.CASCADE)
    calendar = models.ForeignKey("bustimes.Calendar", models.CASCADE)

    class Meta:
        unique_together = ("bank_holiday", "calendar")

    def __str__(self):
        if self.operation:
            return str(self.bank_holiday)
        return f"not {self.bank_holiday}"


day_keys = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


class Calendar(models.Model):
    mon = models.BooleanField(default=False)
    tue = models.BooleanField(default=False)
    wed = models.BooleanField(default=False)
    thu = models.BooleanField(default=False)
    fri = models.BooleanField(default=False)
    sat = models.BooleanField(default=False)
    sun = models.BooleanField(default=False)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    summary = models.CharField(max_length=255, blank=True)
    bank_holidays = models.ManyToManyField(BankHoliday, through=CalendarBankHoliday)
    source = models.ForeignKey(
        "busstops.DataSource", models.CASCADE, null=True, blank=True
    )

    def contains(self, date):
        if not self.start_date or self.start_date <= date:
            if not self.end_date or self.end_date >= date:
                return True

    class Meta:
        indexes = [models.Index(fields=["start_date", "end_date"])]

    def is_sufficiently_simple(self, today, future) -> bool:
        if all(
            date.start_date > future or date.end_date and date.end_date < today
            for date in self.calendardate_set.all()
        ):
            if str(self):
                return True
        return False

    def allows(self, date) -> bool:
        if not self.contains(date):
            return False

        if getattr(self, f"{date:%a}".lower()):
            for calendar_date in self.calendardate_set.all():
                if not calendar_date.operation and calendar_date.contains(date):
                    return False
            return True

        for calendar_date in self.calendardate_set.all():
            if (
                calendar_date.operation
                and calendar_date.special
                and calendar_date.contains(date)
            ):
                return True

        if date in self.bank_holiday_exclusions:
            return False
        if date in self.bank_holiday_inclusions:
            return True

    def get_days(self) -> list:
        day_values = (
            self.mon,
            self.tue,
            self.wed,
            self.thu,
            self.fri,
            self.sat,
            self.sun,
        )
        return [day_keys[i] for i, value in enumerate(day_values) if value]

    def get_order(self) -> list:
        return [day_keys.index(day) for day in self.get_days()]

    def describe_for_timetable(self, today=None) -> str:
        start_date = self.start_date
        end_date = self.end_date

        for i in range(0, 6):
            if not self.allows(start_date):
                start_date += timedelta(days=1)

        if end_date:
            for calendar_date in self.calendardate_set.all():
                if (
                    not calendar_date.operation
                    and calendar_date.end_date >= end_date
                    and calendar_date.start_date <= end_date
                ):
                    # "until 30 may 2020, but not from 20 may to 30 may" - simplify to "until 19 may"
                    end_date = calendar_date.start_date - timedelta(days=1)

            for i in range(0, 6):
                if not self.allows(end_date):
                    end_date -= timedelta(days=1)

            if start_date == end_date:
                return f"{start_date:%A %-d %B %Y} only"

        description = str(self)

        if self.bank_holiday_inclusions and not self.bank_holiday_exclusions:
            description = f"{description} and bank holidays"
        elif self.bank_holiday_exclusions and not self.bank_holiday_inclusions:
            for date in self.bank_holiday_exclusions:
                if getattr(self, f"{date:%a}".lower()):
                    description = f"{description} (not bank holidays)"
                    break

        if not today:
            today = localdate()

        for cd in self.calendardate_set.all():
            if (
                cd.start_date == cd.end_date
                and cd.start_date >= today
                and cd.start_date - today < timedelta(days=21)
            ):
                if cd.operation and not getattr(self, f"{cd.start_date:%a}".lower()):
                    description = f"{description} (and {cd.start_date:%A %-d %B})"
                elif not cd.operation and getattr(self, f"{cd.start_date:%a}".lower()):
                    description = f"{description} (not {cd.start_date:%A %-d %B})"

        if self.start_date > today:
            description = f"{description} from {start_date:%A %-d %B %Y}"
        if end_date and (
            end_date - today < timedelta(days=21)
            or end_date - start_date < timedelta(days=200)
        ):
            description = f"{description} until {end_date:%A %-d %B %Y}"

        return description

    def __str__(self):
        days = self.get_days()
        if not days:
            return self.summary
        if len(days) == 1:
            days = f"{days[0]}s"
        elif (
            len(days) > 2
            and day_keys.index(days[-1]) - day_keys.index(days[0]) == len(days) - 1
        ):
            days = f"{days[0]} to {days[-1]}"
        else:
            days = f"{'s, '.join(days[:-1])}s and {days[-1]}s"

        if self.summary:
            return f"{days}, {self.summary}"

        return days


class CalendarDate(models.Model):
    calendar = models.ForeignKey(Calendar, models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    operation = models.BooleanField()
    special = models.BooleanField(default=False)
    summary = models.CharField(max_length=255, blank=True)

    contains = Calendar.contains

    class Meta:
        indexes = [
            models.Index(fields=["calendar", "operation", "special"]),
            models.Index(fields=["calendar", "start_date", "end_date", "operation"]),
        ]

    def __str__(self):
        string = str(self.start_date)
        if self.end_date != self.start_date:
            string = f"{string}–{self.end_date}"
        if not self.operation:
            string = f"not {string}"
        if self.special:
            string = f"also {string}"
        if self.summary:
            string = f"{string} ({self.summary})"
        return string


class Note(models.Model):
    code = models.CharField(max_length=16)
    text = models.CharField(max_length=255)

    def get_absolute_url(self):
        return self.trip_set.first().get_absolute_url()


class Trip(models.Model):
    route = models.ForeignKey(Route, models.CASCADE, null=True, blank=True)
    inbound = models.BooleanField(default=False)
    journey_pattern = models.CharField(max_length=100, null=True, blank=True)
    vehicle_journey_code = models.CharField(max_length=100, null=True, blank=True)
    ticket_machine_code = models.CharField(max_length=100, null=True, blank=True)
    block = models.CharField(max_length=100, null=True, blank=True)
    destination = models.ForeignKey(
        "busstops.StopPoint", models.DO_NOTHING, null=True, blank=True
    )
    headsign = models.CharField(null=True, blank=True)
    calendar = models.ForeignKey(Calendar, models.DO_NOTHING, null=True, blank=True)
    sequence = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.ManyToManyField(Note, blank=True)
    start = SecondsField()
    end = SecondsField()
    garage = models.ForeignKey("Garage", models.SET_NULL, null=True, blank=True)
    vehicle_type = models.ForeignKey(
        "VehicleType", models.SET_NULL, null=True, blank=True
    )
    operator = models.ForeignKey(
        "busstops.Operator", models.SET_NULL, null=True, blank=True
    )
    next_trip = models.OneToOneField("Trip", models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return format_timedelta(self.start, plus_one=True) or ""

    def start_datetime(self, date):
        return time_datetime(self.start, date)

    def end_datetime(self, date):
        return time_datetime(self.end, date)

    class Meta:
        indexes = [
            models.Index(fields=["route", "start", "end"]),
            models.Index(
                fields=["vehicle_journey_code"],
                condition=Q(vehicle_journey_code__isnull=False),
                name="bustimes_trip_vj_code",
            ),
            models.Index(
                fields=["ticket_machine_code"],
                condition=Q(ticket_machine_code__isnull=False),
                name="bustimes_trip_tm_code",
            ),
            models.Index(
                fields=["block"],
                condition=Q(block__isnull=False),
                name="bustimes_trip_block",
            ),
        ]

    def copy(self, start):
        difference = start - self.start
        new_trip = Trip.objects.get(id=self.id)
        times = list(new_trip.stoptime_set.all())
        new_trip.id = None
        new_trip.start += difference
        new_trip.end += difference
        new_trip.save(force_insert=True)
        for stop_time in times:
            stop_time.id = None
            if stop_time.arrival is not None:
                stop_time.arrival += difference
            if stop_time.departure is not None:
                stop_time.departure += difference
            stop_time.trip = new_trip
            stop_time.save(force_insert=True)

    def __repr__(self):
        return str(self.start)

    def get_absolute_url(self):
        return reverse("trip_detail", args=(self.id,))

    def get_trips(self):
        if self.ticket_machine_code and self.route and self.route.service_id:
            # get other parts of this trip (if the service has been split into parts)
            # see also: merge_split_trips

            code_filter = Q(ticket_machine_code=self.ticket_machine_code)
            if self.vehicle_journey_code:
                code_filter |= Q(vehicle_journey_code=self.vehicle_journey_code)

            calendar_filter = Q(calendar=self.calendar)
            if self.calendar:
                for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
                    if getattr(self.calendar, day):
                        calendar_filter |= Q(**{f"calendar__{day}": True})

            trips = (
                Trip.objects.filter(
                    Q(id=self.id)
                    | Q(
                        code_filter,
                        calendar_filter,
                        Q(start__gte=self.end) | Q(end__lte=self.start),
                        ~Q(destination_id=self.destination_id),
                        block=self.block,
                        inbound=self.inbound,
                        operator_id=self.operator_id,
                        route__service=self.route.service_id,
                    )
                )
                .order_by("start")
                .distinct("start")
            )
            no_minutes = timedelta()
            fifteen_minutes = timedelta(minutes=15)
            trips_list = []
            for trip_a, trip_b in pairwise(trips):
                if no_minutes <= trip_b.start - trip_a.end < fifteen_minutes:
                    if not trips_list:
                        trips_list.append(trip_a)
                    trips_list.append(trip_b)
                elif self in trips_list:
                    return trips_list
                else:
                    trips_list = []
            if self in trips_list:
                return trips_list
        return [self]


class StopTime(models.Model):
    id = models.BigAutoField(primary_key=True)
    trip = models.ForeignKey(Trip, models.CASCADE)
    stop_code = models.CharField(max_length=255, blank=True)
    stop = models.ForeignKey(
        "busstops.StopPoint", models.DO_NOTHING, null=True, blank=True
    )
    arrival = SecondsField(null=True, blank=True)
    departure = SecondsField(null=True, blank=True)
    sequence = models.PositiveSmallIntegerField(null=True, blank=True)
    timing_status = models.CharField(max_length=3, blank=True)
    pick_up = models.BooleanField(default=True)
    set_down = models.BooleanField(default=True)
    notes = models.ManyToManyField(Note, blank=True)

    def get_key(self):
        return self.stop_id or self.stop_code

    class Meta:
        ordering = ("id",)
        indexes = [
            models.Index(
                fields=["stop", "departure"],
                include=["trip"],
                condition=Q(pick_up=True),
                name="stoptime_stop_dep_covering",
            ),
            models.Index(
                fields=["trip", "id"],
                name="stoptime_trip_id",
            ),
        ]

    def __str__(self):
        return format_timedelta(self.arrival_or_departure())

    def __repr__(self):
        return f"<StopTime: {self.pk} {self.stop_id} {self}>"

    def arrival_or_departure(self) -> timedelta:
        if self.arrival is not None:
            return self.arrival
        return self.departure

    def departure_or_arrival(self) -> timedelta:
        if self.departure is not None:
            return self.departure
        return self.arrival

    def arrival_time(self):
        return format_timedelta(self.arrival)

    def arrival_datetime(self, date):
        if self.arrival is not None:
            return time_datetime(self.arrival, date)

    def departure_time(self):
        return format_timedelta(self.departure)

    def departure_datetime(self, date):
        if self.departure is not None:
            return time_datetime(self.departure, date)

    def is_minor(self):
        return self.timing_status and self.timing_status != "PTP"


class Garage(models.Model):
    operator = models.ForeignKey(
        "busstops.Operator", models.SET_NULL, null=True, blank=True
    )
    code = models.CharField(max_length=50, blank=True)
    name = models.CharField(max_length=100, blank=True)
    location = models.PointField(null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)

    def __str__(self):
        if self.name != self.code:
            if self.name.isupper():
                return self.name.title()
            return self.name
        return self.code


class VehicleType(models.Model):
    code = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.code
