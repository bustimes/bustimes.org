import datetime
import subprocess
import re
import struct
import uuid
from collections import Counter
from math import ceil
from urllib.parse import quote

from django.conf import settings
from django.contrib.gis.db import models
from django.db.models import Q, UniqueConstraint
from django.db.models.functions import Upper
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from simple_history.models import HistoricalRecords
from webcolors import HTML5SimpleColor, html5_parse_legacy_color

from busstops.fields import AutoSlugField
from busstops.models import DataSource, Operator, Service
from bustimes.utils import get_trip
from .fields import ColourField, ColoursField, CSSField


def format_reg(reg):
    if "-" not in reg:
        if reg[-3:].isalpha():
            return reg[:-3] + " " + reg[-3:]
        if reg[:3].isalpha():
            return reg[:3] + " " + reg[3:]
        if reg[-2:].isalpha():
            return reg[:-2] + " " + reg[-2:]
        if reg[:2].isalpha():
            return reg[:2] + " " + reg[2:]

    return reg


def get_css(colours, direction=None, horizontal=False, angle=None):
    if angle is None:
        angle = 90
    if len(colours) == 1:
        return colours[0]
    if direction is None:
        direction = 180
    else:
        direction = int(direction)
    background = "linear-gradient("
    if horizontal:
        background += "to top"
    elif direction < 180:
        background += f"{360 - angle}deg"
    else:
        background += f"{angle}deg"
    percentage = 100 / len(colours)
    for i, colour in enumerate(colours):
        if i != 0 and colour != colours[i - 1]:
            background += ",{} {}%".format(colour, ceil(percentage * i))
        if i != len(colours) - 1 and colour != colours[i + 1]:
            background += ",{} {}%".format(colour, ceil(percentage * (i + 1)))
    background += ")"

    return background


def get_brightness(colour: HTML5SimpleColor) -> float:
    """Returns a "relative luminance" between 0 and 255"""
    return 0.299 * colour.red + 0.587 * colour.green + 0.114 * colour.blue


def get_text_colour(colours) -> str:
    """Returns "#fff" if the colour is dark, otherwise None"""
    if not colours:
        return
    colours = colours.split()
    colours = [html5_parse_legacy_color(colour) for colour in colours]
    brightnesses = [get_brightness(colour) for colour in colours]
    colours_length = len(colours)
    if colours_length > 2:
        # ignore the leftmost and rightmost strips
        brightnesses = brightnesses[1:-1]
        colours_length -= 2
    if (sum(brightnesses) / colours_length) <= 186:
        return "#fff"


class VehicleTypeType(models.TextChoices):
    SINGLE_DECKER = "", "single decker"
    DOUBLE_DECKER = "double decker", "double decker"
    MINIBUS = "minibus", "minibus"
    COACH = "coach", "coach"
    DOUBLE_DECK_COACH = "decker coach", "double decker coach"
    ARTICULATED = "articulated", "bendy bus"
    TRAIN = "train", "train"
    TRAM = "tram", "tram"
    AMPHIBIOUS = "amphibious", "amphibious"


class FuelType(models.TextChoices):
    DIESEL = "diesel", "diesel"
    ELECTRIC = "electric", "electric"
    HYBRID = "hybrid", "hybrid"
    HYDROGEN = "hydrogen", "hydrogen"
    GAS = "gas", "gas"  # (compressed natural)


