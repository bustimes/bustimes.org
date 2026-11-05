from pathlib import Path
from unittest.mock import patch

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import Operator, Region, StopPoint

from ...models import VehicleJourney
from .test_bod_avl import CapturingChannelLayer, distribute, patch_redis_client

VCR_DIR = Path(__file__).resolve().parent / "vcr"


class StopLoop(Exception):
    """raised to break out of the command's polling loop in tests"""


class JerseyImportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Region.objects.create(id="JE")
        Operator.objects.create(noc="libertybus", region_id="JE")

    def test_stops(self):
        with self.assertNumQueries(5):
            call_command("jersey_stops")
        with self.assertNumQueries(2):
            call_command("jersey_stops")
        self.assertEqual(StopPoint.objects.count(), 763)

    def test_routes(self):
        with vcr.use_cassette(VCR_DIR / "jersey_routes.yaml"):
            call_command("jersey_routes")

        response = self.client.get("/regions/JE")
        self.assertContains(
            response,
            """{
    background: #96DCFD;
    border-color: #000;
    color: #000;
}""",
        )

    @vcr.use_cassette(
        str(VCR_DIR / "import_live_jersey.yaml"),
        decode_compressed_response=True,
    )
    @time_machine.travel("2025-10-15T10:20:00Z")
    def test_handle(self):
        server = fakeredis.FakeServer()
        async_redis_client = fakeredis.FakeAsyncRedis(server=server, version=7)
        redis_client = fakeredis.FakeStrictRedis(server=server, version=7)
        channel_layer = CapturingChannelLayer()

        with (
            patch_redis_client(redis_client),
            patch(
                "vehicles.management.import_live_vehicles.sleep",
                side_effect=[None, None, StopLoop],
            ),
            self.assertRaises(StopLoop),
            patch(
                "vehicles.management.import_live_vehicles.get_channel_layer",
                return_value=channel_layer,
            ),
        ):
            call_command("import_live_jersey")

        with (
            patch("vehicles.views.redis_client", redis_client),
            patch("api.views.redis_client", redis_client),
        ):
            positions = self.client.get("/vehicles.json").json()
            self.assertEqual(positions[0]["datetime"], "2025-10-15T11:16:36+01:00")
            self.assertEqual(positions[1]["datetime"], "2025-10-15T11:16:51+01:00")
            self.assertEqual(positions[2]["datetime"], "2025-10-15T11:13:42+01:00")

            distribute(channel_layer, async_redis_client)

            journey = VehicleJourney.objects.get(route_name="12")
            response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details/")
            self.assertEqual(
                "~mlL}fgkHe|x||gB|m@tXaJ", response.json()["time_aware_polyline"]
            )

            journey = VehicleJourney.objects.get(route_name="12A")
            response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details/")
            self.assertEqual(
                "r|`LwahkHs|x||gB`mBfKqJ", response.json()["time_aware_polyline"]
            )

            journey = VehicleJourney.objects.get(route_name="15")
            response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details/")
            self.assertEqual("ji}KotfkHk|x||gB", response.json()["time_aware_polyline"])
