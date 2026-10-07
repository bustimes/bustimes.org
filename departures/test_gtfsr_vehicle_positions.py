from unittest.mock import patch

import fakeredis
import vcr
from django.test import TestCase, override_settings
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Operator, Service
from bustimes.models import Calendar, Route, Trip
from vehicles.management.commands.import_gtfsr_ie import Command
from vehicles.models import Vehicle, VehicleJourney


class GTFSRTTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="Realtime Transport Operators")

        cls.service = Service.objects.create(service_code="7", line_name="7")

        cls.operator = Operator.objects.create(
            noc="dub", name="Dublin Bus / Bus Átha Cliath"
        )
        cls.service.operator.add(cls.operator)

        route = Route.objects.create(
            service=cls.service, source=source, code="4099_68164"
        )

        calendar = Calendar.objects.create(
            mon=True,
            tue=True,
            wed=True,
            thu=True,
            fri=True,
            sat=True,
            sun=True,
            start_date="2022-05-04",
        )

        # deliberately duplicate trips
        cls.trip_1 = Trip.objects.create(
            route=route,
            ticket_machine_code="4099_18890",
            start="10:25:00",
            end="11:25:00",
            calendar=calendar,
            operator=cls.operator,
        )
        cls.trip_2 = Trip.objects.create(
            ticket_machine_code="4099_18890",
            start="10:25:00",
            end="11:25:00",
        )

    @patch(
        "vehicles.management.import_live_vehicles.redis_client",
        fakeredis.FakeStrictRedis(),
    )
    def test_vehicle_position(self):
        with (
            override_settings(
                NTA_API_KEY="poopants",
                CACHES={
                    "default": {
                        "BACKEND": "django.core.cache.backends.redis.RedisCache",
                        "LOCATION": "redis://",
                        "OPTIONS": {"connection_class": fakeredis.FakeRedisConnection},
                    }
                },
            ),
            vcr.use_cassette("fixtures/vcr/nta_ie_vehicle_positions.yaml"),
        ):
            c = Command()
            c.do_source()
            c.update()

        self.assertEqual(VehicleJourney.objects.count(), 51)
        self.assertEqual(self.service.vehiclejourney_set.count(), 5)
        self.assertEqual(self.trip_1.vehiclejourney_set.count(), 1)
        self.assertEqual(self.trip_2.vehiclejourney_set.count(), 0)
        self.assertEqual(Vehicle.objects.filter(operator=None).count(), 50)
        self.assertEqual(self.operator.vehicle_set.count(), 1)

        vehicle_journey = VehicleJourney.objects.filter(trip__isnull=False).get()
        self.assertEqual(str(vehicle_journey.datetime), "2024-06-06 01:55:00+00:00")

    def test_occupancy(self):
        entity = gtfs_realtime_pb2.FeedEntity()
        entity.vehicle.position.latitude = 53.3
        entity.vehicle.position.longitude = -6.2
        location = Command().create_vehicle_location(entity)
        self.assertIsNone(location.occupancy)

        entity.vehicle.occupancy_status = entity.vehicle.EMPTY
        location = Command().create_vehicle_location(entity)
        self.assertEqual(location.occupancy, "Empty")

        entity.vehicle.occupancy_status = entity.vehicle.FULL
        location = Command().create_vehicle_location(entity)
        self.assertEqual(location.occupancy, "Full")
