from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import fakeredis
import vcr
from django.test import TestCase

from busstops.models import DataSource, Operator, Route, Service

from ..commands.import_first import Command


class FirstTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = DataSource.objects.create(
            name="First",
            url="https://example.com/socket_information",
            datetime=datetime(2026, 9, 21, 7, 2, 50, tzinfo=UTC),
        )
        o = Operator.objects.create(noc="BDGR", name="Badgerline")
        s = Service.objects.create(current=True, line_name="B")
        Route.objects.create(service=s, line_name="B", source=cls.source)
        s.operator.add(o)

    def test_sock_it(self):
        cmd = Command()
        cmd.do_source()

        with (
            vcr.use_cassette(
                str(Path(__file__).resolve().parent / "vcr" / "first.yaml")
            ),
            self.assertRaises(KeyError),
        ):
            cmd.get_items()

    def test_handle_data(self):
        cmd = Command()
        cmd.source = self.source

        redis_client = fakeredis.FakeStrictRedis(version=7)

        with mock.patch(
            "vehicles.management.import_live_vehicles.redis_client", redis_client
        ):
            location, vehicle = cmd.handle_item(
                {
                    "dir": "outbound",
                    "line": "B",
                    "status": {
                        "bearing": 61,
                        "location": {
                            "type": "Point",
                            "coordinates": [1.267833, 52.614746],
                        },
                        "vehicle_id": "BDGR-outbound-2025-09-21-0615-11111-B",
                        "recorded_at_time": "2025-09-21T07:02:17+01:00",
                    },
                    "stops": [
                        {
                            "date": "2025-09-21",
                            "time": "06:15",
                            "locality": "Woody Knoll",
                            "atcocode": "",
                        }
                    ],
                    "operator": "BDGR",
                    "line_name": "B",
                    "description": "",
                    "operator_name": "Badgerline",
                }
            )

        self.assertEqual(str(vehicle), "11111")
        self.assertEqual(location.journey.route_name, "B")
        self.assertEqual(location.journey.code, "0615")

    def test_unchanged_location(self):
        cmd = Command()
        cmd.source = self.source

        def item(recorded_at_time, code="0615"):
            return {
                "dir": "outbound",
                "line": "B",
                "status": {
                    "bearing": 61,
                    "location": {
                        "type": "Point",
                        "coordinates": [1.267833, 52.614746],
                    },
                    "vehicle_id": f"BDGR-outbound-2025-09-21-{code}-11111-B",
                    "recorded_at_time": recorded_at_time,
                },
                "stops": [
                    {
                        "date": "2025-09-21",
                        "time": f"{code[:2]}:{code[2:]}",
                        "locality": "Woody Knoll",
                        "atcocode": "",
                    }
                ],
                "operator": "BDGR",
                "line_name": "B",
                "description": "",
                "operator_name": "Badgerline",
            }

        redis_client = fakeredis.FakeStrictRedis(version=7)

        with mock.patch(
            "vehicles.management.import_live_vehicles.redis_client", redis_client
        ):
            self.assertIsNotNone(cmd.handle_item(item("2026-09-21T07:00:17Z")))
            cmd.save()

            # same place, same journey - nothing new
            self.assertIsNone(cmd.handle_item(item("2026-09-21T07:01:17Z")))

            # same place, but a different journey
            location, _ = cmd.handle_item(item("2026-09-21T07:02:17Z", "0715"))
            self.assertEqual(location.journey.code, "0715")
            self.assertEqual(
                location.datetime, datetime(2026, 9, 21, 7, 0, 17, tzinfo=UTC)
            )
