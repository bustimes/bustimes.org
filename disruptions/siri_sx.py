import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import datetime

import requests
from django.db import transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q
from django.utils.dateparse import parse_duration

from busstops.models import AdminArea, DataSource, Operator, Service, StopPoint

from .models import (
    Consequence,
    Link,
    Situation,
    ValidityPeriod,
    delete_old_situations,
)

logger = logging.getLogger(__name__)


def get_period(element):
    start = element.find("StartTime").text
    end = element.findtext("EndTime")
    return DateTimeTZRange(start, end, "[]")


def get_operators(operator_ref):
    return Operator.objects.filter(
        operatorcode__code=operator_ref,
        operatorcode__source__name="National Operator Codes",
    )


def match_localities(services, *texts):
    """If exactly one of the services serves a locality named in the text, return it"""
    text = " ".join(filter(None, texts))
    localities = defaultdict(set)
    for service_id, name in (
        StopPoint.objects.filter(service__in=services, locality__isnull=False)
        .values_list("service", "locality__name")
        .distinct()
    ):
        localities[service_id].add(name)
    matches = [
        service
        for service in services
        if any(
            re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE)
            for name in localities[service.id]
        )
    ]
    if len(matches) == 1:
        return matches
    return []


def handle_item(item: ET.Element, source: DataSource, current_situations: dict):
    situation_number = item.findtext("SituationNumber")

    item.find("Source/TimeOfCommunication").text = None

    xml = ET.tostring(item, encoding="unicode")

    situation = current_situations.get(situation_number)

    if situation:
        if situation.data == xml:
            return situation.id  # hasn't changed
        created = False
    else:
        situation = Situation(source=source, situation_number=situation_number)
        created = True

    situation.current = True

    situation.data = xml
    situation.created_at = datetime.fromisoformat(item.find("CreationTime").text)
    if modified_at := item.findtext("VersionedAtTime"):
        situation.modified_at = datetime.fromisoformat(modified_at)
    situation.publication_window = get_period(item.find("PublicationWindow"))

    assert item.findtext("Progress") == "open"

    reason = (
        item.findtext("MiscellaneousReason")
        or item.findtext("EquipmentReason")
        or item.findtext("EnvironmentReason")
        or item.findtext("PersonnelReason")
    )
    if reason:
        situation.reason = reason

    situation.participant_ref = item.find("ParticipantRef").text
    situation.summary = item.find("Summary").text
    situation.text = item.find("Description").text
    if situation.text == situation.summary:
        situation.text = ""
    else:
        situation.text = situation.text.removeprefix(f"{situation.summary}\n\n")
    situation.save()

    for i, link_element in enumerate(item.findall("InfoLinks/InfoLink/Uri")):
        link = Link(situation=situation)
        if not created and i == 0:
            try:
                link = situation.link_set.get()
            except Link.MultipleObjectsReturned:
                situation.link_set.all().delete()
            except Link.DoesNotExist:
                pass
        if link_element.text:
            link.url = link_element.text
            link.save()

    for i, period_element in enumerate(item.findall("ValidityPeriod")):
        period = ValidityPeriod(situation=situation)
        if not created and i == 0:
            try:
                period = situation.validityperiod_set.get()
            except ValidityPeriod.MultipleObjectsReturned:
                situation.validityperiod_set.all().delete()
            except ValidityPeriod.DoesNotExist:
                pass
        period.period = get_period(period_element)
        period.save()

    for i, consequence_element in enumerate(item.find("Consequences")):
        consequence = Consequence(situation=situation)
        if not created and i == 0:
            try:
                consequence = situation.consequence_set.get()
            except Consequence.MultipleObjectsReturned:
                situation.consequence_set.all().delete()
            except Consequence.DoesNotExist:
                pass

        had_places = "<AffectedPlace>" in consequence.data

        consequence.text = consequence_element.find("Advice/Details").text
        consequence.data = ET.tostring(consequence_element, encoding="unicode")
        consequence.severity = consequence_element.findtext("Severity") or ""
        consequence.vehicle_mode = (
            consequence_element.findtext("Affects/Networks/AffectedNetwork/VehicleMode")
            or ""
        )
        consequence.direction = (
            consequence_element.findtext(
                "Affects/Networks/AffectedNetwork/AffectedLine/Direction/DirectionRef"
            )
            or ""
        )
        if delay := consequence_element.findtext("Delays/Delay"):
            consequence.delay = parse_duration(delay)
        else:
            consequence.delay = None
        consequence.save()

        area_refs = [
            place.findtext("PlaceRef")
            for place in consequence_element.findall("Affects/Places/AffectedPlace")
            if place.findtext("PlaceCategory") == "AdministrativeArea"
        ]
        if area_refs or had_places:
            consequence.admin_areas.set(
                AdminArea.objects.filter(
                    id__in=[int(ref) for ref in area_refs if ref.isdigit()]
                )
            )

        stops = consequence_element.findall("Affects/StopPoints/AffectedStopPoint")
        stops = [stop.find("StopPointRef").text for stop in stops]
        stops = StopPoint.objects.filter(atco_code__in=stops)
        consequence.stops.set(stops)

        consequence.services.clear()

        services = Service.objects.filter(current=True)
        stops_filter = Q(stops__in=stops)

        for line in consequence_element.findall(
            "Affects/Networks/AffectedNetwork/AffectedLine"
        ):
            line_name = line.findtext("PublishedLineName") or line.findtext("LineRef")
            line_name = line_name.replace("_", " ")
            line_filter = Q(route__line_name__iexact=line_name) | Q(
                line_name__iexact=line_name
            )
            for operator_ref in line.findall("AffectedOperator/OperatorRef"):
                operator_ref = operator_ref.text

                matching_services = services.filter(
                    line_filter, operator__in=get_operators(operator_ref)
                ).distinct()
                candidates = len(matching_services)
                if candidates > 1:
                    narrowed = matching_services.filter(stops_filter) if stops else ()
                    if not narrowed:
                        narrowed = match_localities(
                            matching_services,
                            situation.summary,
                            situation.text,
                            consequence.text,
                        )
                    if narrowed:
                        matching_services = narrowed
                    else:
                        logger.warning(
                            "ambiguous line %s %s",
                            operator_ref,
                            line_name,
                            extra={
                                "situation_number": situation_number,
                                "summary": situation.summary,
                                "candidates": candidates,
                                "stops": len(stops),
                            },
                        )

                if matching_services:
                    consequence.services.add(*matching_services)
                else:
                    logger.warning(
                        "no matching service for %s %s",
                        operator_ref,
                        line_name,
                        extra={
                            "situation_number": situation_number,
                            "summary": situation.summary,
                            "candidates": candidates,
                            "stops": len(stops),
                        },
                    )

        for operator in consequence_element.findall(
            "Affects/Operators/AffectedOperator"
        ):
            operator_ref = operator.findtext("OperatorRef")
            try:
                consequence.operators.add(*get_operators(operator_ref))
            except Operator.DoesNotExist:
                logger.exception("operator %s does not exist", operator_ref)

    return situation.id


def get_situation_elements(open_file):
    for _, element in ET.iterparse(open_file):
        if element.tag[:29] == "{http://www.siri.org.uk/siri}":
            element.tag = element.tag[29:]

        if element.tag.endswith("PtSituationElement"):
            yield element


def bods_disruptions():
    url = "https://data.bus-data.dft.gov.uk/disruptions/download/bulk_archive"

    source = DataSource.objects.get_or_create(name="Bus Open Data")[0]

    situations = []

    response = requests.get(url, timeout=61)
    response.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(response.content))

    namelist = archive.namelist()
    assert len(namelist) == 1
    open_file = archive.open(namelist[0])

    elements = list(get_situation_elements(open_file))

    situation_numbers = (element.findtext("SituationNumber") for element in elements)

    current_situations = {
        s.situation_number: s
        for s in source.situation_set.filter(situation_number__in=situation_numbers)
    }

    for element in elements:
        situation_number = element.findtext("SituationNumber")
        try:
            with transaction.atomic():
                situations.append(handle_item(element, source, current_situations))
        except Exception:
            logger.exception("situation %s", situation_number)
            # keep showing the previous version
            if situation := current_situations.get(situation_number):
                situations.append(situation.id)

    delete_old_situations(source, situations)
