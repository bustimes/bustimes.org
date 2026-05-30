"""
Usage:

    ./manage.py import_transxchange EA.zip [EM.zip etc]
"""

import datetime
import hashlib
import logging
import os
import sys
from pathlib import Path
import re
import zipfile
from functools import cache

from tqdm import tqdm

from django.core.management.base import BaseCommand, CommandError
from django.contrib.gis.geos import GEOSGeometry, Point
from django.db import IntegrityError
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import Now, Upper
from django.utils.timezone import localdate
from titlecase import titlecase

from busstops.models import (
    DataSource,
    Operator,
    Service,
    ServiceCode,
    ServiceColour,
    StopPoint,
    StopUsage,
)
from busstops.management.commands.naptan_new import get_stop
from busstops.utils import get_datetime
from txc import TransXChange
from vehicles.models import get_text_colour
from vosa.models import Registration

from ...models import (
    BankHoliday,
    Calendar,
    CalendarBankHoliday,
    CalendarDate,
    Garage,
    Note,
    Route,
    RouteLink,
    StopTime,
    TimetableDataSource,
    Trip,
    VehicleType,
)

logger = logging.getLogger(__name__)

"""
_________________________________________________________________________________________________
| AllBankHolidays | AllHolidaysExceptChristmas | Holidays             | NewYearsDay              |
|                 |                            |                      | 🏴󠁧󠁢󠁳󠁣󠁴󠁿 Jan2ndScotland        |
|                 |                            |                      | GoodFriday               |
|                 |                            |                      | 🏴󠁧󠁢󠁳󠁣󠁴󠁿 StAndrewsDay          |
|                 |                            |______________________|__________________________|
|                 |                            | HolidayMondays       | EasterMonday             |
|                 |                            |                      | MayDay                   |
|                 |                            |                      | SpringBank               |________
|                 |                            |                      | LateSummerBankHolidayNotScotland  |
|                 |                            |                      | AugustBankHolidayScotland   ______|
|                 |____________________________|______________________|____________________________|
|                 | Christmas            | ChristmasDay               |
|                 |                      | BoxingDay                  |
|                 |______________________|____________________________|
|                 | DisplacementHolidays | ChristmasDayHoliday        |
|                 |                      | BoxingDayHoliday           |
|                 |                      | NewYearsDayHoliday         |
|                 |                      | 🏴󠁧󠁢󠁳󠁣󠁴󠁿 Jan2ndScotlandHoliday   |
|                 |                      | 🏴󠁧󠁢󠁳󠁣󠁴󠁿 StAndrewsDayHoliday     |
|_________________|______________________|____________________________|
| EarlyRunOff     | ChristmasEve |
|                 | NewYearsEve  |
|_________________|______________|
"""


# a callback for titlecase
def initialisms(word, **kwargs):
    if word in ("YMCA", "PH"):
        return word


STUPID_ORIGINS_DESTINATIONS = {"Origin", "Destination", "Unknown"}


def get_summary(summary: str):
    summary = summary.strip()

    # London wtf
    if summary == "School vacation in free public holidays regulation days":
        return "school days"

    # Scotland
    if summary.startswith("QE0 : ") and summary.endswith(".cif"):
        return ""

    summary = summary.replace(" days days", " days")
    summary = summary.replace("olidays holidays", "olidays")
    summary = summary.replace("AnySchool", "school")

    summary = re.sub(r"(?i)(school(day)?s)", "school", summary)

    return summary[:255]


def get_service_code(filename):
    """
    Given a filename like 'ea_21-45A-_-y08-1.xml',
    returns a service_code like 'ea_21-45A-_-y08'
    """
    parts = filename.split("/")[-1].split("-")  # ['ea_21', '3', '_', '1']
    if len(parts) == 5:
        net = parts[0].split("_")[0]
        if len(net) <= 3 and net.isalpha() and net.islower():
            return "-".join(parts[:-1])


def get_operator_name(operator_element):
    "Given an Operator element, returns the operator name or None"

    for element_name in ("TradingName", "OperatorNameOnLicence", "OperatorShortName"):
        name = operator_element.findtext(element_name)
        if name:
            return name.replace("&amp;", "&")


@cache
def get_operator_by(scheme, code):
    assert scheme
    if code:
        try:
            return (
                Operator.objects.filter(
                    operatorcode__code=code, operatorcode__source__name=scheme
                )
                .distinct()
                .get()
            )
        except Operator.DoesNotExist:
            pass
        except Operator.MultipleObjectsReturned as e:
            logger.exception(e)


def get_open_data_operators():
    timetable_data_sources = TimetableDataSource.operators.through.objects.filter(
        timetabledatasource__active=True,
    ).values_list("operator_id", flat=True)

    open_data_operators = set(
        timetable_data_sources.filter(timetabledatasource__complete=True)
    )
    incomplete_operators = set(
        timetable_data_sources.filter(timetabledatasource__complete=False)
    )

    return open_data_operators, incomplete_operators


def get_calendar_date(
    date_range=None, date=None, special=False, operation=None, summary=""
):
    if date_range:
        start_date = date_range.start
        end_date = date_range.end
        summary = date_range.note or date_range.description
    else:
        start_date = date
        end_date = date
    return CalendarDate(
        start_date=start_date,
        end_date=end_date,
        special=special,
        operation=operation,
        summary=summary,
    )


def get_registration(service_code: str) -> Registration | None:
    parts = service_code.split(":")
    if len(parts[0]) != 9:
        prefix = parts[0][:2]
        suffix = str(int(parts[0][2:]))
        parts[0] = f"{prefix}{suffix.zfill(7)}"
    if parts[1] and parts[1].isdigit():
        try:
            return Registration.objects.get(
                Q(registration_number=f"{parts[0]}/{int(parts[1])}")
                | Q(registration_number=service_code.replace(":", "/"))
            )
        except Registration.DoesNotExist:
            pass


