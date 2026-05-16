"Model definitions"

import datetime
import logging
import re
from urllib.parse import urlencode, urlparse

import yaml
from botocore.exceptions import NoCredentialsError
from django.contrib.gis.db import models
from django.contrib.gis.db.models import Extent
from django.contrib.gis.geos import Polygon
from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.cache import cache
from django.db.models import Q, Value
from django.db.models.aggregates import StringAgg
from django.db.models.functions import Coalesce, Concat, Upper
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from timezone_field import TimeZoneField

from bustimes.models import Route, TimetableDataSource, StopTime
from bustimes.timetables import Timetable
from bustimes.utils import get_descriptions
from .fields import AutoSlugField

TIMING_STATUS_CHOICES = (
    ("PPT", "Principal point"),
    ("TIP", "Time info point"),
    ("PTP", "Principal and time info point"),
    ("OTH", "Other bus stop"),
)
SERVICE_ORDER_REGEX = re.compile(r"(\D*)(\d*)(\D*)")


class SearchMixin:
    def update_search_vector(self):
        instance = self._meta.default_manager.with_documents().get(pk=self.pk)
        instance.search_vector = instance.document
        instance.save(update_fields=["search_vector"])

    def save(self, *args, update_fields=None, **kwargs):
        super().save(*args, update_fields=update_fields, **kwargs)
        if update_fields is None or "search_vector" not in update_fields:
            self.update_search_vector()


class Region(models.Model):
    """The largest type of geographical area"""

    id = models.CharField(max_length=2, primary_key=True)
    name = models.CharField(max_length=48)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def the(self):
        """Return the name for use in a sentence,
        with the definite article prepended if appropriate"""
        if self.name[-2:] in ("ds", "st"):
            return "the " + self.name
        else:
            return self.name

    def get_absolute_url(self):
        return reverse("region_detail", args=(self.id,))


