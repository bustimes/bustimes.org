import hashlib
import logging
from datetime import UTC, datetime

import requests
from django.db import transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.utils import timezone
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Operator, Service, StopPoint
from bustimes.formatting import format_timedelta
from bustimes.gtfs_feeds import FEEDS
from bustimes.models import Trip

from .models import (
    AffectedJourney,
    Consequence,
    Link,
    Situation,
    ValidityPeriod,
    delete_old_situations,
)

logger = logging.getLogger(__name__)

Alert = gtfs_realtime_pb2.Alert


def get_text(translated_string) -> str:
    translations = translated_string.translation
    for translation in translations:
        if translation.language == "en":
            return translation.text
    return translations[0].text if translations else ""


def get_datetime(timestamp: int):
    return datetime.fromtimestamp(timestamp, UTC) if timestamp else None


def get_reason(alert) -> str:
    if alert.cause in (Alert.UNKNOWN_CAUSE, Alert.OTHER_CAUSE):
        return ""
    return Alert.Cause.Name(alert.cause).lower().replace("_", " ")


def merge_alerts(alert, other) -> None:
    alert.informed_entity.extend(other.informed_entity)
    periods = [*alert.active_period, *other.active_period]
    starts = [period.start for period in periods]
    ends = [period.end for period in periods]
    del alert.active_period[:]
    if periods:
        alert.active_period.add(
            start=0 if 0 in starts else min(starts), end=0 if 0 in ends else max(ends)
        )


def get_alerts(message, feed):
    alerts = {}
    for entity in message.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        if not feed.alert_ids_change:
            alerts[entity.id] = alert
            continue
        # identify alerts by their text, and combine ones with the same text
        # (Ember has one alert for each group of trips affected in the same way)
        key = Alert(
            header_text=alert.header_text,
            description_text=alert.description_text,
            url=alert.url,
            cause=alert.cause,
            effect=alert.effect,
        )
        key = hashlib.sha1(key.SerializeToString(deterministic=True)).hexdigest()
        key = key[:36]
        if key in alerts:
            merge_alerts(alerts[key], alert)
        else:
            alerts[key] = alert
    return alerts


def handle_alert(situation_number, alert, feed, source, operator, current_situations):
    summary = get_text(alert.header_text)
    if not summary or summary in feed.skip_alert_headers:
        return

    data = json_format.MessageToJson(alert, sort_keys=True, indent=None)

    situation = current_situations.get(situation_number)
    if situation:
        if situation.data == data:
            return situation.id  # hasn't changed
        situation.validityperiod_set.all().delete()
        situation.consequence_set.all().delete()
        situation.link_set.all().delete()
        situation.affectedjourney_set.all().delete()
    else:
        situation = Situation(source=source, situation_number=situation_number)

    prefix = feed.stop_id_prefix
    route_ids = set()
    stop_ids = set()
    trip_ids = set()
    journeys = set()  # (trip_id, start_date) of journeys affected as a whole
    agency = False
    for informed_entity in alert.informed_entity:
        trip = informed_entity.trip
        if informed_entity.route_id:
            route_ids.add(informed_entity.route_id)
        if informed_entity.stop_id:
            stop_ids.update(
                (informed_entity.stop_id, f"{prefix}{informed_entity.stop_id}")
            )
        if trip.trip_id:
            trip_ids.add(trip.trip_id)
            if not informed_entity.stop_id:
                journeys.add((trip.trip_id, trip.start_date))
        if not (informed_entity.route_id or informed_entity.stop_id or trip.trip_id):
            agency = True

    trips = Trip.objects.filter(
        route__source=source, ticket_machine_code__in=trip_ids
    ).select_related("route")
    services = Service.objects.filter(
        route__source=source, route__code__in=route_ids
    ).distinct()
    services = {*services, *(trip.route.service for trip in trips)}

    text = get_text(alert.description_text)
    if text == summary:
        text = ""
    # alert about one journey (e.g. Ember "running late") - say which journey
    if len(journeys) == 1 and not (route_ids or stop_ids) and len(trips) == 1:
        trip = trips[0]
        text = "\n\n".join(filter(None, (summary, text)))
        summary = format_timedelta(trip.start)
        if trip.headsign:
            summary = f"{summary} to {trip.headsign}"
    # e.g. Ember stop moved for journeys in one direction
    elif trips and not route_ids:
        if headsigns := sorted({trip.headsign for trip in trips if trip.headsign}):
            towards = f"Journeys towards {' and '.join(headsigns)}"
            text = "\n\n".join(filter(None, (text, towards)))

    situation.data = data
    situation.current = True
    situation.modified_at = timezone.now()
    situation.summary = summary[:255]
    situation.text = text
    situation.reason = get_reason(alert)

    periods = [
        (
            get_datetime(period.start),
            # Ember sometimes sends an end before the start
            get_datetime(period.end and max(period.start, period.end)),
        )
        for period in alert.active_period
    ]
    starts = [start for start, _ in periods]
    ends = [end for _, end in periods]
    situation.publication_window = DateTimeTZRange(
        min(starts) if periods and all(starts) else situation.created_at,
        max(ends) if periods and all(ends) else None,
        "[]",
    )
    situation.save()

    ValidityPeriod.objects.bulk_create(
        ValidityPeriod(situation=situation, period=DateTimeTZRange(start, end, "[]"))
        for start, end in periods
    )

    if url := get_text(alert.url):
        Link.objects.create(situation=situation, url=url)

    stops = StopPoint.objects.filter(atco_code__in=stop_ids)

    if services or stops or agency:
        consequence = Consequence.objects.create(situation=situation)
        consequence.services.set(services)
        consequence.stops.set(stops)
        if agency and operator:
            consequence.operators.add(operator)

    trips = {trip.ticket_machine_code: trip for trip in trips}
    condition = Alert.Effect.Name(alert.effect).lower().replace("_", " ")
    AffectedJourney.objects.bulk_create(
        AffectedJourney(
            situation=situation,
            trip=trips[trip_id],
            date=datetime.strptime(start_date, "%Y%m%d").date() if start_date else None,
            condition=condition,
        )
        for trip_id, start_date in journeys
        if trip_id in trips
    )

    return situation.id


def import_alerts(feed_name: str):
    feed = FEEDS[feed_name]

    response = requests.get(
        feed.alerts_url, headers={"User-Agent": "bustimes.org"}, timeout=10
    )
    response.raise_for_status()
    message = gtfs_realtime_pb2.FeedMessage()
    message.ParseFromString(response.content)

    source = DataSource.objects.get_or_create(name=feed.source_name)[0]
    operator = Operator.objects.filter(noc=feed.noc).first()

    current_situations = {
        situation.situation_number: situation
        for situation in source.situation_set.all()
    }

    situations = []
    with transaction.atomic():
        for situation_number, alert in get_alerts(message, feed).items():
            if situation_id := handle_alert(
                situation_number, alert, feed, source, operator, current_situations
            ):
                situations.append(situation_id)

        delete_old_situations(source, situations, empty_feed_ok=True)


def gtfsr_alerts():
    for feed_name, feed in FEEDS.items():
        if feed.alerts_url:
            try:
                import_alerts(feed_name)
            except requests.RequestException as e:
                logger.warning(f"{feed_name} alerts: {e}")