def get_route_links(journeys, transxchange: TransXChange):
    patterns = {
        journey.journey_pattern.id: journey.journey_pattern for journey in journeys
    }
    route_refs = [
        pattern.route_ref for pattern in patterns.values() if pattern.route_ref
    ]
    if route_refs:
        routes = [
            transxchange.routes[route_id]
            for route_id in transxchange.routes
            if route_id in route_refs
        ]
        for route in routes:
            for section_ref in route.route_section_refs:
                route_section = transxchange.route_sections[section_ref]
                for route_link in route_section.links:
                    if route_link.track:
                        yield route_link
    else:
        route_links = {}
        for route_section in transxchange.route_sections.values():
            for route_section_link in route_section.links:
                route_links[route_section_link.id] = route_section_link
        for journey in journeys:
            if journey.journey_pattern:
                for section in journey.journey_pattern.sections:
                    for timing_link in section.timinglinks:
                        try:
                            route_link = route_links[timing_link.route_link_ref]
                        except KeyError:
                            continue
                        if route_link.track:
                            yield route_link


def route_link_is_dodgy(point: Point, stop: StopPoint, context: str) -> bool:
    if point.srid and point.srid != 4326:
        point.transform(4326)

    if stop.latlong:
        if stop.latlong.srid and stop.latlong.srid != 4326:
            stop.latlong.transform(4326)
        distance = stop.latlong.distance(point)
        if distance > 0.02:
            # stop location roughly more than 1 km start/end of RouteLink
            logger.warning(f"{context}: {stop.atco_code} is {distance} from {point}")
            return True
    return False


def do_route_links(journeys, transxchange, stops, service):
    route_links = list(get_route_links(journeys, transxchange))

    # we're not interested in straight lines between stops
    if any(len(link.track) > 2 for link in route_links):
        stops_to_update = []

        route_links_to_create = {}  # or update

        for route_link in route_links:
            from_stop = stops.get(route_link.from_stop)
            to_stop = stops.get(route_link.to_stop)

            if type(from_stop) is StopPoint and type(to_stop) is StopPoint:
                start_point = GEOSGeometry(route_link.track[0].wkt())

                # Highland Council - eastings and northings are 1000000x off
                if 0 < start_point.x < 1 and 0 < start_point.y < 1:
                    start_point = Point(
                        start_point.x * 1000000, start_point.y * 1000000, srid=27700
                    )

                    for point in route_link.track:
                        point.longitude = float(point.longitude) * 1000000
                        point.latitude = float(point.latitude) * 1000000
                        point.srid = 27700
                    route_link.srid = 27700

                if route_link_is_dodgy(start_point, from_stop, service.slug):
                    continue

                end_point = GEOSGeometry(route_link.track[-1].wkt())

                if route_link_is_dodgy(end_point, to_stop, service.slug):
                    continue

                key = (from_stop.atco_code, to_stop.atco_code)
                route_links_to_create[key] = RouteLink(
                    from_stop_id=from_stop.atco_code,
                    to_stop_id=to_stop.atco_code,
                    geometry=route_link.wkt(),
                    service=service,
                )

                # deduce stop location from start or end of track
                if from_stop.latlong is None:
                    from_stop.latlong = start_point
                    stops_to_update.append(from_stop)
                elif to_stop.latlong is None:
                    to_stop.latlong = end_point
                    stops_to_update.append(to_stop)

        RouteLink.objects.bulk_create(
            route_links_to_create.values(),
            update_conflicts=True,
            update_fields=["geometry"],
            unique_fields=["from_stop", "to_stop", "service"],
        )

        StopPoint.objects.bulk_update(stops_to_update, ["latlong"])


def get_stop_time(trip, cell, stops: dict):
    timing_status = cell.stopusage.timingstatus or ""
    if len(timing_status) > 3:
        match timing_status:
            case "otherPoint":
                timing_status = "OTH"
            case "timeInfoPoint":
                timing_status = "TIP"
            case "principleTimingPoint" | "principalTimingPoint":
                timing_status = "PTP"
            case _:
                logger.warning(timing_status)

    stop_time = StopTime(
        trip=trip,
        sequence=cell.stopusage.sequencenumber,
        timing_status=timing_status,
    )
    if (
        stop_time.sequence is not None and stop_time.sequence > 32767
    ):  # too big for smallint
        stop_time.sequence = None

    match cell.activity:
        case "pickUp":
            stop_time.set_down = False
        case "setDown":
            stop_time.pick_up = False
        case "pass":
            stop_time.pick_up = False
            stop_time.set_down = False

    stop_time.departure = cell.departure_time
    if cell.arrival_time != cell.departure_time:
        stop_time.arrival = cell.arrival_time

    if trip.start is None:
        trip.start = stop_time.departure_or_arrival()

    atco_code = cell.stopusage.stop.atco_code.upper()
    if atco_code in stops:
        if type(stops[atco_code]) is str:
            stop_time.stop_code = stops[atco_code]
        else:
            stop_time.stop = stops[atco_code]
            trip.destination = stop_time.stop
    else:
        # stop missing from TransXChange StopPoints - this should never happen
        try:
            stops[atco_code] = StopPoint.objects.get(atco_code__iexact=atco_code)
        except StopPoint.DoesNotExist:
            logger.warning(atco_code)
            stops[atco_code] = atco_code
            stop_time.stop_code = atco_code  # !
        else:
            stop_time.stop = stops[atco_code]
            trip.destination = stop_time.stop

    return stop_time


def get_description(txc_service):
    description = txc_service.description

    if description and description.isupper():
        description = titlecase(description, callback=initialisms)

    origin = txc_service.origin
    destination = txc_service.destination

    if origin and destination:
        if origin[:4].isdigit() and destination[:4].isdigit():
            print(origin, destination)

        if origin.isupper() and destination.isupper():
            txc_service.origin = origin = titlecase(origin, callback=initialisms)
            txc_service.destination = destination = titlecase(
                destination, callback=initialisms
            )

        if not description and origin not in STUPID_ORIGINS_DESTINATIONS:
            description = f"{origin} - {destination}"
            vias = txc_service.vias
            if vias:
                if all(via.isupper() for via in vias):
                    vias = [titlecase(via, callback=initialisms) for via in vias]
                if len(vias) == 1:
                    via = vias[0]
                    if "via " in via:
                        return f"{description} {via}"
                    elif "," in via or " and " in via or "&" in via:
                        return f"{description} via {via}"
                description = " - ".join([origin] + vias + [destination])
    return description