class AdminArea(models.Model):
    """An administrative area within a region,
    or possibly a national transport (rail/air/ferry) network
    """

    id = models.PositiveSmallIntegerField(primary_key=True)
    atco_code = models.CharField(verbose_name="ATCO code", max_length=3)
    name = models.CharField(max_length=48)
    short_name = models.CharField(max_length=48, blank=True)
    country = models.CharField(max_length=3, blank=True)
    region = models.ForeignKey(Region, models.CASCADE)
    created_at = models.DateTimeField(null=True, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("adminarea_detail", args=(self.id,))


class District(models.Model):
    """A district within an administrative area.
    Note: some administrative areas *do not* have districts.
    """

    id = models.PositiveSmallIntegerField(primary_key=True)
    name = models.CharField(max_length=48)
    admin_area = models.ForeignKey(AdminArea, models.CASCADE)
    created_at = models.DateTimeField(null=True, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("district_detail", args=(self.id,))


class LocalityManager(models.Manager):
    def with_documents(self):
        vector = SearchVector("name", weight="A", config="english")
        vector += SearchVector("qualifier_name", weight="B", config="english")
        return self.get_queryset().annotate(document=vector)


class Locality(SearchMixin, models.Model):
    """A locality within an administrative area,
    and possibly within a district.

    Localities may be children of other localities...
    """

    id = models.CharField(max_length=48, primary_key=True)
    name = models.CharField(max_length=48)
    short_name = models.CharField(max_length=48, blank=True)
    qualifier_name = models.CharField(max_length=48, blank=True)
    slug = AutoSlugField(populate_from="get_qualified_name", editable=True, unique=True)
    admin_area = models.ForeignKey(AdminArea, models.CASCADE)
    district = models.ForeignKey(District, models.SET_NULL, null=True, blank=True)
    parent = models.ForeignKey("self", models.SET_NULL, null=True, blank=True)
    latlong = models.PointField(null=True, blank=True)
    adjacent = models.ManyToManyField("self", blank=True)
    search_vector = SearchVectorField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)

    objects = LocalityManager()

    class Meta:
        ordering = ("name",)
        indexes = [GinIndex(fields=["search_vector"])]

    def __str__(self):
        return self.name or self.id

    def get_qualified_name(self):
        """Return the name and qualifier (e.g. 'Reepham, Lincs')"""
        if self.qualifier_name:
            return f"{self.name}, {self.qualifier_name}"
        return str(self)

    def get_absolute_url(self):
        return reverse("locality_detail", args=(self.slug,))


class StopArea(models.Model):
    """A small area containing multiple stops, such as a bus station"""

    id = models.CharField(max_length=16, primary_key=True)
    name = models.CharField(max_length=48)
    admin_area = models.ForeignKey(AdminArea, models.CASCADE)

    TYPE_CHOICES = (
        ("GPBS", "on-street pair"),
        ("GCLS", "on-street cluster"),
        ("GAIR", "airport building"),
        ("GBCS", "bus/coach station"),
        ("GFTD", "ferry terminal/dock"),
        ("GTMU", "tram/metro station"),
        ("GRLS", "rail station"),
        ("GCCH", "coach service coverage"),
    )
    stop_area_type = models.CharField(max_length=4, choices=TYPE_CHOICES)

    parent = models.ForeignKey("self", models.SET_NULL, null=True, blank=True)
    latlong = models.PointField(null=True)
    active = models.BooleanField()

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("stoparea_detail", args=(self.id,))


class DataSource(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    description = models.CharField(blank=True)
    url = models.URLField(blank=True, db_index=True)
    datetime = models.DateTimeField(null=True, blank=True)
    sha1 = models.CharField(max_length=40, null=True, blank=True, db_index=True)
    settings = models.JSONField(null=True, blank=True)
    source = models.ForeignKey(
        TimetableDataSource, models.CASCADE, null=True, blank=True
    )
    # for HTTP "if-modified-since" and "if-none-match":
    last_modified = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("source_detail", args=(self.id,))

    def get_nice_name(self):
        return self.name.split("_")[0]

    def get_nice_url(self):
        if not self.url:
            return

        parsed_url = urlparse(self.url)

        # BODS
        if parsed_url.hostname.endswith(".bus-data.dft.gov.uk"):
            return self.url.replace("download/", "")
        # Passenger
        if (
            parsed_url.path == "/open-data"
            or parsed_url.hostname == "data.discoverpassenger.com"
        ):
            return self.url
        match parsed_url.hostname:
            case "opendata.stagecoachbus.com":
                return "https://www.stagecoachbus.com/open-data"
            case "www.transportforireland.ie":
                return f"https://www.transportforireland.ie/transitData/PT_Data.html#:~:text={self.name}"

    def is_tnds(self):
        match self.name:
            case (
                "L"
                | "GB"
                | "Y"
                | "SW"
                | "SE"
                | "EM"
                | "EA"
                | "WM"
                | "S"
                | "NE"
                | "NW"
                | "W"
                | "IM"
            ):
                return True
        return False

    def credit(self, route=None):
        url = self.get_nice_url()
        text = None
        date = self.datetime

        if self.is_tnds():
            match self.name:
                case "L":
                    text = "Transport for London"
                case "GB":
                    url = "https://data.bus-data.dft.gov.uk/coach/download"
                    text = "the Bus Open Data Service (BODS)"
                case _:
                    url = "https://www.travelinedata.org.uk/"
                    text = "the Traveline National Dataset (TNDS)"
        elif url:
            text = self.get_nice_name()
            hostname = urlparse(url).hostname
            if hostname.endswith(".bus-data.dft.gov.uk"):
                text = f"{text}/Bus Open Data Service (BODS)"
            elif hostname == "www.transportforireland.ie":
                text = "National Transport Authority"
        elif urlparse(self.url).hostname == "opendata.ticketer.com":
            text = self.url
        elif self.name == "MET" or self.name == "ULB":
            url = self.url
            text = "Translink open data"
        else:
            text = self.name

        if text == "FlixBus":
            url = "https://transport.data.gouv.fr/datasets/flixbus-horaires-theoriques-du-reseau-europeen-1"
        elif text == "TfGM":
            url = "https://www.data.gov.uk/dataset/c3ca6469-7955-4a57-8bfc-58ef2361b797/gm-public-transport-schedules-gtfs"

        if route:
            # get date from 'bluestar_1611829131.zip/Bluestar 31 01 2021_SER2.xml'
            timestamp = route.code.split("/")[0].split("_")[-1].removesuffix(".zip")
            if timestamp.isdigit():
                timestamp = int(timestamp)
                if timestamp > 1600000000:
                    date = datetime.datetime.fromtimestamp(int(timestamp))

        if text:
            if url:
                text = format_html('<a href="{}" rel="nofollow">{}</a>', url, text)
            else:
                text = escape(text)
            if date:
                text = mark_safe(
                    f"""{text}, <time datetime="{date.date()}">{date:%-d %B %Y}</time>"""
                )
            return text

        return ""

    def older_than(self, when):
        if not self.datetime or not when or self.datetime < when:
            return True
        return False

    def get_s3_path(self):
        return f"source/{self.id}/{self.datetime.isoformat()}"

    def upload_to_s3_etc(self, path):
        import boto3

        client = boto3.client("s3", endpoint_url="https://ams3.digitaloceanspaces.com")
        try:
            client.upload_file(path, "bustimes-data", self.get_s3_path())
        except NoCredentialsError:
            pass


class StopPoint(models.Model):
    """The smallest type of geographical point.
    A point at which vehicles stop"""

    source = models.ForeignKey(DataSource, models.DO_NOTHING, null=True, blank=True)

    atco_code = models.CharField(
        verbose_name="ATCO code", max_length=36, primary_key=True
    )
    naptan_code = models.CharField(
        verbose_name="NaPTAN code", max_length=16, null=True, blank=True
    )

    common_name = models.CharField(max_length=48)
    short_common_name = models.CharField(max_length=48, blank=True)
    landmark = models.CharField(max_length=48, blank=True)
    street = models.CharField(max_length=48, blank=True)
    crossing = models.CharField(max_length=48, blank=True)
    indicator = models.CharField(max_length=48, blank=True)

    latlong = models.PointField(null=True, blank=True)

    parents = models.ManyToManyField("self", blank=True)
    stop_area = models.ForeignKey(StopArea, models.SET_NULL, null=True, blank=True)
    locality = models.ForeignKey("Locality", models.SET_NULL, null=True, blank=True)
    suburb = models.CharField(max_length=48, blank=True)
    town = models.CharField(max_length=48, blank=True)
    locality_centre = models.BooleanField(null=True)

    heading = models.PositiveIntegerField(null=True, blank=True)

    timezone = TimeZoneField(null=True, blank=True)

    description = models.CharField(null=True, blank=True)
    notes = models.CharField(null=True, blank=True)

    BEARING_CHOICES = (
        ("N", "north \u2191"),
        ("NE", "north-east \u2197"),
        ("E", "east \u2192"),
        ("SE", "south-east \u2198"),
        ("S", "south \u2193"),
        ("SW", "south-west \u2199"),
        ("W", "west \u2190"),
        ("NW", "north-west \u2196"),
    )
    bearing = models.CharField(max_length=2, choices=BEARING_CHOICES, blank=True)

    STOP_TYPE_CHOICES = (
        ("AIR", "Airport entrance"),
        ("GAT", "Air airside area"),
        ("FTD", "Ferry terminal/dock entrance"),
        ("FER", "Ferry/dock berth area"),
        ("FBT", "Ferry berth"),
        ("RSE", "Rail station entrance"),
        ("RLY", "Rail platform access area"),
        ("RPL", "Rail platform"),
        ("TMU", "Tram/metro/underground entrance"),
        ("MET", "Tram/metro/underground access area"),
        ("PLT", "Metro and underground platform access area"),
        ("BCE", "Bus/coach station entrance"),
        ("BCS", "Bus/coach bay/stand/stance within bus/coach station"),
        ("BCQ", "Bus/coach bay"),
        ("BCT", "On street bus/coach/tram stop"),
        ("TXR", "Taxi rank (head of)"),
        ("STR", "Shared taxi rank (head of)"),
    )
    stop_type = models.CharField(max_length=3, choices=STOP_TYPE_CHOICES, blank=True)

    BUS_STOP_TYPE_CHOICES = (
        ("MKD", "Marked (pole, shelter etc)"),
        ("HAR", "Hail and ride"),
        ("CUS", "Custom (unmarked, or only marked on road)"),
        ("FLX", "Flexible zone"),
    )
    bus_stop_type = models.CharField(
        max_length=3, choices=BUS_STOP_TYPE_CHOICES, blank=True
    )

    timing_status = models.CharField(
        max_length=3, choices=TIMING_STATUS_CHOICES, blank=True
    )

    admin_area = models.ForeignKey("AdminArea", models.SET_NULL, null=True, blank=True)
    active = models.BooleanField(db_index=True)

    created_at = models.DateTimeField(null=True, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)
    revision_number = models.PositiveSmallIntegerField(null=True, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        ordering = ("common_name", "atco_code")
        indexes = [
            models.Index(Upper("naptan_code"), name="naptan_code"),
        ]
        constraints = [
            models.UniqueConstraint(Upper("atco_code"), name="atco_code"),
        ]

    def __str__(self):
        name = self.get_unqualified_name()
        if self.bearing:
            name = f"{name} {self.get_arrow()}"
        return name

    def get_heading(self):
        """Return the stop's bearing converted to degrees, for use with Google Street View."""
        if self.heading:
            return self.heading
        headings = {
            "N": 0,
            "NE": 45,
            "E": 90,
            "SE": 135,
            "S": 180,
            "SW": 225,
            "W": 270,
            "NW": 315,
        }
        return headings.get(self.bearing)

    prepositions = {
        "opp": "opposite",
        "adj": "adjacent to",
        "at": "at",
        "o/s": "outside",
        "nr": "near",
        "before": "before",
        "after": "after",
        "by": "by",
        "on": "on",
        "in": "in",
        "opposite": "opposite",
        "outside": "outside",
    }

    def get_unqualified_name(self):
        if self.indicator:
            if (
                " " in self.indicator
                and self.indicator.lower() in self.common_name.lower()
            ):
                return self.common_name  # not 'Bus Station stand V (Stand V)'
            return f"{self.common_name} ({self.indicator})"
        if self.atco_code[:3] == "940":
            return self.common_name.replace(" Underground Station", "")
        return self.common_name

    def get_arrow(self):
        if self.bearing:
            return self.get_bearing_display().split()[-1]
        return ""

    def get_qualified_name(self, short=True):
        name = self.get_unqualified_name()
        if self.locality:
            locality_name = self.locality.name.replace(" Town Centre", "").replace(
                " City Centre", ""
            )
            if self.common_name and locality_name.endswith(self.common_name):
                return locality_name.replace(self.common_name, name)  # Cardiff Airport
            if slugify(locality_name).split("-", 1)[0] not in slugify(self.common_name):
                if self.indicator.lower() in self.prepositions:
                    indicator = self.indicator.lower()
                    if not short:
                        indicator = self.prepositions[indicator]
                    return f"{locality_name}, {indicator} {self.common_name}"
                return f"{locality_name} {name}"
        elif self.town not in self.common_name:
            return f"{self.town} {name}"
        return name

    def get_name_for_timetable(self):
        if self.locality:
            locality_name = self.locality.name.replace(" Town Centre", "").replace(
                " City Centre", ""
            )
            if locality_name not in self.common_name:
                return f"{locality_name} {self.common_name}"
        return self.common_name

    def get_long_name(self):
        return self.get_qualified_name(short=False)

    def get_region(self):
        if self.admin_area_id:
            return self.admin_area.region
        return Region.objects.filter(service__stops=self).first()

    def get_absolute_url(self):
        return reverse("stoppoint_detail", args=(self.atco_code,))

    def get_icon(self):
        if self.indicator:
            if len(self.indicator) < 3 and not self.indicator.islower():
                return self.indicator

            parts = self.indicator.split()
            if len(parts) == 2 and len(parts[1]) < 3:
                a, b = parts
                match a.lower():
                    case "stop" | "bay" | "stand" | "stance" | "gate" | "platform":
                        return b

        if self.common_name:
            # "Bus Station A" or "Bus Station 4"
            parts = self.common_name.split()
            if (parts[-1].isdigit() or parts[-1].isupper()) and len(parts[-1]) < 3:
                return parts[-1]

    def get_line_names(self):
        return list(filter(None, self.line_names))


class OperatorGroup(models.Model):
    slug = models.SlugField(max_length=48)
    name = models.CharField(max_length=100)
    group_fleet_numbering = models.BooleanField(default=True)
    allow_transfers = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("group_vehicles", args=(self.slug,))


class OperatorManager(models.Manager):
    def with_documents(self):
        vector = SearchVector("name", weight="A", config="english")
        vector += SearchVector("noc", weight="A", config="english")
        vector += SearchVector("aka", weight="B", config="english")
        return self.get_queryset().annotate(document=vector)


class Operator(SearchMixin, models.Model):
    """An entity that operates public transport services"""

    source = models.ForeignKey(DataSource, models.DO_NOTHING, null=True, blank=True)

    noc = models.CharField(max_length=10, primary_key=True)  # e.g. 'YCST'
    name = models.CharField(max_length=100, db_index=True)
    qualifier_name = models.CharField(max_length=100, blank=True)
    aka = models.CharField(max_length=100, blank=True)
    slug = AutoSlugField(populate_from=str, editable=True, unique=True)
    vehicle_mode = models.CharField(max_length=48, blank=True)
    group = models.ForeignKey(OperatorGroup, models.SET_NULL, null=True, blank=True)
    siblings = models.ManyToManyField("self", blank=True)
    region = models.ForeignKey(Region, models.SET_NULL, null=True, blank=True)
    regions = models.ManyToManyField(Region, blank=True, related_name="operators")
    colour = models.ForeignKey("ServiceColour", models.SET_NULL, null=True, blank=True)

    address = models.CharField(max_length=128, blank=True)
    url = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=128, blank=True)
    twitter = models.CharField(max_length=255, blank=True)

    licences = models.ManyToManyField("vosa.Licence", blank=True)
    payment_methods = models.ManyToManyField("PaymentMethod", blank=True)
    search_vector = SearchVectorField(null=True, blank=True)
    modified_at = models.DateTimeField(auto_now=True)

    objects = OperatorManager()

    class Meta:
        ordering = ("name",)
        indexes = [GinIndex(fields=["search_vector"])]

    def __repr__(self):
        return f"{self.noc}: {self.name}"

    def __str__(self):
        return str(self.name or self.noc)

    def get_absolute_url(self):
        return reverse("operator_detail", args=(self.slug or self.noc,))

    def get_vehicles_url(self):
        return reverse("operator_vehicles", args=(self.slug,))

    def mode(self):
        return self.vehicle_mode

    def get_a_mode(self):
        """Return the the name of the operator's vehicle mode,
        with the correct indefinite article
        depending on whether it begins with a vowel.

        'Airline' becomes 'An airline', 'Bus' becomes 'A bus'.
        """
        mode = str(self.vehicle_mode).lower()
        if not mode or mode[0].lower() in "aeiou":
            return "An " + mode  # 'An airline' or 'An '
        return "A " + mode  # 'A hovercraft'


class StopCode(models.Model):
    stop = models.ForeignKey(StopPoint, models.CASCADE)
    source = models.ForeignKey(
        "busstops.DataSource",
        models.CASCADE,
        limit_choices_to={
            "name__in": (
                "FlixBus",
                "National coach code",
            )
        },
        default=3695,  # FlixBus
    )
    code = models.CharField(max_length=100)

    class Meta:
        unique_together = ("code", "source")

    def __str__(self):
        return self.code


class OperatorCode(models.Model):
    operator = models.ForeignKey(Operator, models.CASCADE)
    source = models.ForeignKey(DataSource, models.CASCADE)
    code = models.CharField(max_length=100, db_index=True)

    class Meta:
        unique_together = ("operator", "code", "source")

    def __str__(self):
        return self.code


class StopUsage(models.Model):
    """A link between a StopPoint and a Service,
    with an order placing it in a direction (e.g. the first outbound stop)"""

    service = models.ForeignKey("Service", models.CASCADE)
    stop = models.ForeignKey(StopPoint, models.CASCADE)
    order = models.PositiveSmallIntegerField()
    timing_point = models.BooleanField(default=True)
    inbound = models.BooleanField(default=False)
    line_name = models.CharField(db_collation="en_numeric")

    class Meta:
        ordering = ("inbound", "order")


class ServiceColour(models.Model):
    name = models.CharField(max_length=64, blank=True)
    foreground = models.CharField(max_length=20, blank=True)
    background = models.CharField(max_length=20, blank=True)
    border = models.CharField(max_length=20, blank=True)
    use_name_as_brand = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def preview(self, name=False):
        return format_html(
            '<div style="background:{};color:{}">{}</div>',
            self.background,
            self.foreground,
            self.name or "-",
        )


class ServiceManager(models.Manager):
    def with_documents(self):
        vector = SearchVector(
            StringAgg("route__line_name", Value(" "), distinct=True, default=""),
            weight="A",
            config="english",
        )
        vector += SearchVector("line_brand", weight="A", config="english")
        vector += SearchVector("description", weight="B", config="english")
        vector += SearchVector(
            StringAgg(
                Concat("operator__noc", Value(" "), "operator__name"),
                Value(" "),
                default="",
            ),
            weight="B",
            config="english",
        )
        vector += SearchVector(
            StringAgg(
                Concat("stops__locality__name", Value(" "), "stops__common_name"),
                Value(" "),
                default="",
            ),
            weight="C",
            config="english",
        )
        return self.get_queryset().annotate(document=vector)

    def with_line_names(self):
        return self.get_queryset().annotate(
            line_names=ArrayAgg(
                Coalesce("route__line_name", "line_name"), distinct=True, default=None
            )
        )


class Service(models.Model):
    """A bus service"""

    service_code = models.CharField(max_length=64, db_index=True, blank=True)
    line_name = models.CharField(max_length=64, blank=True)
    line_brand = models.CharField(max_length=64, blank=True)
    description = models.CharField(max_length=255, blank=True, db_index=True)
    slug = AutoSlugField(populate_from=str, editable=True, unique=True)
    mode = models.CharField(max_length=11, blank=True, default="bus")
    operator = models.ManyToManyField(Operator, blank=True)
    region = models.ForeignKey(Region, models.CASCADE, null=True, blank=True)
    stops = models.ManyToManyField(StopPoint, through=StopUsage)
    current = models.BooleanField(default=True, db_index=True)
    timetable_wrong = models.BooleanField(default=False)
    geometry = models.GeometryField(null=True, blank=True)

    source = models.ForeignKey(DataSource, models.SET_NULL, null=True, blank=True)
    tracking = models.BooleanField(default=False)
    payment_methods = models.ManyToManyField(
        "PaymentMethod", through="ServicePaymentMethod", blank=True
    )
    search_vector = SearchVectorField(null=True, blank=True)
    modified_at = models.DateTimeField(auto_now=True)

    public_use = models.BooleanField(null=True)

    colour = models.ForeignKey(ServiceColour, models.SET_NULL, null=True, blank=True)

    objects = ServiceManager()
    update_search_vector = SearchMixin.update_search_vector

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(Upper("line_name"), name="line_name"),
            GinIndex(fields=["search_vector"]),
        ]

    def __str__(self):
        line_name = self.get_line_name()
        description = self.description
        if description == line_name:
            description = None
        elif (
            " " in line_name
            and line_name in description
            or line_name in self.line_brand
        ):
            line_name = None
        if line_name or self.line_brand or description:
            parts = (line_name, self.line_brand, description)
            return " - ".join(part for part in parts if part)
        return self.service_code

    def yaml(self):
        return yaml.dump(
            {
                self.service_code: {
                    "line_name": self.line_name,
                    "line_brand": self.line_brand,
                    "description": self.description,
                    "current": self.current,
                }
            }
        )

    def get_line_names(self):
        if hasattr(self, "line_names") and self.line_names:
            return self.line_names
        return [self.line_name]

    def get_line_name(self):
        return ", ".join(self.get_line_names())

    def get_line_name_and_brand(self):
        line_name = self.get_line_name()
        if self.line_brand:
            return f"{line_name} - {self.line_brand}"
        return line_name

    def get_a_mode(self):
        if self.mode and self.mode[0].lower() in "aeiou":
            return f"An {self.mode}"  # 'An underground service'
        return f"A {self.mode}"  # 'A bus service' or 'A service'

    def get_absolute_url(self):
        return reverse("service_detail", args=(self.slug,))

    def get_order(self):
        if hasattr(self, "group"):
            return self.group, self.get_line_name_order(self.get_line_names()[0])
        return self.get_line_name_order(self.get_line_names()[0])

    @staticmethod
    def get_line_name_order(line_name):
        prefix, number, suffix = SERVICE_ORDER_REGEX.match(line_name).groups()
        number = number.zfill(4)
        if prefix == "X" or prefix == "N":
            return ("", number, prefix, suffix)
        return (prefix, number, suffix)

    def get_tfl_url(self) -> str:
        return f"https://tfl.gov.uk/bus/timetable/{self.line_name}/"

    def get_trapeze_link(self):
        domain = "travelinescotland.com"
        name = "Timetable on the Traveline Scotland website"
        query = (("serviceId", self.service_code.replace("_", " ")),)
        return f"https://www.{domain}/timetables?{urlencode(query)}", name

    def get_traveline_links(self, date=None) -> list:
        if not self.source_id:
            return []

        if (
            self.source.name == "S"
            and "_" in self.service_code
            and not self.service_code.startswith("S_")
        ):
            return [self.get_trapeze_link()]

        if self.source.name == "W" or self.region_id == "W":
            for service_code in self.servicecode_set.filter(scheme="Traveline Cymru"):
                query = (
                    ("routeNum", self.line_name),
                    ("direction_id", 0),
                    ("timetable_key", service_code.code),
                )
                url = "https://www.traveline.cymru/timetables/?" + urlencode(query)
                return [(url, "Timetable on the Traveline Cymru website")]

        elif (
            self.source.name == "L"
            and self.servicecode_set.filter(scheme="TfL").exists()
        ):
            return [
                (self.get_tfl_url(), "Timetable on the Transport for London website"),
            ]
        return []

    def get_similar_services(self):
        ids = self.link_from.values("to_service").union(
            self.link_to.values("from_service")
        )

        # if self.service_code:
        #     ids = ids.union(
        #         Service.objects.filter(
        #             ~Q(id=self.id),
        #             source=self.source_id,
        #             service_code=self.service_code
        #         ).values("id")
        #     )
        # else:

        ids = ids.union(
            Route.objects.filter(
                ~Q(service=self.id),
                ~Q(service_code=""),
                ~Q(service_code__endswith=":"),
                service_code__in=self.route_set.values("service_code"),
                source=self.source_id,
            ).values("service")
        )

        if ":" not in self.service_code:
            if match := re.match(r"^(?P<number>\d+)[A-Za-z]?$", self.line_name):
                number = match.group("number")
                ids = ids.union(
                    Service.objects.filter(
                        ~Q(id=self.id),
                        source=self.source_id,
                        current=True,
                        operator__service=self,
                        line_name__regex=rf"^{number}[A-Za-z]?$",
                    ).values("id")
                )

        services = (
            Service.objects.with_line_names()
            .filter(id__in=ids, current=True)
            .order_by()
            .defer("search_vector", "geometry")
        )
        services = sorted(
            services.annotate(
                operators=ArrayAgg("operator__name", distinct=True, default=None)
            ),
            key=Service.get_order,
        )
        return services

    def get_timetable(
        self,
        day=None,
        calendar_id=None,
        also_services=None,
        line_names=None,
        detailed=False,
    ):
        """Given a Service, return a Timetable"""

        routes = self.route_set.all()

        if line_names:
            if also_services:
                routes = Route.objects.filter(service__in=[self] + also_services)

            line_name_query = Q()
            for line_name in line_names:
                if ":" in line_name:
                    service_id, line_name = line_name.split(":", 1)
                    line_name_query |= Q(service=service_id, line_name=line_name)
                else:
                    line_name_query |= Q(line_name=line_name)
            routes = routes.filter(line_name_query)

        operators = self.operator.all()
        try:
            timetable = Timetable(
                routes,
                day,
                calendar_id=calendar_id,
                detailed=detailed,
                operators=operators,
            )
        except (IndexError, UnboundLocalError, AssertionError) as e:
            logger = logging.getLogger(__name__)

            logger.exception(e)
            return

        cache_key = [
            str(self.id),
            str(int(self.modified_at.timestamp())),
            str(detailed),
        ]
        if line_names:
            cache_key += line_names
        if also_services:
            cache_key += [
                f"{s.id}:{int(self.modified_at.timestamp())}" for s in also_services
            ]
        cache_key += [str(r.id) for r in timetable.current_routes]

        if timetable.calendar:
            cache_key += str(timetable.calendar.id)
        elif timetable.calendars:
            cache_key += [str(calendar_id) for calendar_id in timetable.calendar_ids]

        timetable.cache_key = ":".join(cache_key)

        return timetable

    def do_stop_usages(self):
        existing = self.stopusage_set.order_by("id")

        stop_times = StopTime.objects.filter(
            trip__route__service=self, stop__isnull=False
        )
        stop_times = stop_times.distinct(
            "trip__inbound", "trip__route__line_name", "stop_id"
        ).order_by("trip__inbound", "trip__route__line_name", "stop_id")
        stop_times = stop_times.values(
            "trip__route__line_name",
            "trip__inbound",
            "sequence",
            "stop_id",
            "timing_status",
        )
        stop_usages = [
            (
                st["trip__route__line_name"],
                st["trip__inbound"],
                st["sequence"] or 0,
                st["stop_id"],
                st["timing_status"],
            )
            for st in stop_times
        ]
        stop_usages.sort()

        proposed = [
            StopUsage(
                service=self,
                stop_id=stop_id,
                timing_point=(timing_status == "PTP"),
                inbound=inbound,
                order=i,
                line_name=line_name,
            )
            for i, (
                line_name,
                inbound,
                sequence,
                stop_id,
                timing_status,
            ) in enumerate(stop_usages)
        ]

        existing_hash = [
            (su.stop_id, su.timing_point, su.inbound, su.order, su.line_name)
            for su in existing
        ]
        proposed_hash = [
            (su.stop_id, su.timing_point, su.inbound, su.order, su.line_name)
            for su in proposed
        ]

        if existing_hash != proposed_hash:
            if len(existing_hash) >= len(proposed_hash):  # reuse ids
                for proposed_su, existing_su in zip(proposed, existing):
                    proposed_su.id = existing_su.id
                StopUsage.objects.bulk_update(
                    proposed,
                    ["stop_id", "timing_point", "inbound", "order", "line_name"],
                )
                if len(existing_hash) > len(proposed_hash):
                    existing.filter(~Q(id__in=[su.id for su in proposed])).delete()
            else:
                if existing_hash:
                    existing.delete()
                StopUsage.objects.bulk_create(proposed)

    def update_description(self):
        routes = self.route_set.all()

        inbound_outbound_descriptions, origins_and_destinations = get_descriptions(
            routes
        )

        descriptions = {route.description for route in routes if route.description}

        if origins_and_destinations and (
            not inbound_outbound_descriptions
            and len(descriptions) > len(origins_and_destinations)
            or len(inbound_outbound_descriptions) > len(origins_and_destinations)
        ):
            description = " - ".join(max(origins_and_destinations, key=len))
            if description != self.description and len(description) <= 255:
                self.description = description
                self.save(update_fields=["description"])

    def update_geometry(self, save=True):
        extent = self.stopusage_set.aggregate(Extent("stop__latlong"))
        extent = extent["stop__latlong__extent"]
        if extent:
            self.geometry = Polygon.from_bbox(extent)
            if save:
                self.save(update_fields=["geometry"])


class ServiceCode(models.Model):
    service = models.ForeignKey(Service, models.CASCADE)
    scheme = models.CharField(max_length=255)
    code = models.CharField(max_length=255)

    class Meta:
        unique_together = ("service", "scheme", "code")

    def __str__(self):
        return f"{self.scheme} {self.code}"


class ServiceLink(models.Model):
    from_service = models.ForeignKey(Service, models.CASCADE, "link_from")
    to_service = models.ForeignKey(Service, models.CASCADE, "link_to")
    how = models.CharField(
        max_length=10,
        choices=(
            ("parallel", "Combine timetables"),
            ("also", "Just list"),
        ),
    )

    def get_absolute_url(self):
        return self.from_service.get_absolute_url()


class PaymentMethod(models.Model):
    name = models.CharField(max_length=48)
    url = models.URLField(blank=True)

    def __str__(self):
        return self.name


class ServicePaymentMethod(models.Model):
    service = models.ForeignKey("Service", models.CASCADE)
    payment_method = models.ForeignKey("PaymentMethod", models.CASCADE)
    accepted = models.BooleanField(default=True)

    class Meta:
        unique_together = ("service", "payment_method")


class Contact(models.Model):
    from_name = models.CharField(max_length=255)
    from_email = models.EmailField()
    message = models.TextField()
    spam_score = models.PositiveIntegerField()
    ip_address = models.GenericIPAddressField()
    referrer = models.URLField(blank=True)


class SIRISource(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField()
    requestor_ref = models.CharField(max_length=255, blank=True)
    admin_areas = models.ManyToManyField(AdminArea, blank=True)
    operators = models.ManyToManyField(Operator, blank=True)

    def __str__(self):
        return self.name

    def get_poorly_key(self):
        return f"{self.url}:{self.requestor_ref}:poorly"

    def is_poorly(self):
        return cache.get(self.get_poorly_key())
