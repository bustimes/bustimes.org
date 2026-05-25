"""Tests for importing NaPTAN data"""

from pathlib import Path
from tempfile import TemporaryDirectory

import vcr
from django.core.management import call_command
from django.test import TestCase, override_settings

from ...models import AdminArea, DataSource, Locality, Region, StopArea, StopPoint


class NaptanTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        DataSource.objects.create(
            name="NaPTAN",
            url="https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=xml",
        )
        Region.objects.create(id="EA", name="East Anglia")
        AdminArea.objects.create(id=91, atco_code="290", name="Norfolk", region_id="EA")
        AdminArea.objects.create(
            id=110, atco_code="910", name="National - National Rail", region_id="EA"
        )
        AdminArea.objects.create(
            id=15, atco_code="076", name="Darlington", region_id="EA"
        )
        AdminArea.objects.create(
            id=90, atco_code="280", name="Merseyside", region_id="EA"
        )
        AdminArea.objects.create(
            id=92, atco_code="320", name="North Yorkshire", region_id="EA"
        )
        AdminArea.objects.create(id=701, atco_code="701", name="Derry", region_id="EA")
        Locality.objects.create(id="E0017763", name="Old Catton", admin_area_id=91)
        # Locality.objects.create(id="E0017806", name="Berney Arms", admin_area_id=91)
        Locality.objects.create(id="N0078629", name="Neasham Road", admin_area_id=15)
        Locality.objects.create(id="E0048995", name="Great Ayton", admin_area_id=92)
        Locality.objects.create(id="E0048637", name="Briningham", admin_area_id=91)
        StopPoint.objects.create(atco_code="07605395", active=True)
        StopPoint.objects.create(atco_code="2900flex1", active=False)

    def test_download(self):
        fixtures_dir = Path(__file__).resolve().parent / "fixtures"

        with (
            TemporaryDirectory() as temp_dir,
            vcr.use_cassette(str(fixtures_dir / "naptan.yml")) as cassette,
        ):
            temp_dir_path = Path(temp_dir)

            with override_settings(DATA_DIR=temp_dir_path):
                self.assertFalse((temp_dir_path / "NaPTAN.xml").exists())

                with (
                    self.assertNumQueries(25),
                    self.assertLogs(
                        "busstops.management.commands.naptan_new", "WARNING"
                    ) as cm,
                ):
                    call_command("naptan_new")
                self.assertEqual(
                    cm.output,
                    [
                        "WARNING:busstops.management.commands.naptan_new:"
                        "9100BRNYARM locality E0017806 does not exist"
                    ],
                )

                source = DataSource.objects.get(name="NaPTAN")
                self.assertEqual(str(source.datetime), "2022-01-19 12:56:29+00:00")

                self.assertTrue((temp_dir_path / "NaPTAN.xml").exists())

                cassette.rewind()

                # data hasn't changed
                with self.assertNumQueries(2):
                    call_command("naptan_new")

                cassette.rewind()

                with self.assertNumQueries(2):
                    call_command("naptan_new")

                cassette.rewind()

                source.datetime = None
                source.save()

                with (
                    self.assertNumQueries(11),
                    self.assertLogs(
                        "busstops.management.commands.naptan_new", "WARNING"
                    ) as cm,
                ):
                    call_command("naptan_new")
                self.assertEqual(
                    cm.output,
                    [
                        "WARNING:busstops.management.commands.naptan_new:"
                        "9100BRNYARM locality E0017806 does not exist"
                    ],
                )

                source = DataSource.objects.get(name="NaPTAN")
                self.assertEqual(str(source.datetime), "2022-01-19 12:56:29+00:00")

        # inactive stop in Wroxham
        stop = StopPoint.objects.get(atco_code="2900flex1")
        self.assertEqual(str(stop), "Wroxham ↑")
        self.assertEqual(stop.get_qualified_name(), "Wroxham")

        response = self.client.get("/stops/2900flEx1")  # case insensitivity
        self.assertContains(response, "Wroxham", status_code=404)

        # active stop
        response = self.client.get("/stops/2900C1323")
        self.assertContains(response, '<li title="NaPTAN code">NFOAJGDT</li>')
        self.assertContains(response, "<p>On White Woman Lane, near Longe Lane</p>")

        # stop in area
        response = self.client.get("/stops/07605395")
        self.assertContains(response, "Neasham Road Brankin Drive (O)")
        self.assertContains(response, "On Brankin Road, near Brankin Drive")
        self.assertContains(response, "Darlington")
        stop = response.context_data["object"]
        self.assertEqual(stop.admin_area.name, "Darlington")
        self.assertEqual(stop.stop_area_id, "076G5394")
        self.assertAlmostEqual(stop.latlong.x, -1.538063)
        self.assertAlmostEqual(stop.latlong.y, 54.511525)

        stop = StopPoint.objects.get(atco_code="3200GTAYTON0")
        self.assertAlmostEqual(stop.latlong.x, -1.117418697321657)
        self.assertAlmostEqual(stop.latlong.y, 54.48934185786758)
        self.assertEqual(str(stop), "Great Ayton Rail Station (entrance)")

        # overriden stop
        stop = StopPoint.objects.get(atco_code="2900B482")
        self.assertEqual(str(stop), "green (adj) \u2199")

        # stop area
        stop = StopArea.objects.get(id="701GA00001")
        self.assertAlmostEqual(stop.latlong.x, -6.966171)
        self.assertAlmostEqual(stop.latlong.y, 55.1911494)
