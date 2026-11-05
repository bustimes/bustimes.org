from pathlib import Path
from unittest import mock

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import DataSource, Operator, Region, Service, StopPoint
from bustimes.models import Route, StopTime, Trip

from ...models import Vehicle
from ..commands.lothian import Command


class EdinburghImportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="TfE")
        source.save()
        source.refresh_from_db()
        Region.objects.create(name="Scotland", id="S")
        cls.operator_1 = Operator.objects.create(
            name="Lothian Buses", noc="LOTH", region_id="S"
        )
        cls.operator_2 = Operator.objects.create(
            name="Edinburgh Trams", noc="EDTR", region_id="S"
        )
        cls.service = Service.objects.create(line_name="N14", current=True)
        cls.service.operator.add(cls.operator_1)
        service_2 = Service.objects.create(line_name="100", current=True)
        service_2.operator.add(cls.operator_1)
        cls.source = source
        Vehicle.objects.create(operator_id="EDTR", source=source, code="1120")

        stop = StopPoint.objects.create(
            naptan_code="36237267", atco_code="6200204990", active=True
        )
        route = Route.objects.create(service=cls.service, source=source)
        cls.trip = Trip.objects.create(route=route, start="26:45:00", end="27:00:00")
        StopTime.objects.create(trip=cls.trip, stop=stop, departure="26:50:00")

    @time_machine.travel("2025-12-18T02:48:00Z")
    def test_lothian_avl(self):
        redis_client = fakeredis.FakeStrictRedis(version=7)

        with vcr.use_cassette(
            str(
                Path(__file__).resolve().parent
                / "vcr"
                / "edinburgh_vehicle_locations.yaml"
            )
        ) as cassette:
            command = Command()
            command.do_source()

            with mock.patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ):
                with self.assertNumQueries(191):
                    command.update()

                # NaPTAN code -> ATCO code
                self.assertEqual(command.stop_codes["36237267"], "6200204990")

                # the feed has no timestamp of its own,
                # so the newest vehicle location timestamp is used
                self.assertEqual(
                    str(command.status[-1].timestamp), "2025-12-18 02:47:53+00:00"
                )

                cassette.rewind()

                # make it think 1 vehicle has moved
                del command.identifiers["1116"]

                with self.assertNumQueries(1):
                    command.update()

        journey = command.source.vehiclejourney_set.first()

        self.assertEqual("NovWedAL23907804", journey.code)
        self.assertEqual("Surgeons' Hall", journey.destination)
        self.assertEqual(self.service, journey.service)

        self.assertEqual(self.trip, journey.trip)
        self.assertEqual("2025-12-17", str(journey.date))

        self.assertTrue(journey.service.tracking)
        response = self.client.get(journey.service.get_absolute_url())
        self.assertContains(response, '/vehicles">Vehicles</a>')

        call_command("lothian_colours")

        response = self.client.get(self.operator_1.get_absolute_url())
        self.assertContains(response, '/map">Map</a>')
        self.assertContains(response, '/vehicles">Vehicles</a>')

        self.assertContains(response, "background: #0C1436;")  # N14
        self.assertContains(response, "background: #002C4D;")  # 100
