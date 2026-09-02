from django.conf import settings
from django.test import TestCase
from vcr import use_cassette

from busstops.models import (
    DataSource,
    Operator,
    OperatorCode,
    Region,
    Service,
    StopPoint,
    StopUsage,
)

from .models import Situation
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

    def test_siri_sx_request(self):
        with use_cassette(str(VCR_DIR / "siri_sx.yaml")) as cassette:
            with self.assertNumQueries(126):
                bods_disruptions()

            cassette.rewind()

            with self.assertNumQueries(3):
                bods_disruptions()

            cassette.rewind()
            Situation.objects.all().update(data="")

            with self.assertNumQueries(140):
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