class Command(BaseCommand):
    bank_holidays = None
    version = None

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("archives", nargs=1, type=str)
        parser.add_argument("files", nargs="*", type=str)
        parser.add_argument(
            "--progress",
            action="store_true",
            default=sys.stdout.isatty(),
            help="Show progress bar (default: auto-detect based on terminal)",
        )

    def set_up(self):
        self.calendar_cache = {}
        self.missing_operators = []
        self.notes = {}
        self.garages = {}
        self.today = localdate()

    def handle(self, *args, **options):
        self.set_up()
        self.show_progress = options["progress"]

        self.open_data_operators, self.incomplete_operators = get_open_data_operators()

        for archive_path in options["archives"]:
            self.handle_archive(Path(archive_path), options["files"])

    def set_region(self, archive_name):
        """
        Set region_id and source based on the name of the TNDS archive, creating a DataSource if necessary
        """
        region_id, _ = os.path.splitext(archive_name)  # ea
        self.region_id = region_id.upper()  # EA

        if len(self.region_id) > 2:
            match self.region_id:
                case "NCSD":
                    self.region_id = "GB"
                case "IOM":
                    self.region_id = "IM"
                case _:
                    self.region_id = None

        if self.region_id:
            url = f"ftp://ftp.tnds.basemap.co.uk/{archive_name}"
            self.source, _ = DataSource.objects.get_or_create(
                {"url": url},
                name=self.region_id,
            )
            assert self.source.is_tnds()
        else:
            self.source, _ = DataSource.objects.get_or_create(name=archive_name)

    def get_operator(self, operator_element):
        """
        Given an Operator element, returns an operator code for an operator that exists
        """

        operator_code = operator_element.findtext("NationalOperatorCode")
        if not operator_code:
            if not self.source.is_tnds() or self.source.name == "L":
                operator_code = operator_element.findtext("OperatorCode")

        if operator_code:
            if self.source.name == "L":
                if operator := get_operator_by("L", operator_code):
                    return operator
            elif operator := get_operator_by("National Operator Codes", operator_code):
                return operator

        licence_number = operator_element.findtext("LicenceNumber")
        if licence_number:
            try:
                return Operator.objects.get(licences__licence_number=licence_number)
            except (Operator.DoesNotExist, Operator.MultipleObjectsReturned):
                pass

        name = get_operator_name(operator_element)

        try:
            return Operator.objects.get(name__iexact=name)
        except (Operator.DoesNotExist, Operator.MultipleObjectsReturned):
            pass

        # Get by regional operator code
        operator_code = operator_element.findtext("OperatorCode")
        if operator_code:
            if operator_code.startswith("Rail"):
                operator_code = operator_code.removeprefix("Rail")

            if self.region_id and (
                operator := get_operator_by(self.region_id, operator_code)
            ):
                return operator
            if operator := get_operator_by("National Operator Codes", operator_code):
                return operator

        missing_operator = {
            element.tag: element.text.strip()
            for element in operator_element
            if element.text
        }
        if missing_operator not in self.missing_operators:
            logger.warning(str(missing_operator))
            self.missing_operators.append(missing_operator)

    def get_operators(self, transxchange, service) -> dict:
        operators = transxchange.operators

        if len(operators) > 1:
            # if more than one operator listed, which ones listed actually operate any journeys?
            journey_operators = {
                journey.operator
                for journey in transxchange.journeys
                if journey.operator and journey.service_ref == service.service_code
            }
            journey_operators.add(service.operator)
            operators = [
                operator
                for operator in operators
                if operator.get("id") in journey_operators
            ]

        operators = {
            element.get("id"): self.get_operator(element) for element in operators
        }

        return {key: value for key, value in operators.items() if value}

    def mark_old_services_as_not_current(self):
        # "delete" old routes, if no longer in the dataset OR
        # all the service's routes' end dates are in the past
        self.source.route_set.filter(
            ~Q(id__in=self.route_ids)
            | Q(
                ~Exists(
                    Route.objects.filter(
                        Q(source=OuterRef("source")) | Q(service=OuterRef("service")),
                        Q(end_date=None) | Q(end_date__gte=self.today),
                        revision_number__lt=OuterRef("revision_number"),
                        service_code=OuterRef("service_code"),
                    )
                ),
                end_date__lt=self.today,
            ),
        ).update(service=None)

        old_services = self.source.service_set.filter(current=True, route=None)
        if old_services.update(current=False):
            logger.info(f"  {old_services=}")

    def handle_sub_archive(self, archive, sub_archive_name):
        if sub_archive_name.startswith("__MACOSX"):
            return

        with (
            archive.open(sub_archive_name) as open_file,
            zipfile.ZipFile(open_file) as sub_archive,
        ):
            for filename in sub_archive.namelist():
                if filename.startswith("__MACOSX"):
                    continue
                if filename.endswith(".xml"):
                    with sub_archive.open(filename) as open_file:
                        self.handle_file(open_file, f"{sub_archive_name}/{filename}")
                elif filename.endswith(".zip"):
                    self.handle_sub_archive(sub_archive, filename)

    def maybe_flatten(self, archive_path: Path) -> Path:
        """If the zip contains nested zips, rebuild it as a single flat zip.

        Errors if two inner XMLs would share a basename. Returns the path
        to use for the rest of the import (and the S3 upload).
        """
        try:
            archive = zipfile.ZipFile(archive_path)
        except zipfile.BadZipFile:
            return archive_path

        with archive:
            namelist = archive.namelist()
            if not any(
                name.endswith(".zip") and not name.startswith("__MACOSX")
                for name in namelist
            ):
                return archive_path

            flat_path = archive_path.with_name(archive_path.stem + "-flat.zip")
            seen: dict[str, str] = {}

            def add(base: str, origin: str, data: bytes) -> None:
                if base in seen:
                    raise CommandError(
                        f"duplicate basename {base!r}: {seen[base]} and {origin}"
                    )
                seen[base] = origin
                flat.writestr(base, data)

            with zipfile.ZipFile(flat_path, "w", zipfile.ZIP_DEFLATED) as flat:
                for name in namelist:
                    if name.startswith("__MACOSX"):
                        continue
                    if name.endswith(".zip"):
                        with (
                            archive.open(name) as f,
                            zipfile.ZipFile(f) as sub,
                        ):
                            for inner in sub.namelist():
                                if inner.startswith("__MACOSX") or not inner.endswith(
                                    ".xml"
                                ):
                                    continue
                                with sub.open(inner) as src:
                                    add(Path(inner).name, f"{name}/{inner}", src.read())
                    elif name.endswith(".xml"):
                        with archive.open(name) as src:
                            add(Path(name).name, name, src.read())

        return flat_path

    def handle_archive(self, archive_path: Path, filenames):
        self.service_ids = set()
        self.route_ids = set()

        basename = archive_path.name

        self.set_region(basename)

        self.source.datetime = datetime.datetime.fromtimestamp(
            os.path.getmtime(archive_path), datetime.timezone.utc
        )

        if not filenames:
            archive_path = self.maybe_flatten(archive_path)

        try:
            with zipfile.ZipFile(archive_path) as archive:
                namelist = archive.namelist()

                items = filenames or namelist
                if self.show_progress:
                    items = tqdm(items, desc=basename)

                for filename in items:
                    if filename.startswith("__MACOSX"):
                        continue

                    if filename.endswith(".zip"):
                        self.handle_sub_archive(archive, filename)

                    if filename.endswith(".xml"):
                        with archive.open(filename) as open_file:
                            self.handle_file(open_file, filename)
        except zipfile.BadZipFile:
            with archive_path.open("rb") as open_file:
                self.handle_file(open_file, str(archive_path))

        if not filenames:
            self.mark_old_services_as_not_current()
            self.source.service_set.filter(
                current=False, geometry__isnull=False
            ).update(geometry=None)

        self.finish_services()

        self.source.save(update_fields=["datetime"])

        self.source.upload_to_s3_etc(archive_path)

    def finish_services(self):
        """update/create StopUsages, search_vector and geometry fields"""

        services = Service.objects.filter(id__in=self.service_ids)
        services = services.annotate(operator_count=Count("operator"))

        for service in services:
            service.do_stop_usages()

            # using StopUsages
            service.update_search_vector()

            # using routes
            service.update_geometry()

            service.update_description()

            if service.operator_count > 1:
                operators = Operator.objects.filter(
                    trip__route__service=service
                ).distinct()
                if operators and list(operators) != list(service.operator.all()):
                    service.operator.set(operators)

        services.update(modified_at=Now())

    def get_bank_holiday(self, bank_holiday_name: str):
        if self.bank_holidays is None:
            self.bank_holidays = BankHoliday.objects.in_bulk(field_name="name")
        if bank_holiday_name not in self.bank_holidays:
            self.bank_holidays[bank_holiday_name] = BankHoliday.objects.create(
                name=bank_holiday_name
            )
        return self.bank_holidays[bank_holiday_name]

    def do_bank_holidays(self, holiday_elements, operation: bool, calendar_dates: list):
        if holiday_elements is None:
            return

        for element in holiday_elements:
            bank_holiday_name = element.tag
            if bank_holiday_name == "OtherPublicHoliday":
                date = element.findtext("Date")
                calendar_dates.append(
                    get_calendar_date(
                        date=date,
                        special=operation,
                        operation=operation,
                        summary=element.findtext("Description"),
                    )
                )
            else:
                if bank_holiday_name == "HolidaysOnly":
                    bank_holiday_name = "AllBankHolidays"
                yield self.get_bank_holiday(bank_holiday_name)

    def get_calendar(self, operating_profile, operating_period):
        calendar_hash = f"{operating_profile.hash}{operating_period}"

        if calendar_hash in self.calendar_cache:
            return self.calendar_cache[calendar_hash]

        calendar = Calendar(
            start_date=operating_period.start,
            end_date=operating_period.end,
            source=self.source,
        )
        for day in operating_profile.regular_days:
            match day:
                case 0:
                    calendar.mon = True
                case 1:
                    calendar.tue = True
                case 2:
                    calendar.wed = True
                case 3:
                    calendar.thu = True
                case 4:
                    calendar.fri = True
                case 5:
                    calendar.sat = True
                case 6:
                    calendar.sun = True

        calendar_dates = [
            get_calendar_date(date_range=date_range, operation=False)
            for date_range in operating_profile.nonoperation_days
        ]
        for date_range in operating_profile.operation_days:
            if not date_range.start:
                # this should never happen
                continue

            calendar_date = get_calendar_date(
                date_range=date_range, operation=True, special=True
            )

            difference = date_range.end - date_range.start
            if operating_profile.regular_days and difference > datetime.timedelta(
                days=5
            ):
                # looks like this SpecialDaysOperation was meant to be treated like a ServicedOrganisation?
                # (school term dates etc)
                # calendar_date.special = False
                logger.warning(
                    "%s is %s days long",
                    date_range,
                    difference.days,
                )
            calendar_dates.append(calendar_date)

        bank_holidays = {}  # a dictionary to remove duplicates! (non-operation overrides operation)

        for bank_holiday in self.do_bank_holidays(
            holiday_elements=operating_profile.operation_bank_holidays,
            operation=True,
            calendar_dates=calendar_dates,
        ):
            bank_holidays[bank_holiday] = CalendarBankHoliday(
                operation=True, bank_holiday=bank_holiday
            )

        for bank_holiday in self.do_bank_holidays(
            holiday_elements=operating_profile.nonoperation_bank_holidays,
            operation=False,
            calendar_dates=calendar_dates,
        ):
            bank_holidays[bank_holiday] = CalendarBankHoliday(
                operation=False, bank_holiday=bank_holiday
            )
        summary = []

        if operating_profile.week_of_month:
            logger.info(operating_profile.week_of_month)
            summary.append(f"{operating_profile.week_of_month} week of the month")

        for sodt in operating_profile.serviced_organisations:
            working = sodt.working
            operation = sodt.operation

            if working:
                if sodt.serviced_organisation.working_days:
                    dates = sodt.serviced_organisation.working_days
                else:
                    operation = not operation
                    dates = sodt.serviced_organisation.holidays
            else:
                if sodt.serviced_organisation.holidays:
                    dates = sodt.serviced_organisation.holidays
                else:
                    operation = not operation
                    dates = sodt.serviced_organisation.working_days

            calendar_dates += [
                get_calendar_date(date_range=date_range, operation=operation)
                for date_range in dates
            ]
            summary.append(str(sodt))

        summary = ", ".join(summary)

        if summary:
            calendar.summary = get_summary(summary)

        calendar.save()

        # filter out calendar dates with no or impossible date ranges
        good_calendar_dates = []
        for date in calendar_dates:
            date.calendar = calendar
            if not date.start_date:
                logger.warning(date)
                continue
            if date.end_date < date.start_date:
                logger.warning(date)
                continue
            good_calendar_dates.append(date)

        CalendarDate.objects.bulk_create(good_calendar_dates)

        for bank_holiday in bank_holidays.values():
            bank_holiday.calendar = calendar
        CalendarBankHoliday.objects.bulk_create(bank_holidays.values())

        self.calendar_cache[calendar_hash] = calendar

        return calendar

    @cache
    def get_note(self, note_code=None, note_text=None):
        return Note.objects.get_or_create(
            code=note_code or "", text=(note_text or "")[:255]
        )[0]

    def handle_journeys(
        self,
        route,
        route_created: bool,
        stops: dict,
        journeys,
        txc_service,
        operators: dict,
        operator_notes: dict,
    ):
        default_calendar = None

        stop_times = []

        trips = []
        trip_notes = []
        stop_time_notes = []

        for i, journey in enumerate(journeys):
            calendar = None
            if journey.operating_profile:
                calendar = self.get_calendar(
                    journey.operating_profile, txc_service.operating_period
                )
            elif journey.journey_pattern.operating_profile:
                calendar = self.get_calendar(
                    journey.journey_pattern.operating_profile,
                    txc_service.operating_period,
                )
            elif txc_service.operating_profile:
                if not default_calendar:
                    default_calendar = self.get_calendar(
                        txc_service.operating_profile, txc_service.operating_period
                    )
                calendar = default_calendar
            else:
                calendar = None

            operator_ref = journey.operator or txc_service.operator

            trip = Trip(
                inbound=journey.journey_pattern.direction
                in ("inbound", "anticlockwise"),
                calendar=calendar,
                route=route,
                journey_pattern=journey.journey_pattern.id,
                vehicle_journey_code=journey.code or "",
                ticket_machine_code=journey.ticket_machine_journey_code or "",
                sequence=journey.sequencenumber,
                operator=operators.get(operator_ref),
            )

            if journey.block and journey.block.code:
                trip.block = journey.block.code
                if (
                    trip.operator
                    and trip.block[:1] == "B"
                    and trip.block < "B99"
                    and trip.operator.noc in ("SNDR", "OBUS", "LYNX")
                ):
                    trip.block = None
            elif (
                journey.journey_pattern
                and journey.journey_pattern.block
                and journey.journey_pattern.block.code
            ):
                trip.block = journey.journey_pattern.block.code

            if journey.vehicle_type and journey.vehicle_type.code:
                if journey.vehicle_type.code not in self.vehicle_types:
                    (
                        self.vehicle_types[journey.vehicle_type.code],
                        _,
                    ) = VehicleType.objects.get_or_create(
                        code=journey.vehicle_type.code,
                        description=journey.vehicle_type.description or "",
                    )
                trip.vehicle_type = self.vehicle_types[journey.vehicle_type.code]

            if journey.garage_ref:
                trip.garage = self.garages.get(journey.garage_ref)

            blank = False
            for sequence, cell in enumerate(journey.get_times()):
                stop_time = get_stop_time(trip, cell, stops)
                if stop_time.sequence is None:
                    stop_time.sequence = sequence
                stop_times.append(stop_time)

                if not stop_time.timing_status:
                    blank = True

                if cell.notes:
                    for note_code, note_text in cell.notes:
                        note = self.get_note(note_code, note_text)
                        if not any(
                            tn.trip is trip and tn.note is note for tn in trip_notes
                        ):
                            trip_notes.append(Trip.notes.through(trip=trip, note=note))
                        if not any(
                            stn.stoptime is stop_time and stn.note is note
                            for stn in stop_time_notes
                        ):
                            stop_time_notes.append(
                                StopTime.notes.through(stoptime=stop_time, note=note)
                            )

            # last stop
            if not stop_time.arrival:
                stop_time.arrival = stop_time.departure
                stop_time.departure = None

            trip.end = stop_time.arrival_or_departure()
            trips.append(trip)

            if trip.start == trip.end:
                logger.warning(f"{route.code} trip {trip} takes no time")

            if blank and any(stop_time.timing_status for stop_time in stop_times):
                # not all timing statuses are blank - mark any blank ones as minor
                for stop_time in stop_times:
                    if not stop_time.timing_status:
                        stop_time.timing_status = "OTH"

            for note_code, note_text in journey.notes.items():
                note = self.get_note(note_code, note_text)
                if not (
                    # trip_notes
                    # and trip_notes[-1].trip is trip
                    any(tn.trip is trip and tn.note is note for tn in trip_notes)
                ):
                    trip_notes.append(Trip.notes.through(trip=trip, note=note))

            if operator_ref in operator_notes:
                note = self.get_note(note_text=operator_notes[operator_ref])
                trip_notes.append(Trip.notes.through(trip=trip, note=note))

            if journey.frequency_interval:
                if len(journeys) > i + 1:
                    next_journey = journeys[i + 1]
                    if journey.frequency_interval != next_journey.frequency_interval:
                        logger.info(
                            "frequency: %s - every %s - %s",
                            trip.start,
                            journey.frequency_interval,
                            journey.frequency_end_time,
                        )
                        # trip repeats every 10 minutes, for example:
                        while trip.start < journey.frequency_end_time:
                            trip = Trip(
                                inbound=trip.inbound,
                                calendar=trip.calendar,
                                route=trip.route,
                                journey_pattern=trip.journey_pattern,
                                operator=trip.operator,
                                start=trip.start + journey.frequency_interval,
                            )
                            journey.departure_time = trip.start
                            for cell in journey.get_times():
                                stop_time = get_stop_time(trip, cell, stops)
                                stop_times.append(stop_time)
                            trip.end = stop_time.arrival_or_departure()
                            trips.append(trip)

        if not route_created:
            # reuse trip ids if the number and start times haven't changed
            existing_trips = route.trip_set.order_by("id")
            try:
                if len(existing_trips) == len(trips):
                    for trip, old_trip in zip(trips, existing_trips):
                        if old_trip.start == trip.start:
                            trip.id = old_trip.id
                        else:
                            logger.info(f"{route.code} {old_trip.start} {trip.start}")
                            existing_trips.delete()
                            existing_trips = None
                            break
                else:
                    existing_trips.delete()
                    existing_trips = None
            except IntegrityError:
                existing_trips.delete()
                existing_trips = None
        else:
            existing_trips = None

        if existing_trips:
            Trip.objects.bulk_update(
                trips,
                fields=[
                    "inbound",
                    "journey_pattern",
                    "vehicle_journey_code",
                    "ticket_machine_code",
                    "block",
                    "destination",
                    "calendar",
                    "sequence",
                    "end",
                    "garage",
                    "vehicle_type",
                    "operator",
                ],
                batch_size=1000,
            )
            existing_trips = [t.id for t in existing_trips]
            Trip.notes.through.objects.filter(trip__in=existing_trips).delete()
            StopTime.objects.filter(trip__in=existing_trips).delete()
        else:
            Trip.objects.bulk_create(trips, batch_size=1000)

        Trip.notes.through.objects.bulk_create(trip_notes, batch_size=1000)

        for stop_time in stop_times:
            stop_time.trip = stop_time.trip  # set trip_id
        StopTime.objects.bulk_create(stop_times, batch_size=1000)

        StopTime.notes.through.objects.bulk_create(stop_time_notes, batch_size=1000)

    def should_defer_to_other_source(self, operators: dict, line_name: str):
        nocs = {operator.noc for operator in operators.values()}

        go_ahead_london_nocs = {
            "LGEN",
            "LONC",
            "MBGA",
            "BTRI",
            "DLBU",
            "KNCO",
            "GAHL",
        }

        if not (self.source.is_tnds() or self.source.name == "TfGM"):
            if not nocs.isdisjoint(go_ahead_london_nocs):
                # defer to TfL source
                return Route.objects.filter(
                    source__name="L",
                    service__current=True,
                    service__operator__in=go_ahead_london_nocs,
                    line_name__iexact=line_name,
                ).exists()

            return False
        elif self.source.name == "L":  # TfL data is always best
            return False
        elif not operators:
            return False

        if self.source.name != "TfGM":
            if any(noc not in self.incomplete_operators for noc in nocs):
                return False  # jointly-operated service?

        if "FHAL" in nocs:
            nocs.add("FHUD")
        elif "FHUD" in nocs:
            nocs.add("FHAL")

        return Route.objects.filter(
            ~Q(source=self.source),
            service__current=True,
            service__operator__in=nocs,
            line_name__iexact=line_name,
        ).exists()

    def handle_service(
        self, filename: str, transxchange, txc_service, stops: dict, file_hash=None
    ):
        skip_journeys = False

        if (
            txc_service.operating_period.end
            and txc_service.operating_period.end < txc_service.operating_period.start
        ):
            logger.warning(
                f"{filename} {txc_service.service_code}: "
                f"end {txc_service.operating_period.end} is before start {txc_service.operating_period.start}"
            )

        if (
            txc_service.operating_period.end
            and txc_service.operating_period.end < self.today
        ):
            logger.warning(
                f"{filename}: {txc_service.service_code} end {txc_service.operating_period.end} is in the past"
            )
            skip_journeys = True

        operators = self.get_operators(transxchange, txc_service)

        operator_notes = {
            element.get("id"): element.findtext("Note")
            for element in transxchange.operators
            if element.findtext("Note")
        }

        if "-Dublin_Express-" in filename:
            # defer to Transport for Ireland open data
            return

        if self.source.is_tnds():
            if self.source.name != "L":
                if operators and all(
                    operator.noc in self.open_data_operators
                    for operator in operators.values()
                ):
                    return
        elif self.source.name.startswith("Arriva") and "tfl_" in filename:
            logger.info(
                f"skipping {filename} {txc_service.service_code} (Arriva London)"
            )
            return

        description = get_description(txc_service)

        if description == "Origin - Destination":
            description = ""

        if re.match(r"^P[BCDFGHKM]\d+:\d+.*$", txc_service.service_code) or re.match(
            r"^UZ[a-zA-Z0-9]+:.*$", txc_service.service_code
        ):
            unique_service_code = txc_service.service_code
        else:
            unique_service_code = None

        service = None

        for line in txc_service.lines:
            line.line_name = line.line_name.replace("_", " ")

            # prefer a BODS-type source over TNDS
            if self.should_defer_to_other_source(operators, line.line_name):
                continue

            existing = None

            services = Service.objects.order_by("-current", "id").filter(
                Q(line_name__iexact=line.line_name)
                | Exists(
                    Route.objects.filter(
                        line_name__iexact=line.line_name, service=OuterRef("id")
                    )
                )
            )

            if operators:
                q = Q(operator__in=operators.values())

                # prevent certain seemingly-the-same services being merged
                if (
                    description
                    and self.source.name.startswith("Stagecoach")
                    and (
                        line.line_name == "1"
                        and "Chester" in description
                        or (
                            line.line_name == "59"
                            and self.source.name == "Stagecoach East Scotland"
                        )
                        or line.line_name == "700"
                    )
                ):
                    q = (Q(source=self.source) | q) & Q(description=description)
                existing = services.filter(q)
            else:
                existing = services

            if len(transxchange.services) == 1:
                has_stop_time = Exists(
                    StopTime.objects.filter(
                        stop__in=stops, trip__route__service=OuterRef("id")
                    )
                )
                has_stop_usage = Exists(
                    StopUsage.objects.filter(stop__in=stops, service=OuterRef("id"))
                )
                has_no_route = ~Exists(
                    Trip.objects.filter(route__service=OuterRef("id"))
                )
                condition = has_stop_time | (has_stop_usage & has_no_route)
            else:
                condition = Exists(
                    Route.objects.filter(
                        service_code=txc_service.service_code,
                        service=OuterRef("id"),
                    )
                )
                if description:
                    condition |= Q(description=description)

            existing = existing.filter(condition).first()

            service_code = None

            if self.source.is_tnds():
                service_code = get_service_code(filename)
                if service_code is None:
                    service_code = txc_service.service_code

                if not existing:
                    if service_code[:4] == "tfl_":
                        existing = self.source.service_set.filter(
                            Q(service_code=service_code)
                            | Q(line_name__iexact=line.line_name)
                            & (
                                Q(description=description)
                                | Q(operator__in=operators.values())
                            )
                        ).first()
                    else:
                        existing = self.source.service_set.filter(
                            Q(
                                service_code=service_code,
                                operator__in=operators.values(),
                            )
                            | Q(
                                description=description,
                                line_name__iexact=line.line_name,
                            )
                        ).first()
            elif unique_service_code:
                service_code = unique_service_code

                if not existing:
                    # try getting by BODS profile compliant service code
                    existing = services.filter(service_code=service_code).first()

            if existing:
                service = existing
                existing_current_service = existing.current
            else:
                service = Service()
                existing_current_service = False

            service.line_name = line.line_name
            service.source = self.source
            service.current = True

            journeys = transxchange.get_journeys(txc_service.service_code, line.id)

            if not journeys:
                logger.warning(f"{txc_service.service_code} has no journeys")
                continue

            if txc_service.mode:
                service.mode = txc_service.mode

            if self.region_id:
                service.region_id = self.region_id

            if service_code:
                service.service_code = service_code

            if description and (
                not service.description
                or "Origin - " not in description
                and " - Destination" not in description
            ):
                service.description = description

            if line.colour:
                background = f"#{line.colour}"
                foreground = get_text_colour(background) or "#000"
                service.colour, _ = ServiceColour.objects.get_or_create(
                    background=background,
                    foreground=foreground,
                    use_name_as_brand=False,
                )
            elif service_code and service.mode == "bus" and service_code[:4] == "tfl_":
                # London bus red
                service.colour_id = 127
            else:
                # use the operator's colour
                for operator in operators.values():
                    if operator.colour_id:
                        service.colour_id = operator.colour_id
                        break

            # Lines and Services can have a MarketingName
            # (a line_brand is a thing I've made up)

            line_brand = line.line_brand or line.marketing_name
            if line_brand:
                logger.info(line_brand)

            if txc_service.marketing_name:
                logger.info(txc_service.marketing_name)
                if txc_service.marketing_name in (
                    "CornwallbyKernow",
                    line.line_name,
                ):
                    pass
                elif (
                    "tudents only" in txc_service.marketing_name
                    or "pupils only" in txc_service.marketing_name
                ):
                    service.public_use = False
                else:
                    line_brand = txc_service.marketing_name

                    if service.description and " [" in service.description:
                        service.description = service.description.removesuffix(
                            f" [{line_brand}]"
                        )

            if (
                not line_brand
                and service.colour
                and service.colour.use_name_as_brand
                and service.colour.name
                and service.colour.name != service.line_name
            ):
                # e.g. (First Eastern Counties) 'Yellow Line'
                line_brand = service.colour.name
            if any(line_brand == operator.name for operator in operators.values()):
                line_brand = ""

            if line_brand:
                service.line_brand = line_brand
            elif not existing_current_service:
                service.line_brand = ""

            # inbound and outbound descriptions

            if (
                line.outbound_description != line.inbound_description
                or txc_service.origin in STUPID_ORIGINS_DESTINATIONS
            ):
                out_desc = line.outbound_description
                in_desc = line.inbound_description

                if out_desc and in_desc and out_desc.isupper() and in_desc.isupper():
                    out_desc = titlecase(out_desc, callback=initialisms)
                    in_desc = titlecase(in_desc, callback=initialisms)

                if out_desc:
                    if not service.description or len(txc_service.lines) > 1:
                        service.description = out_desc
                if in_desc:
                    if not service.description:
                        service.description = in_desc

            service.save()

            if operators:
                if existing and not existing_current_service:
                    service.operator.set(operators.values())
                else:
                    service.operator.add(*operators.values())

            self.service_ids.add(service.id)

            journey = journeys[0]

            ticket_machine_service_code = (
                journey.ticket_machine_service_code
                or txc_service.ticket_machine_service_code
            )
            if (
                ticket_machine_service_code
                and ticket_machine_service_code != line.line_name
            ):
                try:
                    ServiceCode.objects.create(
                        scheme="SIRI", code=ticket_machine_service_code, service=service
                    )
                except IntegrityError:
                    pass

            # a code used in Traveline Cymru URLs:
            if self.source.name == "W" and "_" not in txc_service.service_code:
                private_code = journey.private_code
                if private_code and ":" in private_code:
                    ServiceCode.objects.update_or_create(
                        {"code": private_code.split(":", 1)[0]},
                        service=service,
                        scheme="Traveline Cymru",
                    )

            # timetable data:

            route_defaults = {
                "line_id": line.id,
                "line_name": line.line_name,
                "line_brand": line_brand or "",
                "outbound_description": line.outbound_description or "",
                "inbound_description": line.inbound_description or "",
                "start_date": txc_service.operating_period.start,
                "end_date": txc_service.operating_period.end,
                "service": service,
                "revision_number": transxchange.attributes["RevisionNumber"],
                "revision_number_context": "",
                "created_at": get_datetime(transxchange.attributes["CreationDateTime"]),
                "modified_at": get_datetime(
                    transxchange.attributes["ModificationDateTime"]
                ),
                "service_code": txc_service.service_code,
                "version": self.version,
                "source": self.source,
            }

            match txc_service.public_use:
                case "0" | "false":
                    route_defaults["public_use"] = False
                case "1" | "true":
                    route_defaults["public_use"] = True
                case _:
                    route_defaults["public_use"] = None

            for key in ("outbound_description", "inbound_description"):
                if len(route_defaults[key]) > 255:
                    logger.warning(f"{key} too long in {filename}")
                    route_defaults[key] = route_defaults[key][:255]

            if (
                txc_service.origin
                and txc_service.origin not in STUPID_ORIGINS_DESTINATIONS
            ):
                route_defaults["origin"] = txc_service.origin
            else:
                route_defaults["origin"] = ""

            if (
                txc_service.destination
                and txc_service.destination not in STUPID_ORIGINS_DESTINATIONS
            ):
                if " via " in txc_service.destination:
                    (
                        route_defaults["destination"],
                        route_defaults["via"],
                    ) = txc_service.destination.split(" via ", 1)
                else:
                    route_defaults["destination"] = txc_service.destination
            else:
                route_defaults["destination"] = ""

            if txc_service.vias:
                route_defaults["via"] = ", ".join(txc_service.vias)

            if description:
                route_defaults["description"] = description

            if unique_service_code:
                registration = get_registration(unique_service_code)
                if registration:
                    route_defaults["registration"] = registration

            # route links (geometry between stops):
            if transxchange.route_sections:
                do_route_links(journeys, transxchange, stops, service)

            route_code = filename
            if len(transxchange.services) > 1:
                route_code += f"#{txc_service.service_code}"
            if len(txc_service.lines) > 1:
                route_code += f"#{line.id}"

            # diabolical trick - detect Ticketer data and set revision_number_context
            if "tkt_oid" in operators:
                parts = route_code.split("_")
                if len(parts) > 5:
                    route_defaults["revision_number_context"] = parts[1]
                else:
                    logger.warning(
                        f"{filename} has {operators} but unexpected filename format"
                    )
            # detect TransMach data too, and do likewise
            elif "_" in route_code and route_code.endswith(".xml"):
                parts = route_code.split("_")
                if (
                    len(parts) == 2
                    and parts[1][:-4].isdigit()
                    and parts[0] == line.line_name.upper()
                ):
                    route_defaults["revision_number_context"] = parts[0]
                    logger.info(f"{filename} looks like TransMach")
            # thrice wise, Merseytravel
            elif route_code.startswith("mer_"):
                route_defaults["revision_number_context"] = route_defaults["line_name"]

            if file_hash:
                route_defaults["file_hash"] = file_hash

            route_defaults["code"] = route_code

            # TfGM: all files are in a subfolder
            # with a name like "TfGM TXC 260225 OpenData" that changes every release
            # even if the file didn't change

            if file_hash:
                existing_route = self.source.route_set.filter(
                    code=route_code, file_hash=file_hash
                ).first()

                if not existing_route and "/" in route_code:
                    part = route_code.split("/")[-1]

                    existing_route = self.source.route_set.filter(
                        version=self.version,
                        code__endswith=f"/{part}",
                        file_hash=file_hash,
                    ).first()

                if existing_route:
                    route, route_created = Route.objects.update_or_create(
                        route_defaults, id=existing_route.id
                    )
                    self.route_ids.add(route.id)
                    # file hasn't changed, so we don't need to re-load journeys
                    continue

            route, route_created = Route.objects.update_or_create(
                route_defaults, code=route_code, source=self.source
            )

            self.route_ids.add(route.id)

            if not skip_journeys:
                self.handle_journeys(
                    route,
                    route_created,
                    stops,
                    journeys,
                    txc_service,
                    operators,
                    operator_notes,
                )

    def do_stops(self, transxchange_stops: dict) -> dict:
        stops = list(transxchange_stops.keys())
        for atco_code in transxchange_stops:
            # deal with leading 0 being removed by Microsoft Excel maybe
            if atco_code.isdigit() and atco_code[:1] != "0" and atco_code[2:3] == "0":
                stops.append(f"0{atco_code}")

            # rail services in the London dataset
            if atco_code[:3] == "910":
                stops.append(atco_code[:-1])

        stops = (
            StopPoint.objects.annotate(atco_code_upper=Upper("atco_code"))
            .filter(atco_code_upper__in=stops)
            .only("atco_code", "latlong")
            .order_by()
        )
        stops = {stop.atco_code_upper: stop for stop in stops}

        ad_hoc_stops = {}

        for atco_code, stop in transxchange_stops.items():
            atco_code_upper = atco_code.upper()
            if atco_code_upper not in stops:
                if (
                    atco_code.isdigit() and f"0{atco_code}" in stops
                ):  # "36006002112" = "036006002112"
                    logger.warning(f"{atco_code} 0{atco_code}")
                    stops[atco_code_upper] = stops[f"0{atco_code}"]
                elif atco_code[:3] == "910" and atco_code[:-1] in stops:
                    stops[atco_code_upper] = stops[atco_code[:-1]]
                else:
                    stoppoint = get_stop(
                        stop.element, f"{self.source.id}:{atco_code_upper}"
                    )
                    stoppoint.common_name = str(stop)[:48]
                    stoppoint.source = self.source
                    stoppoint.timing_status = ""
                    stoppoint.bus_stop_type = ""
                    stoppoint.stop_type = ""
                    ad_hoc_stops[atco_code_upper] = stoppoint
                    stops[atco_code_upper] = stoppoint

        StopPoint.objects.bulk_create(
            ad_hoc_stops.values(),
            update_conflicts=True,
            unique_fields=["atco_code"],
            update_fields=["common_name", "naptan_code", "latlong", "bearing"],
        )

        return stops

    def do_garages(self, garages):
        for garage_code in garages:
            garage = garages[garage_code]

            name = garage.findtext("GarageName", "")
            if name == f"Garage '{garage_code}'":  # "Garage 'KB'"
                name = ""
            else:
                name = name.removesuffix(" Bus Depot")
                name = (
                    name.removesuffix(" depot")
                    .removesuffix(" Depot")
                    .removesuffix(" DEPOT")
                )
                name = (
                    name.removesuffix(" garage")
                    .removesuffix(" Garage")
                    .removesuffix(" GARAGE")
                    .strip()
                )

            if (
                garage_code not in self.garages
                or self.garages[garage_code].name != name
            ):
                garage = Garage.objects.filter(
                    code=garage_code, name__iexact=name
                ).first()
                if garage is None:
                    garage = Garage.objects.create(code=garage_code, name=name)
                self.garages[garage_code] = garage

    def handle_file(self, open_file, filename: str):
        sha1 = hashlib.sha1(usedforsecurity=False)
        while chunk := open_file.read(65536):
            sha1.update(chunk)
        file_hash = sha1.hexdigest()
        open_file.seek(0)

        transxchange = TransXChange(open_file)

        if not transxchange.journeys:
            logger.warning(f"{filename or open_file} has no journeys")
            return

        self.vehicle_types = {}

        stops = self.do_stops(transxchange.stops)

        self.do_garages(transxchange.garages)

        for txc_service in transxchange.services.values():
            self.handle_service(filename, transxchange, txc_service, stops, file_hash)
