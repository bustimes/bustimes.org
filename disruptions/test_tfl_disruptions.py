from django.conf import settings
from django.test import TestCase, override_settings
from vcr import use_cassette

from busstops.models import DataSource, Region, Service, StopPoint

from .tfl_disruptions import tfl_disruptions


class TfLDisruptionsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(id="L", name="L")
        source = DataSource.objects.create(name="L")
        Service.objects.create(
            line_name="211",
            service_code="211",
            current=True,
            region=region,
            source=source,
        )
        DataSource.objects.create(name="TfL")
        StopPoint.objects.create(atco_code="490001097D", active=True)

    def test_siri_sx_request(self):
        vcr_dir = settings.BASE_DIR / "fixtures" / "vcr"

        with (
            override_settings(TFL={}),
            use_cassette(
                str(vcr_dir / "tfl_disruptions.yaml"), decode_compressed_response=True
            ) as cassette,
        ):
            with self.assertNumQueries(111):
                tfl_disruptions()

            cassette.rewind()

            with self.assertNumQueries(105):
                tfl_disruptions()

        response = self.client.get("/situations")

        situation = response.context["situations"][0]

        consequence = situation.consequence_set.first()
        self.assertEqual("", str(consequence))
        self.assertEqual("", consequence.get_absolute_url())

        situation = response.context["situations"][1]

        self.assertEqual(
            situation.situation_number, "70c2d01c46664fb70c7b1ad11ff7fc8ace2a"
        )
        self.assertEqual(situation.reason, "")
        self.assertEqual(str(situation), "VICTORIA BUS STATION")
        self.assertEqual(
            situation.text,
            "ROUTES 11 211 C1 N11 westbound are now departing from "
            "Victoria Station stop (R) on Buckingham Palace Road.",
        )

        consequence = situation.consequence_set.first()
        self.assertEqual("", str(consequence))
        self.assertEqual("/services/211", consequence.get_absolute_url())
