from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import fakeredis
import requests
from django.core.cache import cache
from django.test import TestCase, override_settings
from vcr import use_cassette

from busstops.models import DataSource, StopPoint
from bustimes.models import Route, StopTime, Trip
from vehicles.models import Vehicle, VehicleJourney

from .views import VehicleJourneyViewSet


class ApiTest(TestCase):
    def test_api(self):
        with self.assertNumQueries(1):
            response = self.client.get(
                "/api/vehicles/",
            )

        # extra queries from livery, operator and type filter widgets
        with self.assertNumQueries(1):
            response = self.client.get(
                "/api/vehicles/", headers={"accept": "text/html"}
            )

        self.assertContains(
            response, "<title>Vehicle List – API – bustimes.org</title>"
        )
        self.assertContains(
            response, "<a class='navbar-brand' href='/'>bustimes.org</a>"
        )

    @override_settings(
        TFL={},
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        },
    )
    def test_tfl_arrivals(self):
        source = DataSource.objects.create(name="L")
        StopPoint.objects.create(
            atco_code="490010552N", common_name="Old Ford Road", active=True
        )
        StopPoint.objects.create(
            atco_code="490004215M",
            common_name="Bow Church",
            latlong="POINT(-0.0204 51.5287)",
            active=True,
        )
        route = Route.objects.create(source=source, line_name="8")
        trip = Trip.objects.create(route=route, start="18:00:00", end="19:00:00")
        StopTime.objects.create(
            trip=trip, stop_id="490010552N", arrival="18:55:00", departure="18:55:00"
        )
        vehicle = Vehicle.objects.create(
            code="LTZ1243",
            reg="LTZ1243",
            latest_journey_data={
                "MonitoredVehicleJourney": {
                    "OperatorRef": "TFLO",
                    "VehicleRef": "LTZ1243",
                }
            },
        )
        old_journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            trip=trip,
            datetime="2021-03-17T17:00:00Z",
            date="2021-03-17",
            source=source,
        )
        journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            trip=trip,
            datetime="2021-03-17T18:00:00Z",
            date="2021-03-17",
            source=source,
        )
        vehicle.latest_journey = journey
        vehicle.save(update_fields=["latest_journey"])

        fake_redis = fakeredis.FakeStrictRedis()
        with (
            patch("vehicles.views.redis_client", fake_redis),
            patch("api.views.redis_client", fake_redis),
            use_cassette(
                str(
                    Path(__file__).parent.parent
                    / "bustimes"
                    / "vcr"
                    / "tfl_vehicle.yaml"
                ),
                decode_compressed_response=True,
            ) as cassette,
        ):
            # not the vehicle's current journey - no live predictions
            response = self.client.get(
                f"/api/vehiclejourneys/{old_journey.id}/details/"
            )
            times = response.json()["trip"]["times"]
            self.assertNotIn("expected_arrival_time", times[0])
            self.assertEqual(cassette.play_count, 0)

            response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details/")
            times = response.json()["trip"]["times"]
            # predictions for stops not in the trip are inserted, in order
            self.assertEqual(len(times), 12)
            self.assertEqual(times[0]["stop"]["atco_code"], "490004215M")
            self.assertEqual(times[0]["stop"]["name"], "Bow Church")
            self.assertEqual(times[0]["stop"]["location"], [-0.0204, 51.5287])
            self.assertEqual(times[1]["stop"]["name"], "Roman Road Market")
            self.assertIsNone(times[1]["stop"]["location"])
            self.assertEqual(times[2]["stop"]["atco_code"], "490010552N")
            self.assertEqual(
                times[2]["aimed_arrival_time"], "2021-03-17T18:55:00+00:00"
            )
            self.assertEqual(
                times[2]["expected_arrival_time"], "2021-03-17T18:55:42+00:00"
            )
            expected = [time["expected_arrival_time"] for time in times]
            self.assertEqual(expected, sorted(expected))
            self.assertEqual(cassette.play_count, 1)

        cache.clear()
        with (
            patch("vehicles.views.redis_client", fake_redis),
            patch("api.views.redis_client", fake_redis),
            patch(
                "api.views.requests.get", side_effect=requests.ConnectionError
            ) as mocked_get,
            self.assertLogs("api.views", "WARNING"),
        ):
            response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details/")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                "expected_arrival_time", response.json()["trip"]["times"][0]
            )
            # failure is cached
            self.client.get(f"/api/vehiclejourneys/{journey.id}/details/")
            mocked_get.assert_called_once()

    @override_settings(
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        },
    )
    def test_tfl_arrivals_none_matched(self):
        # trip from SIRI - just origin and destination, neither predicted
        origin = StopPoint(atco_code="490003637N", common_name="Barnet Hospital")
        dest = StopPoint(atco_code="490008296M", common_name="Nags Head")
        trip = Trip()
        trip.stops = [
            StopTime(stop=origin, departure=timedelta(hours=18, minutes=23)),
            StopTime(stop=dest),
        ]
        journey = VehicleJourney(trip=trip, date=date(2026, 10, 8))
        cache.set(
            "TflVehicle:LV74TJU",
            [
                {
                    "naptanId": "490009082S",
                    "stationName": "Leisure Way",
                    "expectedArrival": "2026-10-08T17:55:20Z",
                },
                {
                    "naptanId": "490015327S",
                    "stationName": "Granville Road",
                    "expectedArrival": "2026-10-08T17:54:17Z",
                },
            ],
        )

        VehicleJourneyViewSet.tfl_arrivals(journey, "LV74TJU")

        self.assertEqual(
            [stop_time.stop.common_name for stop_time in trip.stops],
            ["Barnet Hospital", "Granville Road", "Leisure Way", "Nags Head"],
        )
