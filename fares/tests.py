from http import HTTPStatus
from pathlib import Path

import time_machine
from django.core.management import call_command
from django.test import TestCase
from vcr import use_cassette

from busstops.models import DataSource, Operator, Service
from bustimes.models import Route

from .management.commands.import_netex_fares import Command, get_service
from .models import DataSet, FareZone, Tariff, TimeInterval


class FaresTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a_c_williams = Operator.objects.create(noc="WMSA")
        cls.wm06 = Service.objects.create(line_name="wm06", current=True)
        source = DataSource.objects.create()
        Route.objects.create(line_name="wm06", service=cls.wm06, source=source)
        cls.wm06.operator.add(cls.a_c_williams)

    @time_machine.travel("2020-06-10", tick=False)
    def test_bod_netex(self):
        path = Path(__file__).resolve().parent / "data"

        with (
            use_cassette(str(path / "bod_fares.yaml")),
            self.assertLogs("fares.management.commands.import_netex_fares") as cm,
        ):
            call_command(
                "import_netex_fares", "XCpEBAoqPDfVdYRoUahb3F2nEZTJJCULXZCPo5x8"
            )

        self.assertEqual(
            cm.output,
            [
                "INFO:fares.management.commands.import_netex_fares:AC Williams_20201119 06:45:17",
                "WARNING:fares.management.commands.import_netex_fares:Service matching query does not exist. WMSA WM07",
                "INFO:fares.management.commands.import_netex_fares:  ⏱️ 0:00:00",
                "INFO:fares.management.commands.import_netex_fares:AC Williams_20201119 06:46:57",
                "INFO:fares.management.commands.import_netex_fares:  ⏱️ 0:00:00",
            ],
        )

        tariff = Tariff.objects.get(name="A C Williams WM06 - single fares")

        self.assertEqual(self.wm06, tariff.services.get())

        # tariff detail view
        response = self.client.get(tariff.get_absolute_url())

        self.assertContains(response, "A C Williams WM06 - single fares")
        self.assertContains(response, "<td>£1.70</td>")
        self.assertContains(response, "RAF Cranwell")

        origin = FareZone.objects.get(name="Welbourn", source=tariff.source)
        destination = FareZone.objects.get(name="Cranwell", source=tariff.source)
        response = self.client.get(
            f"{tariff.get_absolute_url()}?origin={origin.id}&destination={destination.id}"
        )

        self.assertContains(response, "<h3>Welbourn to Cranwell</h3>")
        self.assertContains(response, "<p>adult single: £1.50</p>")

        # dataset detail view
        url = tariff.source.get_absolute_url()
        response = self.client.get(url)
        self.assertContains(
            response,
            "Wednesday 30 September 2020\u2009\u2013\u2009Monday 30 September 2120",
        )
        response = self.client.get(
            f"{url}?origin={origin.id}&destination={destination.id}"
        )
        self.assertContains(response, "<h3>Welbourn to Cranwell</h3>")
        self.assertContains(response, "<p>adult single: £1.50</p>")

        # fares index
        response = self.client.get("/fares/")
        self.assertContains(response, "WM06 - Sleaford to Welbourn - Version 1")
        self.assertContains(response, "19 Nov 2020")

        self.assertEqual(TimeInterval.objects.count(), 0)

        tariff.source.published = True
        tariff.source.save(update_fields=["published"])

        # service detail view
        url = self.wm06.get_absolute_url()
        response = self.client.get(url)
        self.assertContains(response, ">Fare tables</")
        self.assertContains(response, ">A C Williams WM06 - single</option>")

        # service fares list view
        response = self.client.get(f"{url}/fares")
        self.assertContains(response, '<th colspan="8">Welbourn</th>')
        self.assertContains(response, '<th colspan="2">Greylees</th>')
        self.assertContains(response, '<th colspan="1">Ancaster</th')

        # fare table
        response = self.client.get(
            response.context["tariffs"][0].faretable_set.all()[0].get_absolute_url()
        )
        self.assertContains(response, '<th colspan="8">Welbourn</th>')
        self.assertContains(response, '<th colspan="2">Greylees</th>')
        self.assertContains(response, '<th colspan="1">Ancaster</th')

    def test_service_fares_not_found(self):
        response = self.client.get(f"{self.wm06.get_absolute_url()}/fares")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_get_service_line_id(self):
        source = DataSource.objects.get()
        services = [
            Service.objects.create(line_name="1", current=True) for _ in range(2)
        ]
        for service, line_id in zip(
            services, ("WMSA:PD0000479:12:1", "WMSA:PD0000479:31:1")
        ):
            service.operator.add(self.a_c_williams)
            Route.objects.create(
                line_name="1",
                line_id=line_id,
                service=service,
                source=source,
                code=line_id,
            )

        get_service.cache_clear()
        self.assertEqual(
            get_service(self.a_c_williams, "1", "WMSA:PD0000479:31:1"), services[1]
        )
        with self.assertLogs("fares.management.commands.import_netex_fares"):
            self.assertIsNone(get_service(self.a_c_williams, "1", "1_Outbound"))

    def test_ad_hoc(self):
        command = Command()
        command.user_profiles = {}
        command.sales_offer_packages = {}
        command.fare_products = {}
        command.fare_zones = {}

        source = DataSet.objects.create()

        base_path = Path(__file__).resolve().parent / "data"

        for filename, number in (
            ("connexions_Harrogate_Coa_16.286Z_IOpbaMX.xml", 42),
            ("FLDSa0eb4e10_1605250801329.xml", 22),
            (
                "KBUS_FF_ArrivaAdd-on_2Multi_6d7e341a-0680-4397-9b3f-90a290087494_637613495098903655.xml",
                12,
            ),
            (
                "FECS_23A_Outbound_YPSingle_6764fa3b-4b05-4331-9bea-c7bb90212531_637829447220443476.xml",
                30,
            ),
            ("LYNX 39 single.xml", 27),
            ("LYNX Coast.xml", 75),
            ("LYNX Townrider.xml", None),
            (
                "NADS_103A_Inbound_AdultReturn_aae41d08-15c5-4fef-bf58-e8188410605e_637503825593765582.xml",
                None,
            ),
            ("STBC96615325_1597249888210_YFXY9eP.xml", None),
            ("TGTC238e19ce_1603195065008_yJWka80.xml", None),
            ("TWGT0b3b32d1_1600857778793_2gKCmVT_2.xml", None),
            ("FX_PI_01_UK_SCTE_PRODUCTS_COMMON_wef-20220208_20220211-0936.xml", None),
            (
                "FX_PI_01_UK_SCTE_LINE_FARE_Line-59t@Outbound_wef-20220208_20220211-0936.xml",
                None,
            ),
        ):
            path = base_path / filename

            with path.open() as open_file:
                if number is None:
                    command.handle_file(source, open_file, filename)
                else:
                    with number and self.assertNumQueries(number):
                        command.handle_file(source, open_file, filename)

        tariff = Tariff.objects.get(
            filename="KBUS_FF_ArrivaAdd-on_2Multi_6d7e341a-0680-4397-9b3f-90a290087494_637613495098903655.xml"
        )
        self.assertEqual(
            str(tariff.valid_between),
            "[2021-07-08 00:00:00+00:00, 2121-07-08 00:00:00+00:00]",
        )
