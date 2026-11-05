from datetime import UTC, datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import TestCase
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Operator

from ...models import Vehicle
from ..commands.import_gtfsr_feed import Command, parse_feed


class GTFSRFeedTest(TestCase):
    def test_parse_feed(self):
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.header.gtfs_realtime_version = "2.0"
        feed.header.timestamp = 1792150470
        for vehicle_id in ("1", "2"):
            entity = feed.entity.add(id=vehicle_id)
            entity.vehicle.vehicle.id = vehicle_id
        data = feed.SerializeToString()

        self.assertEqual(parse_feed(data), feed)

        # declare the first entity's length to be 1 byte too short,
        # like the Skopje feed sometimes does
        entity_length = len(feed.entity[0].SerializeToString())
        header_length = len(feed.header.SerializeToString()) + 2
        corrupt = bytearray(data)
        assert corrupt[header_length + 1] == entity_length
        corrupt[header_length + 1] -= 1

        corrupt_feed = parse_feed(bytes(corrupt))
        self.assertEqual(corrupt_feed.header.timestamp, 1792150470)
        self.assertEqual(
            [entity.vehicle.vehicle.id for entity in corrupt_feed.entity], ["1", "2"]
        )

    def test_get_journey_no_start_time(self):
        # (Cap Cotentin) trips not in the timetable, with no start_time,
        # mustn't all be given a datetime of midnight - and so be merged
        command = Command()
        command.source = DataSource.objects.create(name="Cap Cotentin")
        command.source.datetime = datetime(2026, 10, 27, 7, tzinfo=UTC)
        command.tzinfo = ZoneInfo("Europe/Paris")
        command.trips = {}

        entity = gtfs_realtime_pb2.FeedEntity(id="1")
        entity.vehicle.timestamp = 1792134000
        entity.vehicle.trip.trip_id = "3815905-2026_H-Mult_LaM-L-Ma-Me-00-1101100"
        entity.vehicle.trip.start_date = "20261027"
        entity.vehicle.trip.route_id = "C"

        journey = command.get_journey(entity, Vehicle())
        self.assertIsNone(journey.datetime)
        self.assertEqual(str(journey.date), "2026-10-27")

        entity.vehicle.trip.start_time = "07:15:00"
        journey = command.get_journey(entity, Vehicle())
        self.assertEqual(str(journey.datetime), "2026-10-27 07:15:00+01:00")

    def test_import_zditm_vehicles(self):
        Operator.objects.create(noc="ZDITM", name="ZDiTM Szczecin")
        Vehicle.objects.create(code="254", fleet_code="122+123", operator_id="ZDITM")
        response = Mock(
            json=lambda: {
                "data": [
                    {
                        "vehicle_type": "tram",
                        "vehicle_id": 254,
                        "vehicle_number": "122+123",
                        "vehicle_model": "Moderus Beta <->",
                        "vehicle_operator": "Tramwaje Szczecińskie",
                    },
                    {
                        "vehicle_type": "bus",
                        "vehicle_id": 3,
                        "vehicle_number": "1050",
                        "vehicle_model": "Solaris Urbino 18",
                        "vehicle_operator": "SPA Klonowica",
                    },
                ]
            }
        )
        with patch(
            "vehicles.management.commands.import_zditm_vehicles.requests.get",
            return_value=response,
        ):
            call_command("import_zditm_vehicles")

        vehicle = Vehicle.objects.select_related("vehicle_type").get()
        self.assertEqual(vehicle.vehicle_type.name, "Moderus Beta (bidirectional)")
        self.assertEqual(vehicle.vehicle_type.style, "tram")
        self.assertEqual(vehicle.data, {"Operator": "Tramwaje Szczecińskie"})
