import datetime
import json
from pathlib import Path
from unittest.mock import patch

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase, override_settings
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Operator, Region, Service, StopCode, StopPoint
from vehicles.management.commands import import_gtfsr_ember, import_gtfsr_flixbus
from vehicles.management.tests.test_bod_avl import (
    CapturingChannelLayer,
    distribute,
    patch_redis_client,
)
from vehicles.models import Livery, VehicleJourney
from vehicles.tasks import log_vehicle_journey

from ...models import Route, Trip

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# test data in `ember_gtfs.zip` and `flixus_eu.zip` dirs (not real zipfiles) in fixtures dir
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

        cls.service = Service.objects.create(line_name="004")
        cls.service.operator.add("FLIX")
        route = Route.objects.create(
            line_name="004", code="UK004", service=cls.service, source=sources[1]
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
        Livery.objects.create(name="FlixBus")

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
                datetime.datetime(2024, 6, 18, 10, 0, 0, tzinfo=datetime.UTC),
            ),
        ):
            call_command("import_gtfs_flixbus")

        response = self.client.get("/operators/flixbus")

        self.assertEqual(2, Service.objects.count())

        self.assertContains(response, "London - Northampton - Nottingham")
        self.assertContains(response, "London - Cambridge")

        response = self.client.get(self.service.get_absolute_url())
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
        response = self.client.get(
            self.service.get_absolute_url(), [("date", "2024-04-01")]
        )
        self.assertContains(
            response, "<td>10:30</td><td>15:00</td><td>19:15</td><td>23:40</td>"
        )

        self.assertEqual(Service.objects.count(), 2)

        command = import_gtfsr_flixbus.Command()
        command.do_source()

        log_vehicle_journey(
            None,
            {
                "Delay": "PT0M0S",
                "LineRef": "004",
                "Monitored": "true",
                "OriginRef": "6200600790",
                "OriginName": "Bus Station",
                "VehicleRef": "FLIX-0638",
                "OperatorRef": "FLIX",
                "DirectionRef": "I",
                "DestinationRef": "6290DP01",
                "DestinationName": "Livingston Deer Park",
                "PublishedLineName": "090",
                "FramedVehicleJourneyRef": {
                    "DataFrameRef": "2026_08_04_3210_285_40",
                    "DatedVehicleJourneyRef": "3210_285_40",
                },
                "OriginAimedDepartureTime": "2024-04-01T15:00:00Z",
                "DestinationAimedArrivalTime": "2026-04-01T18:00:00Z",
            },
            None,
            None,
            "",
            "",
            None,
        )

        trip = Trip.objects.get(vehicle_journey_code="UK004-10-1500042024-LVC#NOT-00")

        log_vehicle_journey(
            self.service.id,
            {
                "Delay": "PT0M0S",
                "LineRef": "004",
                "Monitored": "true",
                "OriginRef": "6200600790",
                "OriginName": "Bus Station",
                "VehicleRef": "FLIX-0638",
                "OperatorRef": "FLIX",
                "DirectionRef": "I",
                "DestinationRef": "6290DP01",
                "DestinationName": "Livingston Deer Park",
                "PublishedLineName": "090",
                "FramedVehicleJourneyRef": {
                    "DataFrameRef": "2026_08_04_3210_285_40",
                    "DatedVehicleJourneyRef": "3210_285_40",
                },
                "OriginAimedDepartureTime": "2024-04-01T15:00:00Z",
                "DestinationAimedArrivalTime": "2026-04-01T18:00:00Z",
            },
            None,
            None,
            "",
            "",
            trip.id,
        )

        server = fakeredis.FakeServer()
        async_redis_client = fakeredis.FakeAsyncRedis(server=server, version=7)
        redis_client = fakeredis.FakeStrictRedis(server=server, version=7)
        channel_layer = CapturingChannelLayer()

        with (
            time_machine.travel("2024-04-01 14:04:48+00:00"),
            patch_redis_client(redis_client),
            patch(
                "vehicles.management.commands.import_gtfsr_flixbus.get_channel_layer",
                return_value=channel_layer,
            ),
            vcr.use_cassette(str(FIXTURES_DIR / "flixbus_gtfsr.yml")),
        ):
            with self.assertNumQueries(16):
                command.update()
            with self.assertNumQueries(0):
                command.update()

            distribute(channel_layer, async_redis_client)

            journeys = VehicleJourney.objects.all()
            self.assertEqual(
                [str(journey) for journey in journeys],
                [
                    "1 Apr 24 15:00 004 3210_285_40 ",
                    "1 Apr 24 10:45 004 UK004-3-1045042024-NOT#LVC-00  to London Victoria Coach Station",
                    "1 Apr 24 15:00 004 UK004-7-1500042024-NOT#LVC-00  to London Victoria Coach Station",
                    "1 Apr 24 11:00 004 UK004-6-1100042024-LVC#NOT-00  to Nottingham",
                ],
            )

            # one location in each journey's history:
            for journey, polyline in zip(
                journeys,
                (
                    b"r{[iihyH_o~i`eB",
                    b"t|[abhyH_b~i`eB",
                    b"l|~EigdbI{m~i`eB",
                    b"|shDsdx}H}l~i`eB",
                ),
            ):
                self.assertEqual(redis_client.get(journey.get_redis_key()), polyline)

            with patch("vehicles.views.redis_client", redis_client):
                response = self.client.get("/vehicles.json")
            items = response.json()

            self.assertEqual(
                [item["id"] for item in items],
                [journey.vehicle_id or journey.id for journey in journeys],
            )
            self.assertIn("url", items[0]["service"])
            self.assertIsNone(items[0]["heading"])  # not moved yet

            # a newer location - the heading is calculated from the previous one:
            entity = gtfs_realtime_pb2.FeedEntity()
            entity.vehicle.trip.trip_id = "UK004-3-1045042024-NOT#LVC-00"
            entity.vehicle.trip.start_time = "10:45:00"
            entity.vehicle.trip.start_date = "20240401"
            entity.vehicle.position.latitude = 51.5
            entity.vehicle.position.longitude = -0.14
            entity.vehicle.timestamp = 1711980350
            with self.assertNumQueries(0):
                command.handle_item(entity, command.source.datetime)

            distribute(channel_layer, async_redis_client)

            item = json.loads(redis_client.get(f"vehicle{journeys[1].id}"))
            self.assertEqual(item["heading"], 33)
            self.assertEqual(
                redis_client.get(journeys[1].get_redis_key()),
                b"t|[abhyH_b~i`eBuq@}n@{O",
            )

    @time_machine.travel("2024-09-16")
    def test_import_gtfs_ember(self):
        with (
            patch(
                "bustimes.management.commands.import_gtfs_ember.download_if_modified",
                return_value=(
                    True,
                    datetime.datetime(2024, 6, 18, 10, 0, 0, tzinfo=datetime.UTC),
                ),
            ),
            vcr.use_cassette(str(FIXTURES_DIR / "ember_gtfsr.yml")),
        ):
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
            with self.assertNumQueries(71):
                command.update()
            with self.assertNumQueries(25):
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
