from django.contrib.gis.db import models
from django.utils import timezone

from busstops.models import StopPoint
from bustimes.fields import SecondsField


class BaseVersion(models.Model):
    """A fortnightly iBus static data drop, e.g. 20260717"""

    version = models.PositiveIntegerField(primary_key=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    def __str__(self):
        return str(self.version)


class Operator(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=100)
    agency = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "code"], name="tfl_operator_unique"
            )
        ]

    def __str__(self):
        return self.name


class Line(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    contract_line_no = models.CharField(max_length=10)
    service_line_no = models.CharField(max_length=10)
    logical_line_no = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "contract_line_no"], name="tfl_line_unique"
            )
        ]

    def __str__(self):
        return self.service_line_no


class Destination(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    idx = models.PositiveIntegerField()
    long_name = models.CharField(max_length=100)
    short_name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "idx"], name="tfl_destination_unique"
            )
        ]

    def __str__(self):
        return self.short_name


class Garage(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    number = models.PositiveIntegerField()
    operator_code = models.CharField(max_length=10)
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "number"], name="tfl_garage_unique"
            )
        ]

    def __str__(self):
        return self.name


class Vehicle(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    vehicle_id = models.PositiveIntegerField()
    registration_number = models.CharField(max_length=20, blank=True)
    bonnet_no = models.CharField(max_length=20, blank=True)
    operator_agency = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "vehicle_id"], name="tfl_vehicle_unique"
            )
        ]

    def __str__(self):
        return self.registration_number


class Stop(models.Model):
    """A Stop_Point from the iBus data.

    Despite the name, naptan_code lines up with busstops.StopPoint.atco_code,
    not .naptan_code (confirmed against real data - matching on .naptan_code
    instead gives a nonsense many-to-many blow-up, since it isn't reliably
    unique/populated there).
    """

    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    idx = models.PositiveIntegerField()
    naptan_code = models.CharField(max_length=20, blank=True, db_index=True)
    stop_code_lbsl = models.CharField(max_length=20, blank=True)
    name = models.CharField(max_length=100)
    latlong = models.PointField(null=True, blank=True)
    point_letter = models.CharField(max_length=5, blank=True)
    sms_code = models.CharField(max_length=10, blank=True)
    stop_area = models.CharField(max_length=20, blank=True)
    borough_code = models.CharField(max_length=10, blank=True)
    heading = models.PositiveSmallIntegerField(null=True, blank=True)
    stop_type = models.CharField(max_length=10, blank=True)
    street_name = models.CharField(max_length=100, blank=True)
    post_code = models.CharField(max_length=10, blank=True)
    towards = models.CharField(max_length=100, blank=True)

    stop_point = models.ForeignObject(
        StopPoint,
        models.DO_NOTHING,
        from_fields=["naptan_code"],
        to_fields=["atco_code"],
        null=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "idx"], name="tfl_stop_unique"
            )
        ]

    def __str__(self):
        return self.name


class Block(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    idx = models.PositiveIntegerField()
    operator_code = models.CharField(max_length=10)
    block_no = models.PositiveIntegerField()
    running_no = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "idx"], name="tfl_block_unique"
            )
        ]


class BlockCalendarDay(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    block_idx = models.PositiveIntegerField()
    calendar_day = models.DateField()
    runs = models.BooleanField()

    class Meta:
        indexes = [models.Index(fields=["base_version", "block_idx"])]


class Pattern(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    idx = models.PositiveIntegerField()
    contract_line_no = models.CharField(max_length=10)
    direction = models.PositiveSmallIntegerField()
    type = models.PositiveSmallIntegerField()

    line = models.ForeignObject(
        Line,
        models.DO_NOTHING,
        from_fields=["base_version", "contract_line_no"],
        to_fields=["base_version", "contract_line_no"],
        null=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "idx"], name="tfl_pattern_unique"
            )
        ]
        indexes = [models.Index(fields=["base_version", "contract_line_no"])]


class StopInPattern(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    idx = models.PositiveIntegerField()
    pattern_idx = models.PositiveIntegerField()
    destination_idx = models.PositiveIntegerField(null=True, blank=True)
    stop_idx = models.PositiveIntegerField()
    sequence_no = models.PositiveSmallIntegerField()
    timing_point_code = models.CharField(max_length=10, blank=True)

    pattern = models.ForeignObject(
        Pattern,
        models.DO_NOTHING,
        from_fields=["base_version", "pattern_idx"],
        to_fields=["base_version", "idx"],
        null=True,
        related_name="+",
    )
    stop = models.ForeignObject(
        Stop,
        models.DO_NOTHING,
        from_fields=["base_version", "stop_idx"],
        to_fields=["base_version", "idx"],
        null=True,
        related_name="+",
    )
    destination = models.ForeignObject(
        Destination,
        models.DO_NOTHING,
        from_fields=["base_version", "destination_idx"],
        to_fields=["base_version", "idx"],
        null=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "idx"], name="tfl_stopinpattern_unique"
            )
        ]
        indexes = [models.Index(fields=["base_version", "pattern_idx"])]


class Journey(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    idx = models.PositiveIntegerField()
    pattern_idx = models.PositiveIntegerField()
    block_idx = models.PositiveIntegerField()
    trip_no_lbsl = models.PositiveSmallIntegerField()
    type = models.PositiveSmallIntegerField()
    start_time = SecondsField()  # time past midnight, can exceed 24 hours

    pattern = models.ForeignObject(
        Pattern,
        models.DO_NOTHING,
        from_fields=["base_version", "pattern_idx"],
        to_fields=["base_version", "idx"],
        null=True,
        related_name="+",
    )
    block = models.ForeignObject(
        Block,
        models.DO_NOTHING,
        from_fields=["base_version", "block_idx"],
        to_fields=["base_version", "idx"],
        null=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["base_version", "idx"], name="tfl_journey_unique"
            )
        ]
        indexes = [
            models.Index(fields=["base_version", "block_idx"]),
            models.Index(fields=["base_version", "pattern_idx"]),
        ]

    def matches(self, line_name, departure):
        """Journey idxs are reused between base versions, so check this is
        the same journey the AVL means (requires select_related("pattern__line"))
        """
        line = self.pattern and self.pattern.line
        if not line or line.service_line_no.lower() != line_name.lower():
            return False
        if departure:
            departure = timezone.localtime(departure)
            seconds = departure.hour * 3600 + departure.minute * 60
            return self.start_time.total_seconds() % 86400 == seconds
        return True


class JourneyDriveTime(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    journey_idx = models.PositiveIntegerField()
    stop_in_pattern_from_idx = models.PositiveIntegerField()
    stop_in_pattern_to_idx = models.PositiveIntegerField()
    drive_time = SecondsField()

    class Meta:
        indexes = [models.Index(fields=["base_version", "journey_idx"])]


class JourneyWaitTime(models.Model):
    base_version = models.ForeignKey(BaseVersion, models.CASCADE, db_index=False)
    journey_idx = models.PositiveIntegerField()
    stop_in_pattern_idx = models.PositiveIntegerField()
    wait_time = SecondsField()

    class Meta:
        indexes = [models.Index(fields=["base_version", "journey_idx"])]
