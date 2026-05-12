from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import User
from busstops.models import Operator, Region, Service

from .models import Licence, Registration


class VosaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Region.objects.create(id="SW", name="South West")
        cls.operator = Operator.objects.create(
            region_id="SW", noc="AINS", name="Ainsley's Chariots"
        )
        service = Service.objects.create(current=True, line_name="33B")
        service.operator.add(cls.operator)

        cls.user = User.objects.create(
            username="Roger", is_staff=True, is_superuser=True
        )

        Licence.objects.create(
            traffic_area="",
            licence_number="PF0000705",
            address="10 King Road, Ipswich",
            discs=0,
            authorised_discs=0,
        )
        Licence.objects.create(
            traffic_area="F",
            licence_number="PF0000102",
            discs=0,
            authorised_discs=0,
        )

    @override_settings(DATA_DIR=Path(__file__).resolve().parent / "fixtures")
    def test(self):
        with mock.patch(
            "vosa.management.commands.import_vosa.download_utils.download_if_modified",
            return_value=(True, None),
        ):
            with self.assertNumQueries(18):
                call_command("import_vosa", "F")

            with self.assertNumQueries(8):
                call_command("import_vosa", "F")

        # multiple trading names
        licence = Licence.objects.get(licence_number="PF0000705")
        self.assertEqual(
            licence.trading_name,
            """R O SIMONDS
Simonds Coach& Travel
Simonds Countrylink""",
        )
        # updated other details
        self.assertEqual(
            licence.address,
            "Roswald House, Simonds, Oak Drive, DISS, IP22 4GX, GB",
        )
        self.assertEqual(licence.discs, 44)
        self.assertEqual(licence.authorised_discs, 50)
        self.assertEqual(licence.traffic_area, "F")

        # linked operator
        self.operator.licences.add(licence)

        response = self.client.get("/licences/PF0000705")
        self.assertContains(response, "Ainsley&#x27;s Chariots")
        self.assertContains(response, "<th>Trading name</th>")

        response = self.client.get("/registrations/PF0000705/8")
        self.assertContains(response, "Ainsley&#x27;s Chariots")

        # licence
        response = self.client.get("/licences/PF1018256")
        self.assertEqual(2, len(response.context_data["registrations"]))
        self.assertEqual(1, len(response.context_data["cancelled"]))
        self.assertContains(response, "SANDERS COACHES LIMITED")
        self.assertContains(
            response, "LETHERINGSETT, GLANDFORD, WIVETON, CLEY, BLAKENEY"
        )

        # rss feed
        with self.assertNumQueries(2):
            response = self.client.get("/licences/PF1018256/rss")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SANDERS COACHES LIMITED")

        # licence 404
        with self.assertNumQueries(1):
            response = self.client.get("/licences/PH102095")
        self.assertEqual(response.status_code, 404)

        # registration
        with self.assertNumQueries(4):
            response = self.client.get("/registrations/PF1018256/2")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "WIVETON, CLEY, BLAKENEY, MORSTON, FIELD DALLING, HINDRINGHAM AND THURSFORD",
        )

        # registration 404
        with self.assertNumQueries(1):
            response = self.client.get("/registrations/PH1020951/d")
        self.assertEqual(response.status_code, 404)

        # reg with csv lines in odd order:
        reg = Registration.objects.get(registration_number="PF0000003/113")
        self.assertEqual(reg.registration_status, "Cancelled")

        # admin
        self.client.force_login(self.user)
        response = self.client.get("/admin/vosa/licence/")
        self.assertContains(response, ">AINS<")

        self.assertEqual(Licence.objects.count(), 3)
