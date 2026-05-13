import datetime
import zipfile
from pathlib import Path
from shutil import ReadError
from tempfile import TemporaryDirectory
from unittest.mock import patch

import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase, override_settings

from busstops.models import AdminArea, DataSource, Operator, Region, Service, StopPoint

from ...models import Route, Trip
from ...download_utils import download_if_modified

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def make_zipfile(directory, name):
    dir_path = FIXTURES_DIR / name
    feed_path = Path(directory) / f"{name}.zip"
    with zipfile.ZipFile(feed_path, "a") as open_zipfile:
        for item in dir_path.iterdir():
            open_zipfile.write(item, item.name)


@override_settings(DATA_DIR=FIXTURES_DIR)
class GTFSTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        """Make a GTFS feed (a zip file containing some text files)."""

        cls.leinster = Region.objects.create(id="LE", name="Leinster")
        cls.ulster = Region.objects.create(id="UL", name="Ulster")
        cls.dublin = AdminArea.objects.create(
            id=822, atco_code=822, region_id="LE", name="Dublin"
        )
        cls.south_dublin = AdminArea.objects.create(
            id=823, atco_code=823, region_id="LE"
        )
        cls.donegal = AdminArea.objects.create(id=853, atco_code=853, region_id="UL")

        # Create an existing operator (with a slightly different name) to test that it is re-used
        Operator.objects.create(
            noc="ie-132", name="Seumas Doherty", region=cls.leinster
        )

        # this should get updated later
        StopPoint.objects.create(
            atco_code="8220DB000759", common_name="Estadio Donnybrook", active=True
        )

        DataSource.objects.bulk_create(
            [
                DataSource(
                    name="Seamus Doherty",
                    url="https://www.transportforireland.ie/transitData/Data/GTFS_Seamus_Doherty.zip",
                ),
                DataSource(
                    name="Mortons",
                    url="https://www.transportforireland.ie/transitData/Data/GTFS_Mortons.zip",
                ),
                DataSource(
                    name="Wexford Bus",
                    url="https://www.transportforireland.ie/transitData/Data/GTFS_Wexford_Bus.zip",
                ),
            ]
        )

    def test_import_gtfs(self):
        with TemporaryDirectory() as directory:
            make_zipfile(directory, "GTFS_Seamus_Doherty")
            make_zipfile(directory, "GTFS_Mortons")
            make_zipfile(directory, "GTFS_Wexford_Bus")

            with (
                vcr.use_cassette(
                    str(FIXTURES_DIR / "google_transit_ie.yaml"),
                ) as cassette,
                override_settings(DATA_DIR=Path(directory)),
                self.assertLogs("bustimes.download_utils", "ERROR") as errors,
                self.assertLogs(
                    "bustimes.management.commands.import_gtfs", "WARNING"
                ) as warnings,
            ):
                call_command(
                    "import_gtfs", ["Mortons", "Wexford Bus", "Seamus Doherty"]
                )

                cassette.rewind()

                # import a second time - test that it's OK if stuff already exists
                call_command(
                    "import_gtfs", ["Mortons", "Wexford Bus", "Seamus Doherty"]
                )

        self.assertEqual(
            sorted(errors.output),
            [
                "ERROR:bustimes.download_utils:<Response [404]> "
                "https://www.transportforireland.ie/transitData/Data/GTFS_Mortons.zip",
                "ERROR:bustimes.download_utils:<Response [404]> "
                "https://www.transportforireland.ie/transitData/Data/GTFS_Mortons.zip",
                "ERROR:bustimes.download_utils:<Response [404]> "
                "https://www.transportforireland.ie/transitData/Data/GTFS_Seamus_Doherty.zip",
                "ERROR:bustimes.download_utils:<Response [404]> "
                "https://www.transportforireland.ie/transitData/Data/GTFS_Seamus_Doherty.zip",
            ],
        )
        self.assertEqual(
            warnings.output,
            [
                "WARNING:bustimes.management.commands.import_gtfs:"
                "trip 2868_105 has no stop times",
                "WARNING:bustimes.management.commands.import_gtfs:"
                "trip 2868_105 has no stop times",
            ],
        )

        # stops
        self.assertEqual(StopPoint.objects.count(), 267)
        stop = StopPoint.objects.get(atco_code="822000153")
        self.assertEqual(stop.common_name, "Terenure Library")
        self.assertEqual(stop.admin_area_id, 822)

        self.assertEqual(Operator.objects.count(), 3)
        self.assertEqual(
            Operator.objects.filter(service__current=True).distinct().count(), 3
        )

        # small timetable
        with time_machine.travel("2017-06-07"):
            response = self.client.get("/services/165")
        timetable = response.context_data["timetable"]
        self.assertEqual(str(timetable.groupings[0]), "To Citywest Road")
        self.assertEqual(str(timetable.groupings[1]), "To Ailesbury Road")
        self.assertEqual(str(timetable.groupings[0].rows[0].times), "[07:45]")
        self.assertEqual(str(timetable.groupings[0].rows[4].times), "[07:52]")
        self.assertEqual(str(timetable.groupings[0].rows[6].times), "[08:01]")
        self.assertEqual(str(timetable.groupings[1].rows[0].times), "[17:20]")
        self.assertEqual(str(timetable.groupings[1].rows[6].times), "[17:45]")
        self.assertEqual(str(timetable.groupings[1].rows[-1].times), "[18:25]")
        self.assertEqual(len(timetable.groupings[0].rows), 18)
        self.assertEqual(len(timetable.groupings[1].rows), 14)

        self.assertContains(
            response,
            '<a href="https://www.transportforireland.ie/transitData/PT_Data.html#:~:text=Mortons" rel="nofollow">'
            "National Transport Authority</a>",
        )

        for day in (
            datetime.date(2017, 6, 11),
            datetime.date(2017, 12, 25),
            datetime.date(2015, 12, 3),
            datetime.date(2020, 12, 3),
        ):
            with time_machine.travel(day):
                with self.assertNumQueries(18):
                    response = self.client.get(f"/services/165?date={day}")
                timetable = response.context_data["timetable"]
                self.assertEqual(day, timetable.date)
                self.assertEqual(timetable.groupings, [])

        # big timetable
        service = Service.objects.get(route__code="21-963-1-y11-1")
        self.assertEqual(service.mode, "bus")
        timetable = service.get_timetable(datetime.date(2017, 6, 7)).render()
        self.assertEqual(
            str(timetable.groupings[0]), "To <NA>"
        )  # this looks like a bug, but in practice there is always a headsign
        self.assertEqual(
            str(timetable.groupings[0].rows[0].times), "['', 10:15, '', 14:15, 17:45]"
        )
        self.assertEqual(
            str(timetable.groupings[0].rows[1].times), "['', 10:20, '', 14:20, 17:50]"
        )
        self.assertEqual(
            str(timetable.groupings[0].rows[2].times), "['', 10:22, '', 14:22, 17:52]"
        )

        # self.assertTrue(service.geometry)

        # block_id missing or <NA> should not result in the string "<NA>"
        trip = Trip.objects.get(ticket_machine_code="2.Mo-Fr.21-963-1-y11-1.2.O")
        self.assertEqual(trip.block, "")

        self.assertEqual(str(service.source), "Seamus Doherty")

        # admin area
        res = self.client.get(self.dublin.get_absolute_url())
        self.assertContains(res, "Bus services in Dublin", html=True)
        self.assertContains(res, "/services/165")

        # check that the common_name and latlong of the existing stop were updated
        stop = StopPoint.objects.get(atco_code="8220DB000759")
        self.assertEqual(stop.common_name, "Donnybrook, Old Wesley Rugby Football Club")
        self.assertEqual(
            str(stop.latlong), "SRID=4326;POINT (-6.23334551683733 53.3203488508422)"
        )

    def test_download_if_modified(self):
        path = Path("poop.txt")
        url = "https://bustimes.org/favicon.ico"
        source = DataSource.objects.create(url=url)

        path.unlink(missing_ok=True)

        cassette = str(FIXTURES_DIR / "download_if_modified.yaml")

        with vcr.use_cassette(cassette, match_on=["uri", "headers"]) as cassette:
            changed, when = download_if_modified(path, source)
            self.assertTrue(changed)
            self.assertEqual(str(when), "2024-09-06 13:11:02+00:00")
            self.assertEqual(source.etag, 'W/"66daf156-37b"')

            changed, when = download_if_modified(path, source)
            self.assertFalse(changed)

        self.assertTrue(path.exists())
        path.unlink()

    def test_handle(self):
        with (
            patch(
                "bustimes.management.commands.import_gtfs.download_if_modified",
                return_value=(True, None),
                raise_exception=True,
            ),
            patch(
                "bustimes.management.commands.import_gtfs.Command.handle_zipfile",
                side_effect=ReadError("bad zipfile"),
            ),
            self.assertLogs("bustimes.management.commands.import_gtfs", "ERROR"),
        ):
            call_command("import_gtfs", "Wexford Bus")

        self.assertFalse(Route.objects.all())
