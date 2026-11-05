import json
import logging
from hashlib import sha256

import requests
from django.conf import settings
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q

from busstops.models import DataSource, Service, StopPoint

from .models import Consequence, Situation, ValidityPeriod, delete_old_situations

logger = logging.getLogger(__name__)


def get_hash(text):
    return sha256(text.encode(), usedforsecurity=False).hexdigest()[:36]


def handle_disruption(item: dict, source: DataSource):
    stops = (
        StopPoint.objects.filter(
            Q(atco_code=item["atcoCode"]) | Q(stop_area=item["atcoCode"])
        )
        .only("atco_code")
        .order_by()
    )

    if not stops:
        return

    window = DateTimeTZRange(item["fromDate"], item["toDate"], "[]")

    situation_number = get_hash(f"{item['description']} {window}")

    situation = Situation.objects.filter(
        source=source, situation_number=situation_number
    ).first()

    if not situation:
        situation = Situation(
            situation_number=situation_number,
            created_at=item["fromDate"],
            source=source,
        )
    situation.text = item["description"].replace("\\n", "\n").strip()
    if ": " in situation.text:
        situation.summary, situation.text = situation.text.split(": ", 1)
    else:
        situation.reason = item["type"]
    situation.text = situation.text.replace(". ", ".\n\n")
    situation.publication_window = window
    situation.reason = item["type"]
    situation.data = json.dumps(item)
    situation.save()

    try:
        consequence = situation.consequence_set.get()
    except Consequence.DoesNotExist:
        consequence = Consequence(situation=situation)
        consequence.save()

    consequence.stops.add(*stops)

    try:
        period = situation.validityperiod_set.get()
    except ValidityPeriod.DoesNotExist:
        period = ValidityPeriod(situation=situation, period=window)
        period.save()

    return situation


def handle_status(item: dict, source: DataSource):
    if item["lineStatuses"][0]["statusSeverityDescription"] == "Good Service":
        assert len(item["lineStatuses"]) == 1
        return

    services = Service.objects.only("id").filter(
        line_name__iexact=item["name"], region="L", current=True, mode="bus"
    )
    if not services:
        return
    elif len(services) > 1:
        print(f"multiple {item['name']}s: {services}")

    for status in item["lineStatuses"]:
        assert status["reason"] == status["disruption"]["description"]

        validity_periods = status["validityPeriods"]

        window = DateTimeTZRange(
            min(period["fromDate"] for period in validity_periods),
            max(period["toDate"] for period in validity_periods),
            "[]",
        )

        situation_number = get_hash(f"{status['reason']} {window}")

        situation = Situation.objects.filter(
            source=source, situation_number=situation_number
        ).first()
        if not situation:
            situation = Situation(
                situation_number=situation_number,
                text=status["reason"],
                created_at=status["disruption"]["created"],
                # modified_at=status["modified"]["modified"],
                publication_window=window,
                source=source,
            )
            created = True
        else:
            created = False

        if ": " in situation.text and situation.text.index(": ") < 255:
            situation.summary, situation.text = situation.text.split(": ", 1)

        situation.text = situation.text.replace(". ", ".\n\n")

        situation.data = json.dumps(status)
        situation.save()

        if created:
            for period in validity_periods:
                window = DateTimeTZRange(period["fromDate"], period["toDate"], "[]")
                vp = ValidityPeriod(
                    situation=situation,
                    period=window,
                )
                vp.save()

        consequence = situation.consequence_set.first()
        if not consequence:
            consequence = Consequence(situation=situation)
            consequence.save()

        consequence.services.add(*services)

        yield situation


def tfl_disruptions():
    session = requests.Session()
    session.headers.update({"User-Agent": "bustimes.org"})

    source = DataSource.objects.get_or_create(name="TfL")[0]

    situations = set()

    response = session.get(
        "https://api.tfl.gov.uk/StopPoint/Mode/bus/Disruption",
        params=settings.TFL,
        timeout=30,
    )
    response.raise_for_status()

    for item in response.json():
        if situation := handle_disruption(item, source):
            situations.add(situation.id)

    response = session.get(
        "https://api.tfl.gov.uk/Line/Mode/bus,river-bus,tram,cable-car/Status",
        params=settings.TFL,
        timeout=30,
    )
    response.raise_for_status()

    for item in response.json():
        for situation in handle_status(item, source):
            situations.add(situation.id)

    delete_old_situations(source, situations)
