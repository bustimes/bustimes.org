import json
import logging

import requests
from bs4 import BeautifulSoup
from django.db import transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q

from busstops.models import DataSource, Service

from .models import Consequence, Situation, ValidityPeriod, delete_old_situations

logger = logging.getLogger(__name__)


def get_period(element: dict):
    start = element.get("from")
    end = element.get("to")
    return DateTimeTZRange(start, end, "[]")


def handle_item(item: dict, source: DataSource, current_situations: dict):
    situation_number = item["id"]

    situation = current_situations.get(situation_number)

    data = json.dumps(item)

    if situation:
        if situation.data == data:
            return situation.id  # hasn't changed
        created = False
    else:
        situation = Situation(source=source, situation_number=situation_number)
        created = True

    situation.current = True

    situation.created_at = item["timestamps"]["creation"]
    situation.modified_at = item["timestamps"]["lastModification"]
    situation.publication_window = get_period(item["timestamps"]["availability"])

    assert len(item["infoLinks"]) == 1

    situation.summary = item["infoLinks"][0]["urlText"]

    if "content" not in item["infoLinks"][0]:
        return
    situation.text = item["infoLinks"][0]["content"]
    soup = BeautifulSoup(situation.text, "lxml")
    situation.text = soup.get_text().strip().replace("\n\xa0\n", "\n\n")
    situation.data = data
    situation.save()

    for i, period_element in enumerate(item["timestamps"]["validity"]):
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

    consequence = Consequence(situation=situation)
    if not created and i == 0:
        try:
            consequence = situation.consequence_set.get()
        except Consequence.MultipleObjectsReturned:
            situation.consequence_set.all().delete()
        except Consequence.DoesNotExist:
            pass

    if consequence.id:
        consequence.services.clear()

    services = Service.objects.filter(current=True)

    if "lines" in item["affected"]:
        for line in item["affected"]["lines"]:
            line_name = line["number"]
            line_filter = Q(route__line_name__iexact=line_name) | Q(
                line_name__iexact=line_name
            )

            matching_services = services.filter(line_filter)

            if operator_ref := line["operator"].get("id"):
                matching_services = matching_services.filter(operator=operator_ref)

            matching_services = matching_services.distinct()

            if matching_services:
                if not consequence.id:
                    consequence.save()

                consequence.services.add(*matching_services)

    return situation.id


def translink_disruptions(api_key):
    url = "https://opendata.translinkniplanner.co.uk/Ext_API/XML_ADDINFO_REQUEST?outputFormat=rapidJSON&filterPublicationStatus=current"

    source = DataSource.objects.get_or_create(name="Translink")[0]

    situations = []

    response = requests.get(url, headers={"x-api-token": api_key}, timeout=61)
    response.raise_for_status()
    if not response.content:
        return
    elements = response.json()["infos"]["current"]

    situation_numbers = [element["id"] for element in elements]

    current_situations = {
        s.situation_number: s
        for s in source.situation_set.filter(situation_number__in=situation_numbers)
    }

    for element in elements:
        try:
            with transaction.atomic():
                situation_id = handle_item(element, source, current_situations)
        except Exception:
            logger.exception("situation %s", element["id"])
            # keep showing the previous version
            situation = current_situations.get(element["id"])
            situation_id = situation and situation.id
        if situation_id:
            situations.append(situation_id)
    delete_old_situations(source, situations)
