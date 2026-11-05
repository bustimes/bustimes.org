import dataclasses
import datetime
import json
from pathlib import Path
from unittest.mock import Mock, patch

import fakeredis
import time_machine
from django.core.management import call_command
from django.test import TestCase, override_settings
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import Service
from disruptions import gtfsr_alerts
from disruptions.models import Situation
from vehicles.management.commands import import_gtfsr_feed
from vehicles.models import Vehicle, VehicleJourney

from ...gtfs_feeds import FEEDS
from ...models import Trip
from ..commands.import_gtfs_feed import TOWN_IN_STOP_NAME

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# test data in `rtcsnv.zip` dir (not a real zipfile) in fixtures dir
@override_settings(DATA_DIR=FIXTURES_DIR)
class NevadaTest(TestCase):
    def test_not_modified(self):
        with (
            patch(
                "bustimes.management.commands.import_gtfs_feed.download_if_modified",
                return_value=(False, None),
            ),
            self.assertNumQueries(4),
        ):
            call_command("import_gtfs_feed", "rtcsnv")

    @time_machine.travel("2026-08-04", tick=False)
    def test_import_gtfs_feed(self):
        # a vehicle with an implausibly future timestamp
        self.enterContext(
            self.assertLogs("vehicles.management.import_live_vehicles", "WARNING")
        )

        with patch(
            "bustimes.management.commands.import_gtfs_feed.download_if_modified",
            return_value=(
                True,
                datetime.datetime(2024, 6, 18, 10, 0, 0, tzinfo=datetime.UTC),
            ),
        ):
            call_command("import_gtfs_feed", "rtcsnv")

            self.assertEqual(Service.objects.all().count(), 2)

            # re-importing shouldn't re-create services

            call_command("import_gtfs_feed", "rtcsnv")
            self.assertEqual(Service.objects.all().count(), 2)

        response = self.client.get("/operators/rtcsnv")
        self.assertContains(response, """<a href="/services/deuce-the-deucestrip">""")

        response = self.client.get("/services/deuce-the-deucestrip")
        self.assertContains(response, "/stops/rtcsnv-6038")

        response = self.client.get("/stops/rtcsnv-6038")
        # Pacific Time
        self.assertContains(
            response, """<input type="time" name="time" value="16:00">"""
        )

        # test GTFS-RT
        feed = gtfs_realtime_pb2.FeedMessage()
        json_format.ParseDict(
            {
                "header": {"gtfsRealtimeVersion": "2.0", "timestamp": "1785751500"},
                "entity": [
                    {
                        "id": "20450",
                        "vehicle": {
                            "trip": {
                                "tripId": "358024702",
                                "routeId": "4740",
                                "startDate": "20260803",
                                "startTime": "03:00:00",
                                "scheduleRelationship": "SCHEDULED",
                            },
                            "vehicle": {"id": "20450", "label": "20450"},
                            "occupancyStatus": "NO_DATA_AVAILABLE",
                            "position": {
                                "latitude": 36.10913,
                                "longitude": -115.17262,
                            },
                            # no timestamp - header timestamp 03:05 Pacific Time used
                        },
                    },
                    {
                        "id": "99999",
                        "vehicle": {
                            "trip": {
                                "tripId": "358024702",
                                "startDate": "20260803",
                            },
                            "vehicle": {"id": "99999", "label": "99999"},
                            # no position
                        },
                    },
                    {
                        "id": "26627",
                        "vehicle": {
                            "trip": {
                                "tripId": "358022630",
                                "routeId": "4725",
                                "startDate": "20260803",
                                "startTime": "26:25:00",
                                "directionId": 0,
                                "scheduleRelationship": "SCHEDULED",
                            },
                            "stopId": "2770",
                            "vehicle": {
                                "id": "26627",
                                "label": "26627",
                                "wheelchairAccessible": "WHEELCHAIR_INACCESSIBLE",
                            },
                            "position": {
                                "latitude": 36.18293,
                                "longitude": -115.31084,
                            },
                            "timestamp": "1785835126",
                            "currentStatus": "STOPPED_AT",
                            "occupancyStatus": "MANY_SEATS_AVAILABLE",
                            "currentStopSequence": 1,
                            "occupancyPercentage": 2,
                        },
                    },
                ],
            },
            feed,
        )
        response = Mock(status_code=200, content=feed.SerializeToString(), headers={})

        command = import_gtfsr_feed.Command()
        command.set_feed("rtcsnv")
        command.do_source()

        trip_updates = gtfs_realtime_pb2.FeedMessage()
        json_format.ParseDict(
            {
                "header": {"gtfsRealtimeVersion": "2.0", "timestamp": "1785751500"},
                "entity": [
                    {
                        "id": "358024702_1",
                        "tripUpdate": {
                            "trip": {"tripId": "358024702", "routeId": "4740"},
                            "stopTimeUpdate": [
                                {
                                    "stopSequence": 2,
                                    "arrival": {"time": "1785751620"},
                                    "stopId": "6038",
                                }
                            ],
                        },
                    }
                ],
            },
            trip_updates,
        )
        trip_updates_response = Mock(
            status_code=200, content=trip_updates.SerializeToString(), headers={}
        )

        redis_client = fakeredis.FakeStrictRedis()
        with (
            patch.object(command.session, "get", return_value=response),
            patch(
                "vehicles.management.commands.import_gtfsr_feed.requests.get",
                return_value=trip_updates_response,
            ),
            patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            patch("vehicles.management.commands.import_gtfsr_feed.cache") as cache,
        ):
            command.update()

        cache.set.assert_called_once()
        key, trip_updates, _ = cache.set.call_args.args
        self.assertEqual(key, "rtcsnv_trip_updates")
        self.assertEqual(
            trip_updates["358024702"]["stopTimeUpdate"][0]["stopId"], "6038"
        )

        trip = Trip.objects.get(vehicle_journey_code="358024702")
        journey = VehicleJourney.objects.get(code="358024702")
        self.assertEqual(journey.trip, trip)
        self.assertEqual(journey.service, trip.route.service)
        self.assertEqual(journey.route_name, "DEUCE")
        self.assertEqual(journey.destination, "Deuce on The Strip Northbound")
        self.assertEqual(str(journey.datetime), "2026-08-03 10:00:00+00:00")
        self.assertEqual(str(journey.date), "2026-08-03")

        vehicle = Vehicle.objects.get(code="26627")
        self.assertEqual(vehicle.fleet_code, "26627")
        item = json.loads(redis_client.get(f"vehicle{vehicle.id}"))
        self.assertEqual(item["stops"], ["rtcsnv-2770", None])
        self.assertEqual(item["wheelchair"], "not accessible")
        self.assertEqual(item["seats"], "Many seats available")

        vehicle = Vehicle.objects.get(code="20450")
        item = json.loads(redis_client.get(f"vehicle{vehicle.id}"))
        self.assertNotIn("seats", item)
        self.assertNotIn("wheelchair", item)

        self.assertFalse(Vehicle.objects.filter(code="99999").exists())

        journey = VehicleJourney.objects.get(code="358022630")
        # start_time "26:25:00" and date 2026-08-03
        # means 02:25 Pacific Time the next day
        self.assertEqual(str(journey.date), "2026-08-03")
        self.assertEqual(str(journey.datetime), "2026-08-04 09:25:00+00:00")

        # GTFS-RT alerts
        alerts = gtfs_realtime_pb2.FeedMessage()
        json_format.ParseDict(
            {
                "header": {"gtfsRealtimeVersion": "2.0", "timestamp": "1785751500"},
                "entity": [
                    {
                        "id": "1105",
                        "alert": {
                            "activePeriod": [{"start": "1785700000"}],
                            "informedEntity": [{"routeId": "4740"}],
                            "cause": "CONSTRUCTION",
                            "effect": "DETOUR",
                            "url": {
                                "translation": [
                                    {
                                        "text": "https://example.com/pl",
                                        "language": "pl",
                                    },
                                    {
                                        "text": "https://example.com/en",
                                        "language": "en",
                                    },
                                ]
                            },
                            "headerText": {
                                "translation": [
                                    {"text": "Zmiana trasy", "language": "pl"},
                                    {"text": "Route changed", "language": "en"},
                                ]
                            },
                            "descriptionText": {
                                "translation": [{"text": "Objazd", "language": "pl"}]
                            },
                        },
                    },
                    {
                        "id": "abc",
                        "alert": {
                            "activePeriod": [
                                {"start": "1785700000", "end": "1785900000"}
                            ],
                            "informedEntity": [
                                {
                                    "trip": {
                                        "tripId": "358024702",
                                        "startDate": "20260803",
                                    }
                                }
                            ],
                            "headerText": {
                                "translation": [
                                    {"text": "Running late", "language": "en"}
                                ]
                            },
                        },
                    },
                    {
                        "id": "xyz",
                        "alert": {
                            "informedEntity": [
                                {"stopId": "6038", "trip": {"tripId": "358024702"}}
                            ],
                            "effect": "STOP_MOVED",
                            "headerText": {
                                "translation": [
                                    {"text": "Stop moved", "language": "en"}
                                ]
                            },
                        },
                    },
                    {
                        "id": "def",
                        "alert": {
                            "informedEntity": [{"stopId": "6038"}],
                            "headerText": {
                                "translation": [
                                    {"text": "Pre-booking", "language": "en"}
                                ]
                            },
                        },
                    },
                ],
            },
            alerts,
        )
        alerts_response = Mock(
            status_code=200, content=alerts.SerializeToString(), headers={}
        )
        feed = dataclasses.replace(
            FEEDS["rtcsnv"],
            alert_ids_change=True,
            skip_alert_headers=("Pre-booking",),
        )
        with (
            patch.dict(FEEDS, rtcsnv=feed),
            patch(
                "disruptions.gtfsr_alerts.requests.get", return_value=alerts_response
            ),
        ):
            gtfsr_alerts.import_alerts("rtcsnv")

            situations = Situation.objects.order_by("summary")
            self.assertEqual(len(situations), 3)
            running_late, route_changed, stop_moved = situations
            self.assertEqual(
                stop_moved.text, "Journeys towards Deuce on The Strip Northbound"
            )
            self.assertFalse(stop_moved.affectedjourney_set.exists())
            self.assertEqual(
                running_late.summary, "03:00 to Deuce on The Strip Northbound"
            )
            self.assertEqual(running_late.text, "Running late")
            self.assertEqual(route_changed.summary, "Route changed")
            self.assertEqual(route_changed.text, "Objazd")
            self.assertEqual(route_changed.reason, "construction")
            self.assertEqual(route_changed.link_set.get().url, "https://example.com/en")
            self.assertIsNone(route_changed.publication_window.upper)
            self.assertEqual(
                route_changed.consequence_set.get().services.get().line_name, "DEUCE"
            )
            journey = running_late.affectedjourney_set.get()
            self.assertEqual(journey.trip, trip)
            self.assertEqual(str(journey.date), "2026-08-03")

            response = self.client.get("/services/deuce-the-deucestrip")
            self.assertContains(response, "Route changed")
            # (publication window is compared to the database's real Now())
            self.assertEqual(
                running_late.consequence_set.get().services.get().line_name, "DEUCE"
            )

            # entity ids change, end time changes - update the same situation
            alerts.entity[0].id = "ghi"
            alerts.entity[1].id = "jkl"
            alerts.entity[1].alert.active_period[0].end = 1786000000
            alerts_response.content = alerts.SerializeToString()
            gtfsr_alerts.import_alerts("rtcsnv")
            self.assertEqual(Situation.objects.filter(current=True).count(), 3)
            running_late.refresh_from_db()
            self.assertEqual(
                running_late.publication_window.upper.timestamp(), 1786000000
            )

            # end before start
            alerts.entity[1].alert.active_period[0].end = 1785600000
            alerts_response.content = alerts.SerializeToString()
            gtfsr_alerts.import_alerts("rtcsnv")
            running_late.refresh_from_db()
            self.assertEqual(
                running_late.publication_window.upper,
                running_late.publication_window.lower,
            )

            # alerts gone
            alerts_response.content = gtfs_realtime_pb2.FeedMessage(
                header=alerts.header
            ).SerializeToString()
            gtfsr_alerts.import_alerts("rtcsnv")
            self.assertFalse(Situation.objects.exists())


class TownInStopNameTest(TestCase):
    def test_town_in_stop_name(self):
        for name, groups in (
            ("SAINT-PIERRE-EGLISE Collège", ("SAINT-PIERRE-EGLISE", "Collège")),
            ("LE VAST Place", ("LE VAST", "Place")),
            ("MONTEBOURG Z.A. du Haut Gelé", ("MONTEBOURG", "Z.A. du Haut Gelé")),
        ):
            self.assertEqual(TOWN_IN_STOP_NAME.fullmatch(name).groups(), groups)
        for name in ("AGORA", "CHERBOURG-GARE", "LES AIGUILLONS"):
            self.assertIsNone(TOWN_IN_STOP_NAME.fullmatch(name))
