from django.conf import settings
from django.test import TestCase
from vcr import use_cassette

from busstops.models import Operator, Service

from .translinkni import Situation, translink_disruptions


class TranslinkNorthernIrelandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        operator = Operator.objects.create(noc="UTS")
        service = Service.objects.create(
            line_name="338A",
            service_code="338a",
            current=True,
        )
        service.operator.add(operator)

    def test_siri_sx_request(self):
        vcr_dir = settings.BASE_DIR / "fixtures" / "vcr"

        with use_cassette(
            str(vcr_dir / "disruptions_translinkni.yaml"),
            decode_compressed_response=True,
        ) as cassette:
            with self.assertNumQueries(36):
                translink_disruptions("")

            cassette.rewind()

            with self.assertNumQueries(43):
                translink_disruptions("")

        situation = Situation.objects.get(situation_number="ems-7682")

        self.assertEqual(situation.reason, "")
        self.assertEqual(str(situation), "Newry Area - Service Alteration")
        self.assertEqual(
            situation.text,
            """Due to road words commencing Monday 20th October 2025, for 4 weeks, the following Ulsterbus Town services will be affected:

Town services\xa0338a and 338d will be affected.\xa0

High Street Newry will be closed for 4 weeks.

High Street and Stream Street will not be served.

Diversion will operate via Sandy Street, Talbot Street and Cowan Street.

Subject to change.""",
        )

        consequence = situation.consequence_set.first()
        self.assertEqual("/services/338a", consequence.get_absolute_url())