class VehicleType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    style = models.CharField(choices=VehicleTypeType.choices, max_length=13, blank=True)
    fuel = models.CharField(choices=FuelType.choices, max_length=8, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class Livery(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    show_name = models.BooleanField(default=True, help_text="If branding is present, show livery name?")
    colour = ColourField(
        max_length=7, help_text="For the most simplified version of the livery"
    )
    colours = ColoursField(
        max_length=512,
        blank=True,
        help_text="""Left and right CSS will be generated from this""",
    )
    angle = models.PositiveSmallIntegerField(null=True, blank=True)
    left_css = CSSField(
        max_length=1024,
        blank=True,
        verbose_name="Left CSS",
        help_text="Automatically generated from colours and angle",
    )
    right_css = CSSField(
        max_length=1024,
        blank=True,
        verbose_name="Right CSS",
        help_text="Should be a mirror image of the left CSS",
    )
    white_text = models.BooleanField(default=False)
    text_colour = ColourField(max_length=7, blank=True)
    stroke_colour = ColourField(
        max_length=7, blank=True, help_text="Use sparingly, often looks shit"
    )
    horizontal = models.BooleanField(
        default=False, help_text="Equivalent to setting the angle to 90"
    )
    updated_at = models.DateTimeField(null=True, blank=True, auto_now=True)
    published = models.BooleanField(
        default=False,
        help_text="Tick to include in the CSS and be able to apply this livery to vehicles",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "liveries"

    def __str__(self):
        return self.name

    @staticmethod
    def minify(css):
        prefix = ".livery{background:"
        suffix = "}"
        css = prefix + css + suffix
        completed_process = subprocess.run(
            ["lightningcss", "--minify"], input=css.encode(), capture_output=True
        )
        css = completed_process.stdout.decode().strip()
        assert css.startswith(prefix)
        assert css.endswith(suffix)
        return css[19:-1]

    def set_css(self):
        if self.colours:
            self.left_css = get_css(
                self.colours.split(), None, self.horizontal, self.angle
            )
            self.right_css = get_css(
                self.colours.split(), 90, self.horizontal, self.angle
            )

    def preview(self, name=False):
        if self.left_css:
            background = escape(self.left_css)
        elif self.colours:
            background = get_css(self.colours.split())
        elif name:
            background = ""
        else:
            return

        div = f'<div style="height:1.5em;width:2.25em;background:{background}"'
        if name:
            return format_html(div + "></div> {}", self.name)
        else:
            return format_html(div + ' title="{}"></div>', self.name)

    def save(self, *args, update_fields=None, **kwargs):
        if update_fields is None:
            if self.colours:
                self.set_css()
                if self.colours and not self.id:
                    self.white_text = get_text_colour(self.colours) == "#fff"
            if self.right_css:
                self.right_css = self.minify(self.right_css)
                self.left_css = self.minify(self.left_css)
        super().save(*args, update_fields=update_fields, **kwargs)

    def get_styles(self, livery_ids=None):
        if not self.left_css:
            return []
        if not livery_ids:
            livery_ids = (self.id,)
        selector = ",".join(f".livery-{livery_id}" for livery_id in livery_ids)
        css = f"  background: {self.left_css}"
        if self.text_colour:
            css = f"{css};\n  color: {self.text_colour}"
        elif self.white_text:
            css = f"{css};\n  color: #fff"
        if self.stroke_colour:
            css = f"{css};\n  stroke: {self.stroke_colour}"
        if self.right_css != self.left_css:
            css += f";\n  &.right{{\n    background:{self.right_css}\n  }}"
        return [f"{selector}{{\n{css}\n}}\n"]


class VehicleFeature(models.Model):
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ("name",)


def vehicle_slug(vehicle):
    return f"{vehicle.operator_id} {vehicle.code.replace('_', ' ')}"


class Vehicle(models.Model):
    slug = AutoSlugField(populate_from=vehicle_slug, editable=True, unique=True)
    code = models.CharField(max_length=255)
    fleet_number = models.PositiveIntegerField(null=True, blank=True)
    fleet_code = models.CharField(max_length=24, blank=True, db_collation="en_numeric")
    reg = models.CharField(max_length=24, blank=True)
    source = models.ForeignKey(DataSource, models.SET_NULL, null=True, blank=True)
    operator = models.ForeignKey(Operator, models.SET_NULL, null=True, blank=True)
    vehicle_type = models.ForeignKey(
        VehicleType, models.SET_NULL, null=True, blank=True
    )
    colours = ColoursField(max_length=255, blank=True)
    livery = models.ForeignKey(Livery, models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=255, blank=True)
    branding = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    latest_journey = models.OneToOneField(
        "VehicleJourney",
        models.SET_NULL,
        null=True,
        blank=True,
        related_name="latest_vehicle",
    )
    latest_journey_data = models.JSONField(null=True, blank=True)
    features = models.ManyToManyField(VehicleFeature, blank=True)
    withdrawn = models.BooleanField(default=False)
    data = models.JSONField(null=True, blank=True)
    garage = models.ForeignKey(
        "bustimes.Garage", models.SET_NULL, null=True, blank=True
    )
    locked = models.BooleanField(default=False)

    def is_spare_ticket_machine(self) -> bool:
        return self.notes == "Spare ticket machine"

    def is_editable(self) -> bool:
        if self.locked:
            return False
        # withrawn and hasn't tracked recently - "let sleeping dogs lie"
        if self.withdrawn and (
            not self.latest_journey
            or timezone.now() - self.latest_journey.datetime
            > datetime.timedelta(days=30)
        ):
            return False
        return True

    def save(self, *args, update_fields=None, **kwargs):
        if (
            update_fields is None or "fleet_number" in update_fields
        ) and self.fleet_number:
            if not self.fleet_code or (
                self.fleet_code.isdigit() and self.fleet_number != int(self.fleet_code)
            ):
                self.fleet_code = str(self.fleet_number)
                if update_fields is not None and "fleet_code" not in update_fields:
                    update_fields.append("fleet_code")

        if (update_fields is None or "fleet_code" in update_fields) and self.fleet_code:
            if not self.fleet_number and self.fleet_code.isdigit():
                self.fleet_number = int(self.fleet_code)
                if update_fields is not None and "fleet_number" not in update_fields:
                    update_fields.append("fleet_number")

        if update_fields is None and not self.reg:
            reg = re.match(r"^[A-Z]\w_?\d\d?[ _-]?[A-Z]{3}$", self.code)
            if reg:
                self.reg = re.sub("[-_ ]", "", self.code)
        elif update_fields is None or "reg" in update_fields:
            self.reg = self.reg.upper().replace(" ", "")

        super().save(*args, update_fields=update_fields, **kwargs)

    class Meta:
        indexes = [
            models.Index(Upper("fleet_code"), name="fleet_code"),
            models.Index(Upper("reg"), name="reg"),
            models.Index(fields=["operator", "withdrawn"], name="operator_withdrawn"),
        ]
        constraints = [
            models.UniqueConstraint(
                Upper("code"), "operator", name="vehicle_operator_and_code"
            ),
        ]

    def __str__(self):
        fleet_code = self.fleet_code or self.fleet_number
        if self.reg:
            if fleet_code:
                return f"{fleet_code} - {self.get_reg()}"
            return self.get_reg()
        if fleet_code:
            return str(fleet_code)
        return self.code.replace("_", " ")

    def get_next(self, order=""):
        lookup = "lt" if order == "-" else "gt"
        if self.operator:
            filter = {}
            if self.fleet_number:
                filter[f"fleet_number__{lookup}"] = self.fleet_number
                order_by = f"{order}fleet_number"
            elif self.fleet_code:
                filter[f"fleet_code__{lookup}"] = self.fleet_code
                order_by = f"{order}fleet_code"
            else:
                filter[f"code__{lookup}"] = self.code
                order_by = f"{order}code"

            return (
                self.operator.vehicle_set.filter(
                    **filter,
                    withdrawn=False,
                )
                .order_by(order_by)
                .first()
            )

    def get_previous(self):
        return self.get_next(order="-")

    def get_reg(self):
        return format_reg(self.reg)

    def data_get(self, key=None):
        if not key:
            if self.data:
                return [(key, self.data_get(key)) for key in self.data]
            return ()
        if self.data:
            value = self.data.get(key)
            if value:
                if key == "Previous reg":
                    return ", ".join(format_reg(reg) for reg in value.split(","))
                return value
        return ""

    def get_text_colour(self):
        if self.livery:
            if self.livery.white_text:
                return "#fff"
        elif self.colours:
            return get_text_colour(self.colours)

    def get_livery(self, direction=None):
        if self.livery:
            if direction is not None and direction < 180:
                return escape(self.livery.right_css)
            return escape(self.livery.left_css)

        colours = self.colours
        if colours and colours != "Other":
            colours = colours.split()
            if len(colours) > 1:
                self.colour = Counter(colours).most_common()[0][0]
            return get_css(colours, direction, self.livery and self.livery.horizontal)

    def get_absolute_url(self):
        return reverse("vehicle_detail", args=(self.slug or self.id,))

    def get_edit_url(self):
        return reverse("vehicle_edit", args=(self.slug or self.id,))

    def get_flickr_url(self):
        if self.reg:
            reg = self.get_reg()
            search = f'{self.reg} or "{reg}"'
            return f"https://www.flickr.com/search/?text={quote(search)}&sort=date-taken-desc"

    def get_flickr_link(self):
        if url := self.get_flickr_url():
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">Flickr</a>', url
            )
        return ""

    get_flickr_link.short_description = "Flickr"

    def get_json(self):
        json = {
            "url": self.get_absolute_url(),
            "name": str(self),
        }

        features = self.feature_names
        if self.vehicle_type:
            vehicle_type = self.vehicle_type.style.capitalize()
            if vehicle_type:
                if features:
                    features = f"{vehicle_type}<br>{features}"
                else:
                    features = vehicle_type
        if features:
            json["features"] = features

        if self.livery_id:
            json["livery"] = self.livery_id
        elif self.colours:
            json["css"] = self.get_livery()
            json["right_css"] = self.get_livery(90)
            json["text_colour"] = self.get_text_colour()
        if colour := getattr(self, "colour", None):
            json["colour"] = colour
        return json


class VehicleCode(models.Model):
    code = models.CharField(max_length=100)
    scheme = models.CharField(max_length=24)
    vehicle = models.ForeignKey(Vehicle, models.CASCADE)

    def __str__(self):
        return f"{self.scheme} {self.code}"

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["code", "scheme"],
                name="unique_vehicle_code",
            ),
        ]
        indexes = [models.Index(fields=("code", "scheme"))]


