import difflib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests
from django.core.management.base import BaseCommand
from django.db.transaction import atomic
from django.utils.timezone import localtime

from busstops.models import DataSource
from bustimes.models import Calendar, Trip

from ...models import AffectedJourney, Call, Situation
from ...siri_sx import get_period


def get_trip(avj):
    line_name = avj.findtext("PublishedLineName")
    operator_ref = avj.findtext("Operator/OperatorRef")

    print(operator_ref, line_name)

    journey_ref = avj.findtext("DatedVehicleJourneyRef") or avj.findtext(
        "VehicleJourneyRef"
    )
    departure_time = avj.findtext("OriginAimedDepartureTime")

    departure_time = datetime.fromisoformat(departure_time)
    departure_time = localtime(departure_time)

    trips = Trip.objects.filter(
        route__service__current=True,
        route__line_name=line_name,
        operator=operator_ref,
        ticket_machine_code=journey_ref,
        start=timedelta(hours=departure_time.hour, minutes=departure_time.minute),
        calendar__in=Calendar.objects.active_on(departure_time),
    )
    print("  ", journey_ref, trips)
    return trips


def handle_situation(element, source, current_situations):
    situation_number = element.findtext("SituationNumber")

    situation = current_situations.get(situation_number)

    xml = ET.tostring(element, encoding="unicode")

    progress = element.findtext("Progress")
    assert progress in ("open", "closed"), progress

    if situation:
        if progress == "closed":
            situation.current = False
            situation.data = xml
            situation.save()
            return situation
        if situation.data == xml:
            return situation

        # diff the XML to see why it changed
        for i in difflib.unified_diff(
            situation.data.splitlines(),
            xml.splitlines(),
            fromfile=situation.data,
            tofile=xml,
        ):
            print(i)
    elif progress == "closed":
        return

    if not situation:
        situation = Situation(
            source=source,
            situation_number=situation_number,
            current=(progress == "open"),
        )

    situation.data = xml
    situation.participant_ref = element.findtext("ParticipantRef")
    situation.reason = element.findtext("MiscellaneousReason")
    situation.created_at = datetime.fromisoformat(element.findtext("CreationTime"))
    # if created_at is naive, assume it's in UTC
    if not situation.created_at.tzinfo:
        situation.created_at = situation.created_at.replace(tzinfo=UTC)

    vps = element.findall("ValidityPeriod")
    assert len(vps) == 1
    situation.publication_window = get_period(vps[0])

    for avj in element.findall("Affects/VehicleJourneys/AffectedVehicleJourney"):
        trips = get_trip(avj)

        if len(trips) == 1:
            with atomic():
                situation.save()

                trip = trips[0]
                time = datetime.fromisoformat(avj.findtext("OriginAimedDepartureTime"))
                date = time.date()
                if trip.start.days:
                    date -= timedelta(days=1)

                journey, _created = AffectedJourney.objects.update_or_create(
                    {
                        "condition": element.findtext(
                            "Consequences/Consequence/Condition"
                        ),
                        "trip": trip,
                        "date": date,
                        "origin_departure_time": time,
                    },
                    situation=situation,
                )
                stop_times = journey.trip.stoptime_set.all()
                calls = avj.find("Calls")
                if len(calls) == len(stop_times):
                    calls = [
                        Call(
                            journey=journey,
                            stop_time=stop_time,
                            arrival_time=call.findtext("AimedArrivalTime"),
                            departure_time=call.findtext("AimedDepartureTime"),
                            condition=call.findtext("CallCondition"),
                            order=call.findtext("Order"),
                        )
                        for stop_time, call in zip(stop_times, calls)
                        if stop_time.stop_id == call.findtext("StopPointRef")
                    ]
                    Call.objects.bulk_create(calls)
                else:
                    print(f"  {len(calls)=} {len(stop_times)=}")

    if situation.id:
        return situation


class Command(BaseCommand):
    @staticmethod
    def add_arguments(parser):
        parser.add_argument("url", type=str)
        parser.add_argument("api_key", type=str)

    def handle(self, url, api_key, *args, **options):
        source = DataSource.objects.get_or_create(name="BODS cancellations")[0]

        response = requests.get(
            url, params={"api_key": api_key}, stream=True, timeout=61
        )
        response.raw.decode_content = True
        response.raise_for_status()

        current_situations = {
            s.situation_number: s for s in source.situation_set.filter(current=True)
        }
        situations = []

        for _, element in ET.iterparse(response.raw):
            if element.tag[:29] == "{http://www.siri.org.uk/siri}":
                element.tag = element.tag[29:]

            if element.tag.endswith("PtSituationElement"):
                if situation := handle_situation(element, source, current_situations):
                    situations.append(situation.id)

                element.clear()

        # archive old situations
        print(
            source.situation_set.filter(current=True)
            .exclude(id__in=situations)
            .update(current=False)
        )
