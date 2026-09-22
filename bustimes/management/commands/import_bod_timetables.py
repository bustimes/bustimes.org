"""Import timetable data "fresh from the cow" """

import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from time import sleep
from urllib.parse import parse_qs

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DataError
from django.db.models import Exists, OuterRef, Q

from busstops.models import DataSource, Service

from ...download_utils import download, download_if_modified
from ...models import Route, TimetableDataSource, Trip, is_noc
from ...utils import get_sha1, log_time_taken
from .import_transxchange import Command as TransXChangeCommand

logger = logging.getLogger(__name__)


# delete routes from any sources that have been made inactive
def clean_up(timetable_data_source, current_sources, incomplete=False):
    # if there's no current data, it's probably an error, so don't automatically delete
    if not Service.objects.filter(
        Q(source__in=current_sources) | Q(route__source__in=current_sources),
        current=True,
    ).exists():
        if Service.objects.filter(
            current=True,
            route__source__source=timetable_data_source,
        ).exists():
            logger.warning(
                f"""{timetable_data_source} has no current data
https://bustimes.org/admin/busstops/service/?route__source__source={timetable_data_source.id}"""
            )
        return

    service_operators = Service.operator.through.objects.filter(
        service=OuterRef("service")
    )
    operators = timetable_data_source.operators.values_list("noc", flat=True)

    routes = Route.objects.filter(
        ~Q(source__in=current_sources),
        Q(source__source=timetable_data_source)
        | Q(
            ~Q(source__name="L"),
            ~Q(source__url=""),
            Exists(service_operators.filter(operator__in=operators)),
            ~Exists(
                service_operators.filter(~Q(operator__in=operators))
            ),  # exclude joint services
        ),
    )
    if incomplete:  # leave other sources alone
        routes = routes.filter(source__url__contains="bus-data.dft.gov.uk")
    # force evaluation of QuerySet:
    route_ids = list(routes.values_list("id", flat=True))
    routes = Route.objects.filter(id__in=route_ids)
    # do this first to prevent IntegrityError
    routes.update(service=None)
    # routes.delete()
    Service.objects.filter(
        ~Q(source__url=""),
        operator__in=operators,
        current=True,
        route=None,
    ).update(current=False)


def get_operator_ids(source) -> list:
    operators = (
        Trip.objects.filter(route__source=source, route__service__isnull=False)
        .values("operator_id")
        .distinct()
    )
    return [operator["operator_id"] for operator in operators]


def get_command():
    command = TransXChangeCommand()
    command.set_up()
    return command


def handle_file(command, path, qualify_filename=False):
    # the downloaded file might be plain XML, or a zipped archive - we just don't know yet
    full_path = settings.DATA_DIR / path

    command.start_task()

    try:
        with zipfile.ZipFile(full_path) as archive:
            for filename in archive.namelist():
                if filename.endswith(".csv") or "__MACOSX/" in filename:
                    continue
                with archive.open(filename) as open_file:
                    if qualify_filename:
                        # source has multiple versions (Passsenger) so add a prefix like 'gonortheast_123.zip/'
                        filename = str(Path(path) / filename)
                    try:
                        command.handle_file(open_file, filename)
                    except (ET.ParseError, ValueError, AttributeError, DataError):
                        if filename.endswith(".xml"):
                            logger.info(filename)
                            logger.exception("error handling file")
    except zipfile.BadZipFile:
        # plain XML
        with full_path.open("rb") as open_file:
            if qualify_filename:
                filename = path
            else:
                filename = ""
            try:
                command.handle_file(open_file, filename)
            except (AttributeError, DataError):
                logger.exception("error handling file")

    command.finish_task()

    if not qualify_filename:
        command.source.save_to_archive(full_path)


def get_bus_open_data_paramses(sources, api_key):
    # e.g. 'noc=TMTL&adminArea=092'
    searches = [s.search for s in sources if not is_noc(s.search)]

    # e.g. 'TMTL'
    nocs = [s.search for s in sources if is_noc(s.search)]

    # chunk – we will search for nocs 20 at a time
    nocses = [nocs[i : i + 20] for i in range(0, len(nocs), 20)]

    base_params = {
        "api_key": api_key,
        "status": "published",
        "limit": 100,
    }

    # and search paramses one at a time
    for search in searches:
        yield base_params | parse_qs(search)

    for nocs in nocses:
        yield base_params | {"noc": ",".join(nocs)}


