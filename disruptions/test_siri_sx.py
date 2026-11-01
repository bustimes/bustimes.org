import xml.etree.ElementTree as ET
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.db import DataError
from django.test import TestCase
from vcr import use_cassette

from busstops.models import (
    AdminArea,
    DataSource,
    Operator,
    OperatorCode,
    Region,
    Service,
    StopPoint,
    StopUsage,
)

from .models import Link, Situation
from .siri_sx import handle_item
from .tasks import bods_disruptions

VCR_DIR = settings.BASE_DIR / "fixtures" / "vcr"


class SiriSXTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(id="NW", name="North West")
        operator = Operator.objects.create(
            region=region, noc="HATT", name="Hattons of Huyton"
        )
        source = DataSource.objects.create(name="National Operator Codes")
        OperatorCode.objects.create(operator=operator, source=source, code="HATT")
        service = Service.objects.create(
            line_name="156",
            service_code="156",
            current=True,
            timetable_wrong=True,
        )
        service.operator.add(operator)
        StopPoint.objects.bulk_create(
            [
                StopPoint(atco_code=atco_code, active=True)
                for atco_code in (
                    "1800EB00151",
                    "1800EB06881",
                    "1800EB13721",
                    "1800NF28251",
                    "1800NF28261",
                    "1800NF28271",
                    "1800NF28281",
                    "1800NF28291",
                    "1800NF28301",
                    "1800NF28541",
                    "1800NF28551",
                    "1800NF28781",
                    "1800NF28791",
                    "1800NF28801",
                    "1800NF28811",
                    "1800NF28821",
                    "1800NF28831",
                    "1800NF28841",
                    "1800NF28851",
                    "1800NF28861",
                    "1800NF28931",
                    "1800NF28941",
                    "1800NF28951",
                    "1800NF28961",
                    "1800NF28971",
                    "1800NF28981",
                    "1800SB02041",
                    "1800SB05841",
                    "1800SB12051",
                    "1800SB12491",
                    "1800SB14291",
                    "1800SB14301",
                    "1800SB15431",
                    "1800SB33551",
                    "1800SB33701",
                    "1800SB33721",
                    "1800SB33731",
                    "1800SB33741",
                    "1800SB33751",
                    "1800SB33781",
                    "1800SB33791",
                    "1800SB33801",
                    "1800SB33811",
                    "1800SB33821",
                    "1800SB33841",
                    "1800SB33851",
                    "2800S11031B",
                    "2800S11050A",
                    "2800S11053A",  # see StopUsage below
                    "2800S11085A",
                    "2800S46043C",
                    "2800S46075A",
                    "2800S46075B",
                    "2800S61011A",
                    "2800S61012B",
                    "2800S61012C",
                    "2800S61013A",
                    "2800S61013B",
                )
            ]
        )
        StopUsage.objects.create(service=service, stop_id="2800S11053A", order=69)
        AdminArea.objects.create(id=73, atco_code="080", name="Cornwall", region=region)

    def test_siri_sx_request(self):
        with use_cassette(VCR_DIR / "siri_sx.yaml") as cassette:
            with self.assertNumQueries(144):
                bods_disruptions()

            cassette.rewind()

            with self.assertNumQueries(21):
                bods_disruptions()

            cassette.rewind()
            Situation.objects.all().update(data="")

            with self.assertNumQueries(158):
                bods_disruptions()

        situation = Situation.objects.first()

        self.assertEqual(situation.situation_number, "RGlzcnVwdGlvbk5vZGU6MTA3NjM=")
        self.assertEqual(situation.reason, "roadworks")
        self.assertEqual(
            situation.summary,
            "East Didsbury bus service changes"
            " Monday 11th May until Thursday 14th May. ",
        )
        self.assertEqual(
            situation.text,
            "Due to resurfacing works there will "
            "be bus service diversions and bus stop closures from"
            " Monday 11th May until Thursday 14th may. ",
        )
        self.assertEqual(situation.reason, "roadworks")

        response = self.client.get(situation.get_absolute_url())
        self.assertContains(
            response,
            '<a href="https://tfgm.com/travel-updates/bus-update" rel="nofollow">tfgm.com/travel-updates/bus-update</a>',
        )

        consequence = situation.consequence_set.get()
        self.maxDiff = None
        self.assertEqual(
            consequence.text,
            "Towards East Didsbury terminus customers should alight opposite East "
            "Didsbury Rail Station as this will be the last stop. "
            "From here its a short walk to the terminus. \n\n"
            "Towards Manchester the 142 service will begin outside Didsbury Cricket club . ",
        )

        with self.assertNumQueries(13):
            response = self.client.get("/services/156")

        # long text so collapsed by default
        self.assertContains(response, '<details class="situation">')
        self.assertContains(
            response,
            "<p>East Lancashire Road will be subjected to restrictions,"
            " at Liverpool Road, from Monday 17 February 2020 for approximately 7 months.</p>",
        )
        self.assertContains(
            response,
            "<p>Route 156 will travel as normal from St Helens to Haydock Lane, then "
            "u-turn at Moore Park Way roundabout, Haydock Lane, Millfield Lane, Tithebarn Road,"
            " then as normal route to Garswood (omitting East Lancashire Road and Liverpool Road).</p>",
        )

        self.assertContains(
            response,
            '<a href="https://www.merseytravel.gov.uk/travel-updates/east-lancashire-road-(haydock)/" rel="nofollow">'
            "merseytravel.gov.uk/travel-updates/east-lancashire-road-(haydock)</a>",
        )

        with self.assertNumQueries(7):
            response = self.client.get("/stops/2800S11031B")
        self.assertContains(
            response,
            "subjected to restrictions, at Liverpool Road, from Monday 17 February 2020",
        )

    def test_consequence_details(self):
        xml = f"""<PtSituationElement>
            <CreationTime>2026-10-22T13:25:31Z</CreationTime>
            <ParticipantRef>Cornwall</ParticipantRef>
            <SituationNumber>b42df1bb-f3b7-4159-bfdb-7ab997badb24</SituationNumber>
            <Source><TimeOfCommunication>2026-10-23T10:16:45.192Z</TimeOfCommunication></Source>
            <Progress>open</Progress>
            <PublicationWindow><StartTime>2026-10-22T14:00:00.000Z</StartTime></PublicationWindow>
            <EquipmentReason>defectivePublicAnnouncementSystem</EquipmentReason>
            <Summary>Storm Austen - Friday 23rd October</Summary>
            <Description>Storm Austen - Friday 23rd October</Description>
            <InfoLinks><InfoLink><Uri>https://example.com/{"a" * 230}</Uri></InfoLink></InfoLinks>
            <Consequences>
                <Consequence>
                    <Severity>verySevere</Severity>
                    <Affects>
                        <Operators><AllOperators /></Operators>
                        <Networks><AffectedNetwork><VehicleMode>bus</VehicleMode><AllLines /></AffectedNetwork></Networks>
                        <Places><AffectedPlace>
                            <PlaceRef>073</PlaceRef>
                            <PlaceName>Cornwall</PlaceName>
                            <PlaceCategory>AdministrativeArea</PlaceCategory>
                        </AffectedPlace></Places>
                    </Affects>
                    <Advice><Details>Stay at home</Details></Advice>
                </Consequence>
                <Consequence>
                    <Severity>slight</Severity>
                    <Affects><Networks><AffectedNetwork>
                        <VehicleMode>bus</VehicleMode>
                        <AffectedLine>
                            <AffectedOperator><OperatorRef>HATT</OperatorRef></AffectedOperator>
                            <LineRef>156</LineRef>
                            <Direction><DirectionRef>inboundTowardsTown</DirectionRef></Direction>
                        </AffectedLine>
                    </AffectedNetwork></Networks></Affects>
                    <Advice><Details>Expect delays</Details></Advice>
                    <Delays><Delay>PT60M</Delay></Delays>
                </Consequence>
            </Consequences>
        </PtSituationElement>"""
        source = DataSource.objects.create(name="Bus Open Data")

        situation_id = handle_item(ET.fromstring(xml), source, {})

        situation = Situation.objects.get(id=situation_id)
        self.assertEqual(situation.reason, "defectivePublicAnnouncementSystem")
        self.assertEqual(situation.text, "")
        self.assertEqual(len(situation.link_set.get().url), 250)

        network, line = situation.consequence_set.order_by("id")
        self.assertEqual(network.severity, "verySevere")
        self.assertEqual(network.vehicle_mode, "bus")
        self.assertEqual(network.direction, "")
        self.assertIsNone(network.delay)
        self.assertEqual(network.admin_areas.get().name, "Cornwall")

        self.assertEqual(line.severity, "slight")
        self.assertEqual(line.direction, "inboundTowardsTown")
        self.assertEqual(line.delay, timedelta(hours=1))
        self.assertEqual(line.services.get().line_name, "156")
        self.assertFalse(line.admin_areas.exists())

    def test_admin_area_situations(self):
        def area_situation(situation_number, summary, vehicle_mode):
            return f"""<PtSituationElement>
                <CreationTime>2026-10-22T13:25:31Z</CreationTime>
                <ParticipantRef>Cornwall</ParticipantRef>
                <SituationNumber>{situation_number}</SituationNumber>
                <Source><TimeOfCommunication /></Source>
                <Progress>open</Progress>
                <PublicationWindow><StartTime>2026-10-22T14:00:00.000Z</StartTime></PublicationWindow>
                <Summary>{summary}</Summary>
                <Description>{summary}</Description>
                <Consequences><Consequence>
                    <Affects>
                        <Operators><AllOperators /></Operators>
                        <Networks><AffectedNetwork>
                            <VehicleMode>{vehicle_mode}</VehicleMode><AllLines />
                        </AffectedNetwork></Networks>
                        <Places><AffectedPlace>
                            <PlaceRef>073</PlaceRef>
                            <PlaceCategory>AdministrativeArea</PlaceCategory>
                        </AffectedPlace></Places>
                    </Affects>
                    <Advice><Details>{summary}</Details></Advice>
                </Consequence></Consequences>
            </PtSituationElement>"""

        source = DataSource.objects.create(name="Bus Open Data")
        for element in (
            area_situation("storm", "Storm Austen", "bus"),
            area_situation("engineering", "Engineering works", "rail"),
        ):
            handle_item(ET.fromstring(element), source, {})

        stop = StopPoint.objects.create(
            atco_code="0800COB00001", admin_area_id=73, active=True
        )
        service = Service.objects.create(line_name="1", service_code="1", current=True)
        StopUsage.objects.create(service=service, stop=stop, order=1)

        for url in (stop.get_absolute_url(), service.get_absolute_url()):
            response = self.client.get(url)
            self.assertContains(response, "Storm Austen")
            self.assertNotContains(response, "Engineering works")

        response = self.client.get("/stops/2800S11031B")  # not in Cornwall
        self.assertNotContains(response, "Storm Austen")

    def test_old_situations(self):
        source = DataSource.objects.create(name="Bus Open Data")
        gone = Situation.objects.create(source=source, situation_number="gone")

        with use_cassette(VCR_DIR / "siri_sx.yaml") as cassette:
            bods_disruptions()
            self.assertFalse(Situation.objects.filter(id=gone.id).exists())

            hidden = Situation.objects.first()
            hidden.current = False
            hidden.save(update_fields=["current"])

            cassette.rewind()
            bods_disruptions()

        hidden.refresh_from_db()
        self.assertFalse(hidden.current)

    def test_bad_situation(self):
        situation_number = "RGlzcnVwdGlvbk5vZGU6MTA3NjM="
        save = Link.save

        def bad_save(link, *args, **kwargs):
            if link.situation.situation_number == situation_number:
                raise DataError
            save(link, *args, **kwargs)

        with use_cassette(VCR_DIR / "siri_sx.yaml") as cassette:
            bods_disruptions()

            cassette.rewind()
            Situation.objects.all().update(data="")

            with (
                patch.object(Link, "save", autospec=True, side_effect=bad_save),
                self.assertLogs("disruptions.siri_sx", "ERROR"),
            ):
                bods_disruptions()

        # rolled back, so will be retried next time
        bad = Situation.objects.get(situation_number=situation_number)
        self.assertEqual(bad.data, "")
        self.assertTrue(bad.current)

        self.assertFalse(Situation.objects.filter(data="").exclude(id=bad.id).exists())