class VehicleRevisionFeature(models.Model):
    feature = models.ForeignKey(VehicleFeature, models.CASCADE)
    revision = models.ForeignKey("VehicleRevision", models.CASCADE)
    add = models.BooleanField(default=True)

    def __str__(self):
        if self.add:
            fmt = "<ins>{}</ins>"
        else:
            fmt = "<del>{}</del>"
        return format_html(fmt, self.feature)


class VehicleRevision(models.Model):
    vehicle = models.ForeignKey(Vehicle, models.CASCADE)

    from_operator = models.ForeignKey(
        Operator, models.SET_NULL, null=True, blank=True, related_name="revision_from"
    )
    to_operator = models.ForeignKey(
        Operator, models.SET_NULL, null=True, blank=True, related_name="revision_to"
    )
    from_type = models.ForeignKey(
        VehicleType,
        models.SET_NULL,
        null=True,
        blank=True,
        related_name="revision_from",
    )
    to_type = models.ForeignKey(
        VehicleType, models.SET_NULL, null=True, blank=True, related_name="revision_to"
    )
    from_livery = models.ForeignKey(
        Livery, models.SET_NULL, null=True, blank=True, related_name="revision_from"
    )
    to_livery = models.ForeignKey(
        Livery, models.SET_NULL, null=True, blank=True, related_name="revision_to"
    )

    features = models.ManyToManyField(
        VehicleFeature, blank=True, through=VehicleRevisionFeature
    )

    changes = models.JSONField(null=True, blank=True)
    message = models.TextField(null=True, blank=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.SET_NULL, null=True, blank=True
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved",
    )
    created_at = models.DateTimeField()
    approved_at = models.DateTimeField(null=True, blank=True)

    pending = models.BooleanField(default=False)
    disapproved = models.BooleanField(default=False)
    disapproved_reason = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["vehicle", "to_operator"],
                condition=Q(pending=True),
                name="unique_pending_operator",
            ),
            UniqueConstraint(
                fields=["vehicle", "to_type"],
                condition=Q(pending=True),
                name="unique_pending_type",
            ),
            UniqueConstraint(
                fields=["vehicle", "to_livery"],
                condition=Q(pending=True),
                name="unique_pending_livery",
            ),
        ]

    def __str__(self):
        return ", ".join(
            f"{key}: {before} → {after}"
            for key, before, after in self.list_changes(html=False)
        )

    def list_changes(self, html=True):
        for field in ("operator", "type", "livery"):
            if getattr(self, f"from_{field}_id") or getattr(self, f"to_{field}_id"):
                if getattr(__class__, f"from_{field}").is_cached(self):
                    before = getattr(self, f"from_{field}")
                    after = getattr(self, f"to_{field}")

                    if field == "livery":
                        if before:
                            before = format_html(
                                '<span class="livery" style="background:{}"></span>{}',
                                before.left_css,
                                before.name,
                            )
                        if after:
                            after = format_html(
                                '<span class="livery" style="background:{}"></span>{}',
                                after.left_css,
                                after.name,
                            )
                else:
                    before = getattr(self, f"from_{field}_id")
                    after = getattr(self, f"to_{field}_id")
                yield (field, before, after)
        if self.changes:
            for key in self.changes:
                before, after = self.changes[key].split("\n+")
                before = before[1:]
                if key == "colours" and html:
                    if before and before != "Other":
                        before = format_html(
                            '<span class="livery" style="background:{}"></span>',
                            get_css(before.split()),
                        )
                    if after and after != "Other":
                        after = format_html(
                            '<span class="livery" style="background:{}"></span>',
                            get_css(after.split()),
                        )
                if key == "withdrawn":
                    if after == "Yes":
                        yield ("removed from list", "", "")
                    else:
                        yield ("added to list", "", "")
                else:
                    yield (key, before, after)

    def revert(self):
        """Revert various values to how they were before the revision"""
        vehicle = self.vehicle
        fields = []

        for key, vehicle_key in (
            ("operator", "operator"),
            ("type", "vehicle_type"),
            ("livery", "livery"),
        ):
            before = getattr(self, f"from_{key}_id")
            after = getattr(self, f"to_{key}_id")
            if before or after:
                if getattr(vehicle, f"{vehicle_key}_id") == after:
                    setattr(vehicle, f"{vehicle_key}_id", before)
                    fields.append(vehicle_key)

        if self.changes:
            for key in self.changes:
                before, after = self.changes[key].split("\n+")
                before = before[1:]
                if key == "reg" or key == "name":
                    if getattr(vehicle, key) == after:
                        setattr(vehicle, key, before)
                        fields.append("reg")
                elif key == "withdrawn":
                    if vehicle.withdrawn and after == "Yes":
                        vehicle.withdrawn = False
                        fields.append("withdrawn")
                elif key == "fleet number":
                    vehicle.fleet_code = before
                    if before.isdigit():
                        vehicle.fleet_number = int(vehicle.fleet_number)
                    else:
                        vehicle.fleet_number = None
                    fields += ["fleet_number", "fleet_code"]
                else:
                    yield f"vehicle {vehicle.id} {key} not reverted"

        if fields:
            self.vehicle.save(update_fields=fields)
            yield f"vehicle {vehicle.id} reverted {fields}"


