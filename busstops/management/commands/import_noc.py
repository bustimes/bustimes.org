import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import yaml
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from vosa.models import Licence

from ...models import DataSource, Operator, OperatorCode

logger = logging.getLogger(__name__)


def get_region_id(region_id):
    if region_id in {"ADMIN", "Admin", "Taxi", ""}:
        return "GB"
    if region_id.upper() in {"SC", "YO", "WA", "LO"}:
        return region_id[0]
    return region_id


def get_mode(mode):
    if not mode.isupper():
        mode = mode.lower()
    match mode:
        case "ct operator" | "ct operaor" | "CT":
            return "community transport"
        case "DRT":
            return "demand responsive transport"
        case "partly drt":
            return "partly DRT"
    return mode


def get_operator_codes(
    code_sources: tuple[tuple[str, DataSource]], noc, operator, noc_line
):
    # "National Operator Codes"
    operator_codes = [
        OperatorCode(source=code_sources[0][1], code=noc, operator=operator)
    ]

    # "L", "SW", "WM" etc
    for col, source in code_sources[1:]:
        code = noc_line.find(col).text
        if code:
            code = code.removeprefix("=")
            if code != noc:
                operator_codes.append(
                    OperatorCode(
                        source=source,
                        code=code,
                        operator=operator,
                    )
                )
    return operator_codes


def get_operator_licences(operator, noc_line, licences_by_number):
    licence_number = noc_line.findtext("Licence")
    if licence_number in licences_by_number:
        return [
            Operator.licences.through(
                operator=operator,
                licence=licences_by_number[licence_number],
            )
        ]
    return []


class Command(BaseCommand):
    @transaction.atomic()
    def handle(self, **kwargs):
        # this does 12+ database queries could be reduced but it's not worth it:
        code_sources = [
            (col, DataSource.objects.get_or_create(name=name)[0])
            for col, name in (
                ("NOCCODE", "National Operator Codes"),
                ("LO", "L"),
                ("SW", "SW"),
                ("WM", "WM"),
                ("WA", "W"),
                ("YO", "Y"),
                ("NW", "NW"),
                ("NE", "NE"),
                ("SC", "S"),
                ("SE", "SE"),
                ("EA", "EA"),
                ("EM", "EM"),
            )
        ]
        noc_source = code_sources[0][1]

        url = "https://www.travelinedata.org.uk/noc/api/1.0/nocrecords.xml"
        response = requests.get(url, timeout=61)
        element = ET.fromstring(response.text)

        generation_date = datetime.fromisoformat(element.attrib["generationDate"])
        if generation_date == noc_source.datetime:
            return

        noc_source.datetime = generation_date
        noc_source.save(
            update_fields=["datetime"]
        )  # ok to do this now cos we're inside a transaction

        operators = Operator.objects.prefetch_related(
            "operatorcode_set", "licences"
        ).in_bulk()

        merged_operator_codes = {
            code.code: code
            for operator in operators.values()
            for code in operator.operatorcode_set.all()
            if code.source_id == noc_source.id and code.code != operator.noc
        }

        # all licences (not just ones with operators attached)
        licences_by_number = Licence.objects.in_bulk(field_name="licence_number")

        with open(settings.BASE_DIR / "fixtures" / "operators.yaml") as open_file:
            overrides = yaml.safe_load(open_file)

        operators_by_slug = {operator.slug: operator for operator in operators.values()}

        public_names = {}
        for e in element.find("PublicName"):
            e_id = e.findtext("PubNmId")
            assert e_id not in public_names
            public_names[e_id] = e

        noc_lines = {
            line.findtext("NOCCODE").removeprefix("="): line
            for line in element.find("NOCLines")
        }

        to_create = []
        to_update = []
        operator_codes = []
        operator_licences = []

        for e in element.find("NOCTable"):
            noc = e.findtext("NOCCODE").removeprefix("=")

            if (noc_line := noc_lines.get(noc)) is None:
                continue

            # another operator has that code as sort of an alias - bail
            if noc in merged_operator_codes:
                continue

            vehicle_mode = get_mode(noc_line.findtext("Mode"))
            if vehicle_mode == "airline":
                continue

            # op = operators_by_id[e.findtext("OpId")]
            public_name = public_names[e.findtext("PubNmId")]

            name = public_name.findtext("OperatorPublicName").strip()

            url = public_name.findtext("Website")
            if url:
                url = url.removesuffix("#")
                url = url.split("#")[-1]

            if noc in overrides:
                override_data = overrides[noc]

                if "url" in override_data:
                    if url == override_data["url"]:
                        logger.warning("%s url %s no longer needs overriding", noc, url)
                    url = override_data["url"]

                if "name" in override_data:
                    if name == override_data["name"]:
                        logger.warning(
                            "%s name %s no longer needs overriding", noc, name
                        )
                    name = override_data["name"]

            if noc not in operators:
                operators[noc] = Operator(
                    noc=noc,
                    name=name,
                    region_id=get_region_id(noc_line.findtext("TLRegOwn")),
                    vehicle_mode=vehicle_mode,
                )
                operator = operators[noc]

                slug = slugify(operator.name)
                base_slug = slug
                suffix = 0
                while slug in operators_by_slug:
                    suffix += 1
                    slug = f"{base_slug}-{suffix}"
                operator.slug = slug
                operators_by_slug[slug] = operator
                to_create.append(operator)

                operator.url = url

                operator_codes += get_operator_codes(
                    code_sources, noc, operator, noc_line
                )
                operator_licences += get_operator_licences(
                    operator, noc_line, licences_by_number
                )

            else:
                # update existing operator

                operator = operators[noc]

                if (
                    name != operator.name
                    or url != operator.url
                    or vehicle_mode != operator.vehicle_mode
                ):
                    operator.name = name
                    operator.url = url
                    operator.vehicle_mode = vehicle_mode
                    to_update.append(operator)

                if not operator.licences.all():
                    operator_licences += get_operator_licences(
                        operator, noc_line, licences_by_number
                    )

            operator.source = noc_source
            operator.modified_at = generation_date

            try:
                operator.clean_fields(exclude=["noc", "slug", "region", "source"])
            except ValidationError as e:
                if "url" in e.message_dict:
                    # print(e, operator.url)
                    operator.url = ""
                else:
                    print(noc, e)

        Operator.objects.bulk_create(to_create)
        Operator.objects.bulk_update(
            to_update, ("url", "name", "vehicle_mode", "modified_at")
        )

        OperatorCode.objects.bulk_create(operator_codes)
        Operator.licences.through.objects.bulk_create(operator_licences)
