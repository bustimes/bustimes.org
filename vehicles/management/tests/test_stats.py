import fakeredis
import time_machine
from django.test import TestCase, override_settings

from busstops.models import Service
from bustimes.models import Route

from ... import tasks


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://",
            "OPTIONS": {"connection_class": fakeredis.FakeRedisConnection},
        }
    }
)
@time_machine.travel("2023-10-20", tick=False)
class StatsTest(TestCase):
    def test_stats(self):
        response = self.client.get("/stats.json")
        self.assertEqual(response.json(), [])

        tasks.stats()

        response = self.client.get("/stats.json")
        self.assertEqual(
            response.json(),
            [
                {
                    "datetime": "2023-10-19T23:00:00Z",
                    "pending_vehicle_edits": 0,
                    "service_vehicle_journeys": 0,
                    "trip_vehicle_journeys": 0,
                    "vehicle_journeys": 0,
                }
            ],
        )

    def test_timetable_source_stats(self):
        response = self.client.get("/timetable-source-stats.json")
        self.assertEqual(response.json(), [])

        source = tasks.DataSource.objects.create(
            name="Top Mops Limited_Ventnor_31_20231016"
        )
        service = Service.objects.create(source=source, slug="31")
        Route.objects.create(source=source, service=service)

        tasks.timetable_source_stats()

        response = self.client.get("/timetable-source-stats.json")
        self.assertEqual(
            response.json(),
            [
                {
                    "datetime": "2023-10-19T23:00:00Z",
                    "sources": {"Top Mops Limited": 1},
                }
            ],
        )
