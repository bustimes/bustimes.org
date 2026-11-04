from http import HTTPStatus
from pathlib import Path

import time_machine
from django.core.management import call_command
from django.test import TestCase
from vcr import use_cassette

from busstops.models import DataSource, Operator, Service
from bustimes.models import Route

from .management.commands.import_netex_fares import (
    Command,
    delete_unused_prices,
    get_service,
)
from .models import (
    Cell,
    Column,
    DataSet,
    DistanceMatrixElement,
    FareTable,
    FareZone,
    Price,
    Row,
    Tariff,
    TimeInterval,
)


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
            use_cassette(path / "bod_fares.yaml"),
            self.assertLogs("fares.management.commands.import_netex_fares") as cm,
        ):
            call_command(
                "import_netex_fares", "XCpEBAoqPDfVdYRoUahb3F2nEZTJJCULXZCPo5x8"
            )

        self.assertEqual(
            cm.output,
            [
                "INFO:fares.management.commands.import_netex_fares:AC Williams_20201119 06:45:17",
                "WARNING:fares.management.commands.import_netex_fares:Service matching query does not exist. WMSA WM07 WM07",
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
            "From Wednesday 30 September 2020",
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

        services[0].current = False
        services[0].save(update_fields=["current"])
        self.assertEqual(
            get_service(self.a_c_williams, "1", "op:1#WMSA001#04/10/2020"), services[1]
        )

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

        # Stagecoach - prices referring to distance matrix elements, made into tables
        tariff = Tariff.objects.get(
            filename="FX_PI_01_UK_SCTE_LINE_FARE_Line-59t@Outbound_wef-20220208_20220211-0936.xml"
        )
        self.assertEqual(tariff.faretable_set.count(), 7)
        table = tariff.faretable_set.get(
            code="fareTable@Line_59t@Outbound@trip@Hpool_Adult_Single_Zone"
        )
        self.assertEqual(
            [row.name for row in table.rows()],
            [
                "StocktonHighSt",
                "BrownsBridge",
                "Stockton6Form",
                "SorrelClose",
                "LeamLane",
                "TheFalcon",
            ],
        )
        self.assertEqual(table.columns()[0].name, "BrownsBridge")
        self.assertEqual(table.columns()[5].name, "NTeesHospORHardwick")
        self.assertEqual(
            [str(cell.price) if cell else None for cell in table.rows()[1].cells()],
            [None, "1.20", "1.50", "1.60", "1.90", "1.90"],
        )
        response = self.client.get(tariff.get_absolute_url())
        self.assertContains(response, "£1.20</td>")
        self.assertContains(response, 'colspan="7">StocktonHighSt</th>')

        tariff = Tariff.objects.get(
            filename="KBUS_FF_ArrivaAdd-on_2Multi_6d7e341a-0680-4397-9b3f-90a290087494_637613495098903655.xml"
        )
        self.assertEqual(
            str(tariff.valid_between),
            "[2021-07-08 00:00:00+00:00, 2121-07-08 00:00:00+00:00]",
        )


class StagecoachTest(TestCase):
    def test_distance_matrix_prices(self):
        operator = Operator.objects.create(noc="SBLB", name="Stagecoach Bluebird")
        service = Service.objects.create(line_name="107", current=True)
        service.operator.add(operator)
        Route.objects.create(
            line_name="107", service=service, source=DataSource.objects.create()
        )

        command = Command()
        command.user_profiles = {}
        command.sales_offer_packages = {}
        command.fare_products = {}
        command.fare_zones = {}

        filename = "FX_PI_01_UK_SBLB_LINE_FARE_107_I_Adult-Single_wef-20260817_20260812-0756.xml"
        with (Path(__file__).resolve().parent / "data" / filename).open() as open_file:
            command.handle_file(DataSet.objects.create(), open_file, filename)

        tariff = Tariff.objects.get()
        self.assertEqual(tariff.services.get(), service)
        self.assertEqual(tariff.distancematrixelement_set.count(), 6)

        table = tariff.faretable_set.get()
        self.assertEqual(table.name, "Aberdeen-Montrose - Adult Single")
        self.assertEqual(
            [row.name for row in table.rows()],
            ["Montrose Rail Stn", "Montrose Centre", "Montrose Local"],
        )
        self.assertEqual(
            [column.name for column in table.columns()],
            ["Montrose Centre", "Montrose Local", "Montrose Vetco Gray"],
        )
        self.assertEqual(
            [
                [str(cell.price) if cell else None for cell in row.cells()]
                for row in table.rows()
            ],
            [
                ["2.40", "2.40", "2.40"],
                [None, "2.40", "2.40"],
                [None, None, "2.40"],
            ],
        )

        response = self.client.get(f"{service.get_absolute_url()}/fares")
        self.assertContains(response, "£2.40</td>", count=6)
        self.assertContains(
            response,
            f'<th id="fare-table-{table.id}-0" colspan="4">Montrose Rail Stn</th>',
        )
        self.assertContains(
            response,
            f'<td headers="fare-table-{table.id}-0 fare-table-{table.id}-3">£2.40</td>',
        )
        self.assertContains(
            response,
            f'<th id="fare-table-{table.id}-3" colspan="1">Montrose Vetco Gray</th>',
        )

    def test_network_ticket(self):
        command = Command()
        command.user_profiles = {}
        command.sales_offer_packages = {}
        command.fare_products = {}
        command.fare_zones = {}

        dataset = DataSet.objects.create()
        filename = "FX_PI_01_UK_SBLB_NETWORK_FARE_Abdn-Comm-Xtra_wef-20260603_20260612-1151.xml"
        with (Path(__file__).resolve().parent / "data" / filename).open() as open_file:
            command.handle_file(dataset, open_file, filename)

        tariff = Tariff.objects.get()
        price = tariff.price_set.get()
        self.assertEqual(str(price), "118")
        self.assertEqual(price.time_interval.name, "1 Month")

        response = self.client.get(tariff.get_absolute_url())
        self.assertContains(response, "£118")

        response = self.client.get(dataset.get_absolute_url())
        self.assertContains(response, "Tariff for Aberdeen Commuter MegaRider Xtra")


class FareTableRowOrderTest(TestCase):
    def test_rows_follow_column_order(self):
        dataset = DataSet.objects.create(name="test")
        tariff = Tariff.objects.create(source=dataset, code="t", name="t", filename="t")
        table = FareTable.objects.create(tariff=tariff, code="t", name="t")
        for order, name in enumerate(
            ("Dussindale", "Thunder Lane", "Eaton", "Attleborough"), 1
        ):
            Column.objects.create(table=table, name=name, order=order)
        # Eaton added to the end of the rows when the route was extended
        for order, name in enumerate(
            ("Dussindale", "Thunder Lane", "Attleborough", "Eaton"), 1
        ):
            Row.objects.create(table=table, name=name, order=order)

        self.assertEqual(
            [row.name for row in table.rows()],
            ["Dussindale", "Thunder Lane", "Eaton", "Attleborough"],
        )

    def test_staircase_circular_route(self):
        dataset = DataSet.objects.create(name="test")
        tariff = Tariff.objects.create(source=dataset, code="t", name="t", filename="t")
        table = FareTable.objects.create(tariff=tariff, code="t", name="t")
        stops = ("Barclay Street", "Forest Park", "Barclay Street")
        rows = [
            Row.objects.create(
                table=table, name=name, code=f"zone@{i}@boarding", order=i
            )
            for i, name in enumerate(stops[:-1], 1)
        ]
        columns = [
            Column.objects.create(
                table=table, name=name, code=f"zone@{i}@alighting", order=i - 1
            )
            for i, name in enumerate(stops, 1)
            if i > 1
        ]
        price = Price.objects.create(amount="3.00")
        for row, column in ((0, 0), (0, 1), (1, 1)):
            Cell.objects.create(row=rows[row], column=columns[column], price=price)

        self.assertEqual(
            [(stop["name"], len(stop["cells"])) for stop in table.staircase],
            [("Barclay Street", 0), ("Forest Park", 1), ("Barclay Street", 2)],
        )

    def test_staircase_both_directions(self):
        dataset = DataSet.objects.create(name="test")
        tariff = Tariff.objects.create(source=dataset, code="t", name="t", filename="t")
        table = FareTable.objects.create(tariff=tariff, code="t", name="t")
        stops = ("Dussindale", "Thunder Lane", "Norwich City")
        rows = [
            Row.objects.create(table=table, name=name, order=i)
            for i, name in enumerate(stops)
        ]
        columns = [
            Column.objects.create(table=table, name=name, order=i)
            for i, name in enumerate(stops)
        ]
        low = Price.objects.create(amount="1.80")
        high = Price.objects.create(amount="2.40")
        cells = {}
        for i, row in enumerate(rows):
            for j, column in enumerate(columns):
                cells[i, j] = Cell.objects.create(
                    row=row, column=column, price=high if abs(i - j) > 1 else low
                )

        self.assertEqual(
            [
                [cell["price"] and str(cell["price"]) for cell in stop["cells"]]
                for stop in table.staircase
            ],
            [["1.80"], ["1.80", "1.80"], ["2.40", "1.80", "1.80"]],
        )
        self.assertEqual(
            table.staircase[1]["cells"][1]["headers"], f"fare-table-{table.id}-1"
        )

        # different fare in the other direction
        cells[2, 0].price = low
        cells[2, 0].save()
        del table.staircase
        self.assertIsNone(table.staircase)


class DeleteUnusedPricesTest(TestCase):
    def test_delete_unused_prices(self):
        dataset = DataSet.objects.create(name="test")
        tariff = Tariff.objects.create(source=dataset, code="t", name="t", filename="t")
        zone = FareZone.objects.create(code="z", name="z", source=dataset)

        used = Price.objects.create(amount="1.00")
        DistanceMatrixElement.objects.create(
            code="d", price=used, start_zone=zone, end_zone=zone, tariff=tariff
        )
        Price.objects.create(amount="2.00")  # unused
        tariff_price = Price.objects.create(amount="3.00", tariff=tariff)
        newer = Price.objects.create(amount="4.00")

        with (
            self.assertLogs("fares.management.commands.import_netex_fares") as cm,
            self.assertNumQueries(1),
        ):
            delete_unused_prices(tariff_price.id)
        self.assertEqual(
            cm.output,
            [
                "INFO:fares.management.commands.import_netex_fares:deleted 1 unused prices"
            ],
        )

        self.assertEqual(
            set(Price.objects.values_list("id", flat=True)),
            {used.id, tariff_price.id, newer.id},
        )
