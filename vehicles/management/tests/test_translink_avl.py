from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import DataSource, Operator, Service, StopPoint
from bustimes.models import Calendar, Route, StopTime, Trip

from ...models import Vehicle, VehicleJourney
from ..commands.import_translink_avl import Command
from .test_bod_avl import patch_redis_client


class StopLoop(Exception):
    """raised to break out of the command's polling loop in tests"""


class TranslinkAVLTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Operator.objects.bulk_create(
            [Operator(noc="ULB"), Operator(noc="MET"), Operator(noc="GDR")]
        )
        source = DataSource.objects.create(name="Metro")
        service = Service.objects.create(current=True, line_name="G1")
        service.operator.add("GDR")
        route = Route.objects.create(service=service, line_name="G1", source=source)
        calendar = Calendar.objects.create(sat=True, start_date="2025-09-01")
        a = StopPoint.objects.create(atco_code="700000001636", active=True)
        b = StopPoint.objects.create(atco_code="700000016166", active=True)
        # same code in both directions - the vehicle's CurrentStop and NextStop
        # are in this order on cls.trip
        other_trip, cls.trip = Trip.objects.bulk_create(
            [
                Trip(
                    route=route,
                    calendar=calendar,
                    ticket_machine_code="0612",
                    start="06:12:00",
                    end="07:15:00",
                    inbound=inbound,
                )
                for inbound in (False, True)
            ]
        )
        StopTime.objects.bulk_create(
            [
                StopTime(trip=other_trip, stop=b, departure="06:40:00"),
                StopTime(trip=other_trip, stop=a, departure="06:41:00"),
                StopTime(trip=cls.trip, stop=a, departure="06:40:00"),
                StopTime(trip=cls.trip, stop=b, departure="06:41:00"),
            ]
        )

    def test(self):
        """Translink (Northern Ireland) AVL"""

        redis_client = fakeredis.FakeStrictRedis(version=7)

        with (
            vcr.use_cassette(
                str(Path(__file__).resolve().parent / "vcr" / "translink_avl.yaml")
            ) as cassette,
            mock.patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            mock.patch(
                "vehicles.management.import_live_vehicles.sleep", side_effect=StopLoop
            ),
            time_machine.travel("2025-09-27T05:43:00+00:00", tick=False),
        ):
            with self.assertNumQueries(107), self.assertRaises(StopLoop):
                call_command("import_translink_avl", "--immediate")

            cassette.rewind()

            with self.assertNumQueries(5), self.assertRaises(StopLoop):
                call_command("import_translink_avl", "--immediate")

        self.assertEqual(VehicleJourney.objects.count(), 8)

        journey = VehicleJourney.objects.get(vehicle__code="GDR-3225")
        self.assertEqual(journey.trip, self.trip)
        self.assertEqual(journey.service, self.trip.route.service)

    def test_frozen_previous_journey(self):
        # (ULB-32) a stale item for the previous trip, at null island,
        # mustn't create a new copy of that journey every time
        command = Command()
        command.source = DataSource.objects.create(name="Translink")
        command.operators = Operator.objects.in_bulk()

        vehicle = Vehicle.objects.create(code="ULB-32", operator_id="ULB")
        previous = VehicleJourney.objects.create(
            vehicle=vehicle,
            code="1001",
            source=command.source,
            route_name="319b",
            datetime=datetime(2026, 10, 29, 8, 22, 59, tzinfo=UTC),
            date="2026-10-29",
        )
        vehicle.latest_journey = current = VehicleJourney.objects.create(
            vehicle=vehicle,
            code="1003",
            source=command.source,
            route_name="319a",
            datetime=datetime(2026, 10, 29, 8, 24, 3, tzinfo=UTC),
            date="2026-10-29",
        )
        vehicle.save(update_fields=["latest_journey"])

        item = {
            "VehicleIdentifier": "ULB-32",
            "Timestamp": "2026-10-29T08:22:59+00:00",
            "JourneyIdentifier": "1001",
            "LineText": "319b",
            "DayOfOperation": "29.10.2026",
            "X": "0.000000",
            "Y": "0.000000",
        }
        with patch_redis_client() as redis_client:
            redis_client.set(
                f"vehicle{vehicle.id}",
                '{"coordinates": [-5.9, 54.6], "datetime": "2026-10-29T08:20:00+00:00", "heading": null}',
            )
            for _ in range(2):
                command.handle_item(item, vehicle=vehicle)
                command.save()

        self.assertEqual(
            list(vehicle.vehiclejourney_set.order_by("id")),
            [previous, current],
        )
