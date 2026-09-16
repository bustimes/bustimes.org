from unittest.mock import patch

import fakeredis
import time_machine
from django.test import TestCase, override_settings

from busstops.models import Service
from bustimes.models import Route

from ... import tasks
from ...utils import count_locations


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
                    "datetime": "2023-10-19T23:00:00+00:00",
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
                    "datetime": "2023-10-19T23:00:00+00:00",
                    "sources": {"Top Mops Limited": 1},
                }
            ],
        )


class LocationStatsTest(TestCase):
    def test_no_redis(self):
        response = self.client.get("/location-stats.json")
        self.assertEqual(response.json(), [])

    def test_location_stats(self):
        redis_client = fakeredis.FakeStrictRedis(version=7)

        with patch("vehicles.utils.redis_client", redis_client):
            with time_machine.travel("2023-10-20 12:34:30+00:00", tick=False):
                pipeline = redis_client.pipeline(transaction=False)
                count_locations(pipeline, "Bus Open Data", 120)
                count_locations(pipeline, "Stagecoach", 60)
                pipeline.execute()

            # the minute in progress is left out, so travel to the next one
            with time_machine.travel("2023-10-20 12:36:00+00:00", tick=False):
                response = self.client.get("/location-stats.json?hours=1")
                stats = response.json()

                # an hour of one minute buckets
                self.assertEqual(len(stats), 60)
                self.assertEqual(stats[0]["datetime"], "2023-10-20T11:36:00+00:00")
                self.assertEqual(stats[-1]["datetime"], "2023-10-20T12:35:00+00:00")
                self.assertEqual(stats[-1]["sources"], {})

                self.assertEqual(
                    stats[-2],
                    {
                        "datetime": "2023-10-20T12:34:00+00:00",
                        "sources": {"Bus Open Data": 2.0, "Stagecoach": 1.0},
                    },
                )

                # a day of two minute buckets
                response = self.client.get("/location-stats.json")
                stats = response.json()
                self.assertEqual(len(stats), 720)
                self.assertEqual(
                    stats[-1],
                    {
                        "datetime": "2023-10-20T12:34:00+00:00",
                        "sources": {"Bus Open Data": 1.0, "Stagecoach": 0.5},
                    },
                )
