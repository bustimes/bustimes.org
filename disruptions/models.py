from datetime import timedelta
from itertools import pairwise

from django.contrib.postgres.fields import DateTimeRangeField
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import camel_case_to_spaces

from busstops.templatetags.date_range import date_range


def from_now():
    return [timezone.now()]


def time_range(lower, upper):
    return f"{lower.strftime('%H:%M')}\u2009\u2013\u2009{upper.strftime('%H:%M')}"


class Situation(models.Model):
    source = models.ForeignKey(
        "busstops.DataSource",
        models.DB_CASCADE,
        limit_choices_to={
            "name__in": (
                "bustimes.org",
                "TfL",
                "TfL disruptions",
                "TfL statuses",
                "BODS disruptions",
                "BODS cancellations",
                "Bus Open Data",
                "Translink",
            )
        },
        default=236,
    )
    situation_number = models.CharField(max_length=36, blank=True)
    reason = models.CharField(max_length=25, blank=True)
    severity = models.CharField(max_length=25, blank=True)
    summary = models.CharField(max_length=255, blank=True, help_text="(title)")
    show_summary = models.BooleanField(default=True)
    participant_ref = models.CharField(max_length=36, blank=True)
    text = models.TextField(blank=True)
    data = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    modified_at = models.DateTimeField(default=timezone.now, null=True)
    publication_window = DateTimeRangeField(default=from_now)
    current = models.BooleanField(default=True)

    def __str__(self):
        return self.summary or self.text or self.situation_number or super().__str__()

    def nice_reason(self):
        return camel_case_to_spaces(self.reason)

    def get_absolute_url(self):
        return reverse("situation", args=(self.id,))

    class Meta:
        indexes = (
            models.Index(fields=["current", "publication_window"]),
            models.Index(fields=["source", "situation_number"]),
        )

    def applies_on(self, date) -> bool:
        """Whether this situation is in effect on the given (local) date.

        If there are no validity periods, assume it applies — the
        publication window has already been checked when querying.
        """
        periods = self.validityperiod_set.all()
        if not periods:
            return True
        for period in periods:
            lower = period.period.lower
            upper = period.period.upper
            if (lower is None or timezone.localtime(lower).date() <= date) and (
                upper is None or timezone.localtime(upper).date() >= date
            ):
                return True
        return False

    def list_validity_periods(self) -> list[str]:
        validity_periods = list(self.validityperiod_set.all())
        if not validity_periods:
            return []
        current_timezone = timezone.get_current_timezone()
        validity_periods.sort(key=lambda p: p.period)
        periods = [
            [
                period.period.lower
                and period.period.lower.astimezone(current_timezone),
                period.period.upper
                and period.period.upper.astimezone(current_timezone),
            ]
            for period in validity_periods
        ]

        # merge contiguous periods
        # (e.g. one ends at 23:59 and the next starts at 00:00, 1 minute later)
        merged = periods[:1]
        for prev, curr in pairwise(periods):
            if prev[1] and curr[0] and curr[0] - prev[1] == timedelta(minutes=1):
                merged[-1][1] = curr[1]
            else:
                merged.append(curr)
        periods = merged

        if len(periods) == 1:
            lower, upper = periods[0]
            if upper and lower:
                if upper.date() == lower.date():
                    return [
                        f"""{time_range(lower, upper)}, {date_range(lower=lower, upper=upper)}"""
                    ]
                return [date_range(lower=lower, upper=upper)]

        now = timezone.now()
        if current_periods := [
            (lower, upper)
            for (lower, upper) in periods
            if upper is None or upper >= now
        ]:
            # filter out past periods for brevity
            periods = current_periods

        # Group consecutive periods with matching start/end times into runs
        runs = [[periods[0]]]
        for prev, curr in pairwise(periods):
            if (
                prev[0]
                and curr[0]
                and prev[1]
                and curr[1]
                and curr[0].date() - prev[0].date() == timedelta(days=1)
                and curr[0].time() == prev[0].time()
                and curr[1].time() == prev[1].time()
            ):
                runs[-1].append(curr)
            else:
                runs.append([curr])

        result = []
        for run in runs:
            first = run[0]
            last = run[-1]
            if first[0] and first[1] and last[1]:
                result.append(
                    f"""{time_range(*first)}, {date_range(lower=first[0], upper=last[1])}"""
                )
            else:
                return [date_range(lower=first[0], upper=last[1])]
        return result


class Link(models.Model):
    url = models.URLField()
    situation = models.ForeignKey(Situation, models.DB_CASCADE)

    def __str__(self):
        return self.url

    get_absolute_url = __str__


class ValidityPeriod(models.Model):
    situation = models.ForeignKey(Situation, models.DB_CASCADE)
    period = DateTimeRangeField()

    def __str__(self):
        return date_range(self.period)


class Consequence(models.Model):
    situation = models.ForeignKey(Situation, models.DB_CASCADE)
    stops = models.ManyToManyField(
        "busstops.StopPoint", blank=True, through="ConsequenceStop"
    )
    services = models.ManyToManyField(
        "busstops.Service", blank=True, through="ConsequenceService"
    )
    operators = models.ManyToManyField(
        "busstops.Operator", blank=True, through="ConsequenceOperator"
    )
    text = models.TextField(blank=True)
    data = models.TextField(blank=True)

    def __str__(self):
        return self.text

    def get_absolute_url(self):
        if service := self.services.first():
            return service.get_absolute_url()
        return ""


class ConsequenceStop(models.Model):
    # StopPoint is deleted by Python; the ON DELETE CASCADE comes from the migration
    consequence = models.ForeignKey(
        Consequence, models.DB_CASCADE, related_name="consequencestop+"
    )
    stoppoint = models.ForeignKey(
        "busstops.StopPoint", models.DO_NOTHING, related_name="consequencestop+"
    )

    class Meta:
        db_table = "disruptions_consequence_stops"
        unique_together = ("consequence", "stoppoint")


class ConsequenceService(models.Model):
    # as above
    consequence = models.ForeignKey(
        Consequence, models.DB_CASCADE, related_name="consequenceservice+"
    )
    service = models.ForeignKey(
        "busstops.Service", models.DO_NOTHING, related_name="consequenceservice+"
    )

    class Meta:
        db_table = "disruptions_consequence_services"
        unique_together = ("consequence", "service")


class ConsequenceOperator(models.Model):
    consequence = models.ForeignKey(
        Consequence, models.DB_CASCADE, related_name="consequenceoperator+"
    )
    operator = models.ForeignKey(
        "busstops.Operator", models.DB_CASCADE, related_name="consequenceoperator+"
    )

    class Meta:
        db_table = "disruptions_consequence_operators"
        unique_together = ("consequence", "operator")


class AffectedJourney(models.Model):
    situation = models.ForeignKey(Situation, models.DB_CASCADE)
    trip = models.ForeignKey("bustimes.Trip", models.DB_CASCADE)
    date = models.DateField(null=True, blank=True)
    origin_departure_time = models.DateTimeField(null=True, blank=True)
    condition = models.CharField()  # cancelled, altered, etc

    def __str__(self):
        return f"{self.origin_departure_time} {self.condition}"


class Call(models.Model):
    journey = models.ForeignKey(AffectedJourney, models.DB_CASCADE)
    stop_time = models.ForeignKey("bustimes.StopTime", models.DB_CASCADE)
    arrival_time = models.DateTimeField(null=True, blank=True)
    departure_time = models.DateTimeField(null=True, blank=True)
    condition = models.CharField()
    order = models.PositiveSmallIntegerField()
