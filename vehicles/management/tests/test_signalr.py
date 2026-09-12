from pathlib import Path
from unittest import mock

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import DataSource, Operator, Region, Service

from ...models import VehicleJourney


class SignalRTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        DataSource.objects.create(name="signalr", url="https://example.com/hub")
        s = DataSource.objects.create(name="IM")
        r = Region.objects.create(name="Isle of Man", id="IM")
        o = Operator.objects.create(noc="BVAN")
        s = Service.objects.create(current=True, region=r, line_name="47B", source=s)
        s.operator.add(o)

    def test(self):
        redis_client = fakeredis.FakeStrictRedis(version=7)

        with (
            vcr.use_cassette(
                str(Path(__file__).resolve().parent / "vcr" / "signalr.yaml")
            ) as cassette,
            mock.patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            time_machine.travel("2025-06-20", tick=False),
        ):
            with (
                self.assertNumQueries(57),
                self.assertRaises(vcr.errors.CannotOverwriteExistingCassetteException),
            ):
                call_command("signalr", "signalr")

            cassette.rewind()

            with (
                self.assertNumQueries(3),
                self.assertRaises(vcr.errors.CannotOverwriteExistingCassetteException),
            ):
                call_command("signalr", "signalr")

        self.assertEqual(VehicleJourney.objects.count(), 5)
        self.assertEqual(
            VehicleJourney.objects.filter(service__isnull=False).count(), 1
        )
