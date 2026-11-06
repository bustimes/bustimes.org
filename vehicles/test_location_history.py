import struct
from datetime import date
from io import StringIO
from unittest.mock import patch

import fakeredis
import time_machine
from django.core.management import call_command
from django.test import TestCase

from busstops.models import DataSource

from .models import Vehicle, VehicleJourney, VehicleLocationHistory
from .tasks import archive_old_journey_locations
from .time_aware_polyline import decode_time_aware_polyline, encode_time_aware_polyline


@time_machine.travel("2026-11-05T12:00:00Z", tick=False)
class VehicleLocationHistoryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="Test")
        cls.vehicle = Vehicle.objects.create(code="1", slug="test-1")
        cls.date = date(2026, 11, 3)
        cls.journeys = [
            VehicleJourney.objects.create(
                vehicle=cls.vehicle,
                datetime=f"{cls.date}T{hour}:00:00Z",
                date=cls.date,
                source=source,
            )
            for hour in (10, 11, 12)
        ]
        cls.old_journey = VehicleJourney.objects.create(
            vehicle=cls.vehicle,
            datetime="2026-09-01T10:00:00Z",
            date="2026-09-01",
            source=source,
        )

    def test_archive(self):
        redis = fakeredis.FakeStrictRedis()
        polyline = encode_time_aware_polyline([[1.0, 51.0, 1762164000]])
        redis.set(self.journeys[0].get_redis_key(), polyline)
        redis.set(self.journeys[1].get_redis_key(), polyline)
        # old 'list' type
        redis.rpush(
            self.old_journey.get_redis_key(),
            struct.pack("I 2f ?h ?h", 1756720800, 1.0, 51.0, True, 90, False, 0),
        )

        with patch("vehicles.tasks.redis_client", redis):
            archive_old_journey_locations()

        # only archives the day before yesterday
        self.assertEqual(redis.keys(), [self.old_journey.get_redis_key()])
        history = VehicleLocationHistory.objects.get()
        self.assertEqual(
            history.polylines,
            {
                str(self.journeys[0].id): polyline,
                str(self.journeys[1].id): polyline,
            },
        )

        # late arrival merged into the existing row
        redis.set(self.journeys[2].get_redis_key(), polyline)
        with patch("vehicles.tasks.redis_client", redis):
            archive_old_journey_locations()
        history.refresh_from_db()
        self.assertEqual(len(history.polylines), 3)

        # backfill
        with patch("vehicles.tasks.redis_client", redis):
            call_command("archive_journey_locations", "70", stdout=StringIO())
        self.assertEqual(redis.keys(), [])
        self.assertEqual(VehicleLocationHistory.objects.count(), 2)

        empty_redis = fakeredis.FakeStrictRedis()
        with (
            patch("api.views.redis_client", empty_redis),
            patch("vehicles.views.redis_client", empty_redis),
        ):
            response = self.client.get(
                f"/api/vehiclejourneys/{self.journeys[0].id}/details/"
            )
            self.assertEqual(
                decode_time_aware_polyline(response.json()["time_aware_polyline"]),
                [[1.0, 51.0, 1762164000]],
            )

            response = self.client.get(
                f"/api/vehiclejourneys/{self.old_journey.id}/details/"
            )
            self.assertEqual(
                decode_time_aware_polyline(response.json()["time_aware_polyline"]),
                [[1.0, 51.0, 1756720800]],
            )

            response = self.client.get(
                f"{self.vehicle.get_absolute_url()}?date={self.date}"
            )
        self.assertContains(response, f"/map#journey={self.journeys[0].id}")

    def test_merge_vehicles(self):
        other = Vehicle.objects.create(code="2", slug="test-2")
        VehicleLocationHistory.objects.create(
            vehicle=self.vehicle, date=self.date, polylines={"1": "a"}
        )
        VehicleLocationHistory.objects.create(
            vehicle=other, date=self.date, polylines={"2": "b"}
        )
        VehicleLocationHistory.merge(
            {
                (self.vehicle.id, self.date): {"2": "b"},
                (other.id, date(2026, 11, 4)): {},
            }
        )
        self.assertEqual(
            VehicleLocationHistory.objects.get(vehicle=self.vehicle).polylines,
            {"1": "a", "2": "b"},
        )
        self.assertEqual(
            VehicleLocationHistory.get_journey_ids(self.date, [self.vehicle.id]),
            {1, 2},
        )
        self.assertEqual(
            VehicleLocationHistory.get_polyline(
                VehicleJourney(id=2, vehicle=self.vehicle, date=self.date)
            ),
            "b",
        )