class VehicleJourney(models.Model):
    datetime = models.DateTimeField()
    date = models.DateField()
    service = models.ForeignKey(
        Service, models.SET_NULL, null=True, blank=True, db_index=False
    )
    route_name = models.CharField(max_length=64, blank=True)
    source = models.ForeignKey(DataSource, models.CASCADE)
    vehicle = models.ForeignKey(
        Vehicle, models.CASCADE, null=True, blank=True, db_index=False
    )
    code = models.CharField(max_length=255, blank=True)
    destination = models.CharField(max_length=255, blank=True)
    direction = models.CharField(max_length=13, blank=True)
    trip = models.ForeignKey(
        "bustimes.Trip", models.SET_NULL, null=True, blank=True, db_index=False
    )
    # trip_matched = models.BooleanField(default=True)
    # block = models.ForeignKey("bustimes.Block", models.SET_NULL, null=True, blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)

    def get_absolute_url(self):
        # TODO: change to "/journeys/{self.id}" (actually using `reverse()`)
        return f"/vehicles/{self.vehicle_id}?date={self.date}#journey-{self.id}"

    def __str__(self):
        when = f"{self.datetime:%-d %b %y %H:%M} {self.route_name} {self.code} {self.direction}"
        if self.destination:
            when = f"{when} to {self.destination}"
        return when

    class Meta:
        ordering = ("id",)
        indexes = [
            models.Index("service", "date", name="vehiclejourney_service_date"),
            models.Index(
                "vehicle",
                "date",
                name="vehiclejourney_vehicle_date",
                condition=Q(vehicle__isnull=False),
            ),
            models.Index(
                "trip",
                "date",
                name="vehiclejourney_trip_date",
                condition=Q(trip__isnull=False),
            ),
            models.Index(
                "route_name",
                "date",
                name="route_name__date",
                condition=Q(service__isnull=True),
            ),
            models.Index(fields=["service", "-id"], name="vehiclejourney_service_id"),
            models.Index(
                fields=["vehicle", "-id"], name="vehiclejourney_vehicle_id_desc"
            ),
        ]

    def get_redis_key(self):
        return self.uuid.bytes

    get_trip = get_trip

    def get_trip_block_url(self):
        url = reverse("block_detail", args=(self.trip_id,))
        return f"{url}?date={self.date}"

    def get_service_link(self):
        if self.service:
            slug = self.service.slug
        else:
            slug = f"{self.vehicle.operator_id}:{self.route_name}"
        return (
            reverse("service_vehicles", args=(slug,)) + "?date=" + self.date.isoformat()
        )