def bus_open_data(api_key, specific_operator):
    assert len(api_key) == 40

    command = get_command()

    session = requests.Session()

    url_prefix = "https://data.bus-data.dft.gov.uk"
    path_prefix = settings.DATA_DIR / "bod"
    if not path_prefix.exists():
        path_prefix.mkdir()

    datasets = []

    timetable_data_sources = TimetableDataSource.objects.filter(
        ~Q(search=""), url="", active=True
    )
    if specific_operator:
        timetable_data_sources = timetable_data_sources.filter(name=specific_operator)
        if not timetable_data_sources:
            logger.info(f"no timetable data sources named {specific_operator}")
            return
        logger.info(timetable_data_sources)

    for params in get_bus_open_data_paramses(timetable_data_sources, api_key):
        url = f"{url_prefix}/api/v1/dataset/"
        while url:
            response = session.get(url, params=params, timeout=61)
            response.raise_for_status()
            json = response.json()
            results = json["results"]
            if not results:
                logger.warning(f"no results: {response.url}")
            for dataset in results:
                dataset["modified"] = datetime.fromisoformat(dataset["modified"])
                dataset["params"] = params
                datasets.append(dataset)
            url = json["next"]

    all_source_ids = []

    for source in timetable_data_sources:
        if not is_noc(source.search):
            params = parse_qs(source.search)
            operator_datasets = [
                item for item in datasets if (item["params"] | params) == item["params"]
            ]
        else:
            operator_datasets = [
                item for item in datasets if source.search in item["noc"]
            ]

        command.region_id = source.region_id

        sources = []
        service_ids = set()

        operators = source.operators.values_list("noc", flat=True)

        for dataset in operator_datasets:
            command.source = DataSource.objects.filter(url=dataset["url"]).first()
            if (
                not command.source
                and is_noc(source.search)
                and len(operator_datasets) == 1
            ):
                name_prefix = dataset["name"].split("_", 1)[0]
                # if old dataset was made inactive, reuse id
                command.source = DataSource.objects.filter(
                    name__startswith=f"{name_prefix}_"
                ).first()
            if not command.source:
                command.source = DataSource.objects.create(
                    name=dataset["name"], url=dataset["url"]
                )
            command.source.name = dataset["name"]
            command.source.description = dataset["description"]
            command.source.url = dataset["url"]
            if command.source.source_id != source.id:
                command.source.source = source
                if command.source.id:
                    command.source.save(update_fields=["source"])

            sources.append(command.source)

            if specific_operator or command.source.datetime != dataset["modified"]:
                logger.info(dataset["name"])

                filename = str(command.source.id)
                path = path_prefix / filename

                command.service_ids = set()
                command.route_ids = set()
                command.garages = {}

                command.source.datetime = dataset["modified"]

                with log_time_taken(logger):
                    download(path, url=command.source.url, session=session)

                    handle_file(command, path)

                    command.mark_old_services_as_not_current()

                    command.source.sha1 = get_sha1(path)
                    command.source.save()

                operator_ids = get_operator_ids(command.source)
                logger.info(f"  {operator_ids}")
                unexpected = [o for o in operator_ids if o not in operators]
                if unexpected:
                    logger.info(f"  {unexpected=} (not in {operators})")

                service_ids |= command.service_ids

        clean_up(source, sources, not source.complete)

        command.service_ids = service_ids
        command.finish_services()
        all_source_ids += [source.id for source in sources]

    if not specific_operator:
        to_delete = DataSource.objects.filter(
            ~Q(id__in=all_source_ids),
            ~Exists(Route.objects.filter(source=OuterRef("id"))),
            url__startswith=f"{url_prefix}/timetable/",
        )
        if to_delete:
            logger.info(f"{to_delete=}")
            for source in to_delete:  # one by one to use less memory
                logger.info(source.calendar_set.exclude(trip=None).update(source=None))
                logger.info(source.stoppoint_set.all().delete())
                logger.info(source.delete())


