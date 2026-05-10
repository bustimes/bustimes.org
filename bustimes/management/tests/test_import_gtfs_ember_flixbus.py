import datetime
from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase, override_settings

from busstops.models import DataSource, Operator, Region, Service, StopCode, StopPoint
from vehicles.management.commands import import_gtfsr_ember

from .test_import_gtfs import make_zipfile
from ...models import Route, Trip

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@override_settings(DATA_DIR=FIXTURES_DIR)
class FlixbusTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Region.objects.create(id="GB", name="Great Britain")

        Operator.objects.create(noc="FLIX", name="FlixBus")
        Operator.objects.create(noc="EMBR", name="Ember")

        sources = DataSource.objects.bulk_create(
            [
                DataSource(
                    name="Ember",
                ),
                DataSource(
                    name="FlixBus",
                ),
            ]
        )

        service = Service.objects.create(line_name="004")
        service.operator.add("FLIX")
        route = Route.objects.create(
            line_name="004", code="UK004", service=service, source=sources[1]
        )
        Trip.objects.create(
            route=route,
            operator_id="FLIX",
            start="00:00",
            end="00:00",
            vehicle_journey_code="UK004-10-1500032024-LVC#NOT-00",
        )
        Trip.objects.create(
            route=route,
            operator_id="FLIX",
            start="00:00",
            end="00:00",
            vehicle_journey_code="N401-1-1955102024-STB#VE-00",
        )

        StopPoint.objects.create(
            atco_code="6200247603", common_name="Aeropuerto d'Edinburgh", active=1
        )
        StopPoint.objects.create(
            atco_code="3390C11", common_name="Nottingham", active=1
        )
        StopCode.objects.create(
            source=sources[1],
            code="9b69e4fe-3ecb-11ea-8017-02437075395e",
            stop_id="3390C11",
        )

    def test_not_modified(self):
        with (
            patch(
                "bustimes.management.commands.import_gtfs_flixbus.download_if_modified",
                return_value=(False, None),
            ),
            self.assertNumQueries(2),
        ):
            call_command("import_gtfs_flixbus")

    @time_machine.travel("2023-01-01")
    def test_import_gtfs_flixbus(self):
        with patch(
            "bustimes.management.commands.import_gtfs_flixbus.download_if_modified",
            return_value=(
                True,
                datetime.datetime(2024, 6, 18, 10, 0, 0, tzinfo=datetime.timezone.utc),
            ),
        ):
            call_command("import_gtfs_flixbus")

        response = self.client.get("/operators/flixbus")

        self.assertEqual(2, Service.objects.count())

        self.assertContains(response, "London - Northampton - Nottingham")
        self.assertContains(response, "London - Cambridge")

        service = Service.objects.get(line_name="UK004")

        response = self.client.get(service.get_absolute_url())
        self.assertContains(
            response, "<td>10:30</td><td>15:00</td><td>19:15</td><td>23:40</td>"
        )
        self.assertContains(response, "/stops/3390C11")

        # Uni of Nottm
        response = self.client.get(
            "/stops/89251c5e-72da-49e5-9077-e8549874c710", [("date", "2024-04-01")]
        )
        self.assertContains(
            response, ">University of Nottingham - North Entrance (Stop UN15)<"
        )
        self.assertEqual(7, len(response.context["departures"]))

        # Vicky Coach Stn
        response = self.client.get(
            "/stops/dcc0f769-9603-11e6-9066-549f350fcb0c", [("date", "2024-04-01")]
        )
        self.assertContains(response, ">London Victoria Coach Station<")
        # no departures, only arrivals
        self.assertEqual(7, len(response.context["departures"]))

        # British Summer Time:
        response = self.client.get(service.get_absolute_url(), [("date", "2024-04-01")])
        self.assertContains(
            response, "<td>10:30</td><td>15:00</td><td>19:15</td><td>23:40</td>"
        )

        self.assertEqual(Service.objects.count(), 2)

    @time_machine.travel("2024-09-16")
    def test_import_gtfs_ember(self):
        with (
            patch(
                "bustimes.management.commands.import_gtfs_ember.download_if_modified",
                return_value=(
                    True,
                    datetime.datetime(
                        2024, 6, 18, 10, 0, 0, tzinfo=datetime.timezone.utc
                    ),
                ),
            ),
            TemporaryDirectory() as directory,
            override_settings(DATA_DIR=directory),
        ):
            make_zipfile(directory, "ember_gtfs")

            with vcr.use_cassette(str(FIXTURES_DIR / "ember_gtfsr.yml")):
                call_command("import_gtfs_ember")
                call_command("import_gtfs_ember")

        response = self.client.get("/operators/ember")

        service = Service.objects.get(line_name="E1")

        response = self.client.get(service.get_absolute_url())
        self.assertContains(response, "6200206520")
        self.assertContains(response, "/stops/6200247603")

        self.assertEqual(Service.objects.count(), 2)

        # GTFSR
        command = import_gtfsr_ember.Command()
        command.do_source()

        with (
            patch(
                "vehicles.management.import_live_vehicles.redis_client",
                fakeredis.FakeStrictRedis(),
            ),
            vcr.use_cassette(str(FIXTURES_DIR / "ember_gtfsr.yml")),
        ):
            with self.assertNumQueries(84):
                command.update()
            with self.assertNumQueries(32):
                command.update()

        response = self.client.get(service.get_absolute_url())
        self.assertContains(
            response,
            "Pre-book journey at least 10 minutes before the scheduled departure time",
        )

        journey = service.vehiclejourney_set.first()
        self.assertEqual(str(journey.trip), "15:35")
        self.assertEqual(str(journey.datetime), "2024-01-18 15:35:00+00:00")
        self.assertEqual(str(journey.date), "2024-01-18")
        self.assertEqual(journey.code, "5WGNCip")
