from datetime import timedelta
from http import HTTPStatus
from unittest.mock import patch
from zoneinfo import ZoneInfo

import fakeredis
import time_machine
import vcr
from django.test import TestCase, override_settings

from busstops.models import DataSource, Operator, Service, StopPoint, StopUsage
from bustimes.models import Calendar, Route, StopTime, Trip

from . import gtfsr


class GTFSRTTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="Realtime Transport Operators")

        StopPoint.objects.bulk_create(
            [
                StopPoint(atco_code="8220DB000405", active=True),
                StopPoint(atco_code="8220DB000409", active=True),
                StopPoint(atco_code="8220DB000410", active=True),
                StopPoint(atco_code="8220DB000412", active=True),
                StopPoint(atco_code="8220DB000413", active=True),
                StopPoint(atco_code="8220DB000414", active=True),
                StopPoint(atco_code="8220DB000415", active=True),
                StopPoint(atco_code="8220DB000416", active=True),
                StopPoint(atco_code="8220DB000418", active=True),
                StopPoint(atco_code="8220DB000420", active=True),
                StopPoint(atco_code="8220DB000421", active=True),
                StopPoint(atco_code="8250DB000427", active=True),
                StopPoint(atco_code="8250DB000429", active=True),
            ]
        )

        operator = Operator.objects.create(
            noc="dub", name="Dublin Bus / Bus Átha Cliath"
        )
        service = Service.objects.create(service_code="7", line_name="7")
        service.operator.add(operator)

        route = Route.objects.create(service=service, source=source, code="7")

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

        cls.trip = Trip.objects.create(
            route=route,
            ticket_machine_code="4323_12791",
            start="13:45:00",
            end="15:08:00",
            calendar=calendar,
        )
        cls.cancellable_trip = Trip.objects.create(
            route=route,
            ticket_machine_code="4323_10231",
            start="13:00:00",
            end="07:49:00",
            calendar=calendar,
        )
        StopTime.objects.bulk_create(
            [
                StopTime(trip=cls.trip, sequence=i, stop_id=stop_id, departure=time)
                for i, stop_id, time in (
                    (4, "8220DB000405", "06:45:00"),
                    (6, "8220DB000409", "06:45:00"),
                    (7, "8220DB000410", "06:45:00"),
                    (9, "8220DB000412", "06:45:00"),
                    (10, "8220DB000413", "06:45:00"),
                    (11, "8220DB000414", "06:45:00"),
                    (12, "8250DB000429", "06:45:00"),
                    (13, "8220DB000416", "06:45:00"),
                    (15, "8220DB000418", "06:45:00"),
                    (17, "8220DB000420", "06:45:00"),
                    (18, "8220DB000421", "06:45:00"),
                    (24, "8250DB000427", "06:45:00"),
                )
            ]
            + [
                StopTime(
                    trip=cls.cancellable_trip,
                    sequence=1,
                    stop_id="8250DB000429",
                    departure="06:45:00",
                ),
            ],
        )

        StopUsage.objects.create(service=service, stop_id="8250DB000429", order=0)

    @patch(
        "departures.gtfsr.redis_client",
        fakeredis.FakeStrictRedis(),
    )
    def test_nta_ie(self):
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
            vcr.use_cassette("fixtures/vcr/nta_ie_trip_updates.yaml"),
        ):
            # trip with some delays
            with self.assertNumQueries(6):
                response = self.client.get(self.trip.get_absolute_url())
            self.assertContains(response, '"06:46"')

            response = self.client.get("/trip_updates")
            self.assertContains(response, "3051 trip_updates")
            self.assertContains(response, "2 matching trips")

            response = self.client.get("/trip_updates/ntaie.json")
            self.assertEqual(response.headers["Content-Type"], "application/json")

            response = self.client.get("/trip_updates/foo.json")
            self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

            response = self.client.get("/stops/8250DB000429?date=2022-05-04&time=05:00")
            self.assertContains(response, "Ex&shy;pected")
            self.assertContains(response, "Sched&shy;uled")
            self.assertContains(response, "06:46")
            self.assertContains(
                response, "<s>06:45</s>", html=True
            )  # cancelled - struck through

            # cancelled trip:
            response = self.client.get(self.cancellable_trip.get_absolute_url())
            self.assertTrue(response.context["stops_json"])

    @time_machine.travel("2022-05-04T05:50:00Z")  # 06:50 BST
    def test_late_departure(self):
        trip_updates = {
            "4323_12791": {
                "trip": {"tripId": "4323_12791"},
                "stopTimeUpdate": [{"stopSequence": 12, "departure": {"delay": 600}}],
            }
        }
        with patch("departures.gtfsr.get_trip_updates", return_value=trip_updates):
            response = self.client.get("/stops/8250DB000429")
        # scheduled 06:45, but running 10 minutes late
        self.assertContains(response, self.trip.get_absolute_url())
        self.assertContains(response, "06:55")
        # scheduled 06:45, no real time information
        self.assertNotContains(response, self.cancellable_trip.get_absolute_url())

        trip_updates["4323_12791"]["stopTimeUpdate"][0]["departure"]["delay"] = 60
        with patch("departures.gtfsr.get_trip_updates", return_value=trip_updates):
            response = self.client.get("/stops/8250DB000429")
        # departed at 06:46
        self.assertNotContains(response, self.trip.get_absolute_url())

    def test_no_trip_updates(self):
        self.assertIsNone(gtfsr.update_stop_departures((), {}))

    def test_update_departure_without_stop_time_updates(self):
        departure = {"stop_time": StopTime(sequence=1)}
        gtfsr.update_departure(departure, {"trip": {}})
        self.assertEqual(departure, {"stop_time": departure["stop_time"]})

    def test_apply_trip_update_skipped(self):
        stops = [
            StopTime(sequence=1, departure=timedelta(hours=8)),
            StopTime(sequence=2, departure=timedelta(hours=8, minutes=5)),
        ]
        gtfsr.apply_trip_update(
            stops,
            {
                "stopTimeUpdate": [
                    {"stopSequence": 1, "departure": {"delay": 60}},
                    {"stopSequence": 2, "scheduleRelationship": "SKIPPED"},
                ]
            },
        )
        self.assertFalse(hasattr(stops[0], "call_condition"))
        self.assertEqual(stops[1].call_condition, "notStopping")

    def test_get_expected_time(self):
        update = {
            "arrival": {"time": "1725349158", "uncertainty": 0},
            "departure": {"delay": 560},
            "scheduleRelationship": "SCHEDULED",
            "stopId": "8250DB000427",
            "stopSequence": 24,
        }

        time = timedelta(hours=8, minutes=20)

        self.assertEqual(
            str(gtfsr.get_expected_time(time, update, "arrival")),
            "2024-09-03 08:39:18+01:00",
        )

        self.assertEqual(
            str(
                gtfsr.get_expected_time(
                    time, update, "arrival", ZoneInfo("Europe/Paris")
                )
            ),
            "2024-09-03 09:39:18+02:00",
        )

        time += timedelta(minutes=10)
        self.assertEqual(
            str(gtfsr.get_expected_time(time, update, "departure")),
            "08:39",
        )