class VehicleLocation:
    """This used to be a model,
    is no longer stored in the database
    but this code is still here for historical reasons
    """

    def __init__(self, latlong, heading=None, delay=None, occupancy=None, block=None):
        self.latlong = latlong
        self.heading = heading
        self.delay = delay
        self.occupancy = occupancy
        self.seated_occupancy = None
        self.seated_capacity = None
        self.wheelchair_occupancy = None
        self.wheelchair_capacity = None
        self.occupancy_thresholds = None
        self.block = block
        self.tfl_code = None

    def __str__(self):
        return f"{self.datetime:%-d %b %Y %H:%M:%S}"

    class Meta:
        ordering = ("id",)

    def get_appendage(self):
        delay = self.delay
        if delay is not None:
            delay = round(delay.total_seconds() / 60)

        if self.heading is None or type(self.heading) is int:
            heading = self.heading
        elif type(self.heading) is str:
            if self.heading.isdigit():
                heading = int(self.heading)
            elif self.heading:
                heading = round(float(self.heading))
            else:
                heading = None
        else:
            heading = round(self.heading)

        return self.journey.get_redis_key(), struct.pack(
            "I 2f ?h ?h",
            round(self.datetime.timestamp()),
            self.latlong.x,
            self.latlong.y,
            heading is not None,
            heading or 0,
            delay is not None,
            delay or 0,
        )

    @staticmethod
    def decode_appendage(location):
        location = struct.unpack("I 2f ?h ?h", location)
        return {
            "id": location[0],
            "coordinates": location[1:3],
            "delta": (location[5] or None) and location[6],
            "direction": (location[3] or None) and location[4],
            "datetime": timezone.localtime(
                datetime.datetime.fromtimestamp(location[0], datetime.timezone.utc)
            ),
        }

    def get_redis_json(self):
        journey = self.journey

        json = {
            "id": self.id,  # (same as vehicle id)
            "journey_id": journey.id,
            "coordinates": self.latlong.coords,
            "heading": self.heading,
            "datetime": timezone.localtime(self.datetime),
            "destination": journey.destination,
            "block": self.block,
        }

        if self.delay is not None:
            json["delay"] = self.delay.total_seconds()

        if self.tfl_code:
            json["tfl_code"] = self.tfl_code
        if journey.trip_id:
            json["date"] = journey.date
            json["trip_id"] = journey.trip_id
        if journey.service_id:
            json["service_id"] = journey.service_id
        if journey.route_name:
            json["service"] = {"line_name": journey.route_name}

        if self.seated_occupancy is not None and self.seated_capacity is not None:
            if self.occupancy == "Full":
                json["seats"] = self.occupancy
            else:
                json["seats"] = f"{self.seated_capacity - self.seated_occupancy} free"
        elif self.occupancy:
            json["seats"] = self.occupancy
        if self.wheelchair_occupancy is not None and self.wheelchair_capacity:
            if self.wheelchair_occupancy < self.wheelchair_capacity:
                json["wheelchair"] = "free"
            else:
                json["wheelchair"] = "occupied"

        return json


class SiriSubscription(models.Model):
    name = models.CharField(
        max_length=64,
        blank=True,
        unique=True,
        help_text="There should be a DataSource with the same name as this",
    )
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    sample = models.TextField(null=True, blank=True)
    producer_url = models.URLField(null=True, blank=True, max_length=64)
    username = models.CharField(null=True, blank=True, max_length=64)
    password = models.CharField(null=True, blank=True, max_length=64)
    requestor_ref = models.CharField(null=True, blank=True, max_length=64)

    def __str__(self):
        return self.name

    def get_status_key(self):
        return f"{self.name.replace(' ', '_')}_status"

    def get_absolute_url(self):
        return reverse("siri_post", args=(self.uuid,))
