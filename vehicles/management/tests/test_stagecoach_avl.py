from pathlib import Path
from unittest.mock import patch

import fakeredis
import time_machine
import vcr
from django.test import TestCase

from busstops.models import DataSource, Operator, OperatorGroup, Region, Service

from ...models import VehicleJourney
from ..commands.import_stagecoach_avl import Command


@time_machine.travel("2019-11-17T14:00:01.000Z")
class StagecoachTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = DataSource.objects.create(
            name="Stagecoach",
            url="https://api.stagecoach-technology.net/vehicle-tracking/v1/vehicles?services=:*:::",
        )

        group = OperatorGroup.objects.create(name="Stagecoach", slug="stagecoach")
        region = Region.objects.create(pk="SE")
        operator = Operator.objects.create(
            pk="SCOX", name="Oxford", vehicle_mode="bus", region=region, group=group
        )
        service = Service.objects.create(
            line_name="Oxford Tube",
            geometry="MULTILINESTRING((-0.1475818977 51.4928233539,-0.1460401487 51.496737716))",
        )
        service.operator.add(operator)

    @patch(
        "vehicles.management.import_live_vehicles.redis_client",
        fakeredis.FakeStrictRedis(version=7),
    )
    def test_handle(self):
        """Stagecoach AVL"""

        command = Command()
        command.do_source()
        command.operator_codes = ["SDVN"]

        with vcr.use_cassette(
            str(Path(__file__).resolve().parent / "vcr" / "stagecoach_vehicles.yaml")
        ) as cassette:
            with self.assertNumQueries(87):
                command.update()

            cassette.rewind()
            # make it think 2 vehicles have moved
            del command.identifiers["SCCM:CA:19617"]
            del command.identifiers["SCOX:SOX:50275"]
            with self.assertNumQueries(2):
                command.update()

        self.assertEqual(
            command.operators,
            {
                "SCOX": Operator(noc="SCOX"),
            },
        )
        self.assertEqual(VehicleJourney.objects.count(), 8)

    def test_no_gps(self):
        """a vehicle with no GPS should still be recorded as running,
        but not added to the map"""

        command = Command()
        command.do_source()

        item = {
            "fn": "50275",
            "ut": "1573999200000",
            "oc": "SCOX",
            "so": "SOX",
            "sn": "Oxford Tube",
            "dn": "INBOUND",
            "dd": "London",
            "la": None,
            "lo": None,
            "hg": None,
        }

        redis = fakeredis.FakeStrictRedis(version=7)
        with patch("vehicles.management.import_live_vehicles.redis_client", redis):
            location, vehicle = command.handle_item(item)
            self.assertIsNone(location.latlong)

            # a vehicle with GPS, for comparison
            command.handle_item(
                item | {"fn": "50276", "la": "51.4928233539", "lo": "-0.1475818977"}
            )

            command.save()

        self.assertEqual(redis.zcard("vehicle_location_locations"), 1)

        journey = vehicle.vehiclejourney_set.get()
        self.assertEqual(journey.route_name, "Oxford Tube")
        self.assertEqual(journey.destination, "London")
        self.assertEqual(journey.service.line_name, "Oxford Tube")