def ticketer(specific_operator=None):
    command = get_command()

    session = requests.Session()

    base_dir = settings.DATA_DIR / "ticketer"

    if not base_dir.exists():
        base_dir.mkdir()

    timetable_data_sources = TimetableDataSource.objects.filter(
        url__startswith="https://opendata.ticketer.com", active=True
    )
    if specific_operator:
        timetable_data_sources = timetable_data_sources.filter(
            operators=specific_operator
        )
        if not timetable_data_sources:
            logger.info(f"no timetable data sources for noc {specific_operator}")
            return
        logger.info(timetable_data_sources)

    need_to_sleep = False

    for source in timetable_data_sources:
        path = Path(source.url)

        filename = f"{path.parts[3]}.zip"
        path = base_dir / filename
        command.source, _created = DataSource.objects.get_or_create(
            {"name": source.name}, url=source.url
        )
        command.source.source = source
        command.garages = {}

        if need_to_sleep:
            sleep(2)
            need_to_sleep = False

        _modified, last_modified = download_if_modified(path, command.source, session)

        if (
            specific_operator
            or not command.source.datetime
            or last_modified > command.source.datetime
        ):
            logger.info(f"{source} {last_modified}")

            sha1 = get_sha1(path)

            command.source.sha1 = sha1
            command.source.datetime = last_modified

            if existing := DataSource.objects.filter(
                url__contains=".gov.uk", sha1=sha1
            ):
                # hash matches that hash of some BODS data
                # (I think this is impossible these days)
                logger.info(f"  skipping, {sha1=} matches {existing=}")
            else:
                command.region_id = source.region_id
                command.service_ids = set()
                command.route_ids = set()

                with log_time_taken(logger):
                    handle_file(command, path)

                    command.mark_old_services_as_not_current()

                    clean_up(source, [command.source])

                    command.finish_services()

            command.source.save()

            logger.info(
                f"  {command.source.route_set.order_by('end_date').distinct('end_date').values('end_date')}"
            )
            logger.info(f"  {get_operator_ids(command.source)}")
        else:
            need_to_sleep = True


def do_stagecoach_source(command, last_modified, filename, nocs):
    logger.info(f"{command.source.url} {last_modified}")

    command.source.datetime = last_modified

    with log_time_taken(logger):
        handle_file(command, filename)

        command.mark_old_services_as_not_current()

    command.source.save()

    logger.info(
        f"  {command.source.route_set.order_by('end_date').distinct('end_date').values('end_date')}"
    )
    operators = get_operator_ids(command.source)
    logger.info(f"  {operators=}")
    unexpected = [o for o in operators if o not in nocs]
    if unexpected:
        logger.info(f"  {unexpected=} (not in {nocs})")


def stagecoach(specific_operator=None):
    command = get_command()

    session = requests.Session()

    timetable_data_sources = TimetableDataSource.objects.filter(
        Q(url__startswith="https://opendata.stagecoachbus.com/")
        | Q(url__endswith="/TfGMtxcnew.zip"),
        active=True,
    )
    if specific_operator:
        timetable_data_sources = timetable_data_sources.filter(
            operators=specific_operator
        )
        if not timetable_data_sources:
            logger.info(f"no timetable data sources for noc {specific_operator}")
            return
        logger.info(timetable_data_sources)

    for source in timetable_data_sources:
        command.region_id = source.region_id
        command.service_ids = set()
        command.route_ids = set()
        command.garages = {}

        nocs = list(source.operators.values_list("noc", flat=True))

        filename = Path(source.url).name
        path = settings.DATA_DIR / filename

        command.source, _ = DataSource.objects.get_or_create(
            {"name": source.name}, url=source.url
        )

        modified, last_modified = download_if_modified(path, command.source, session)
        sha1 = get_sha1(path)

        if command.source.datetime != last_modified:
            modified = True

        if modified:
            # use sha1 checksum to check if file has really changed -
            # last_modified seems to change every night
            # even when contents stay the same
            if sha1 == command.source.sha1 or not command.source.older_than(
                last_modified
            ):
                modified = False

            command.source.sha1 = sha1

            if modified or specific_operator:
                do_stagecoach_source(command, last_modified, filename, nocs)

        clean_up(source, [command.source])
        command.finish_services()


class Command(BaseCommand):
    @staticmethod
    def add_arguments(parser):
        parser.add_argument("api_key", type=str)
        parser.add_argument("operator", type=str, nargs="?")

    def handle(self, api_key, operator, **options):
        if api_key == "stagecoach":
            stagecoach(operator)
        elif api_key == "ticketer":
            ticketer(operator)
        else:
            bus_open_data(api_key, operator)
