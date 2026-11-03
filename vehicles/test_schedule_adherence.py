import datetime
import json
from unittest.mock import patch

import fakeredis
import time_machine
from django.contrib.gis.geos import Point
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from busstops.models import DataSource, Service, StopPoint, StopUsage
from bustimes.models import Calendar, Route, RouteLink, StopTime, Trip

from . import rtpi
from .management.import_live_vehicles import ImportLiveVehiclesCommand
from .models import Vehicle, VehicleJourney, VehicleLocation


class ScheduleAdherenceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        StopPoint.objects.bulk_create(
            [
                StopPoint(
                    atco_code="210021503158",
                    common_name="St Peter's Street",
                    latlong="POINT(-0.336513 51.754215)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021506690",
                    common_name="Alban City School",
                    latlong="POINT(-0.334660 51.754162)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021506795",
                    common_name="St Albans City Railway Station",
                    latlong="POINT(-0.326993 51.750312)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021506765",
                    common_name="Granville Road",
                    latlong="POINT(-0.324521 51.751734)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021502200",
                    common_name="Churchill Road",
                    latlong="POINT(-0.317141 51.758903)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021502180",
                    common_name="Homewood Road",
                    latlong="POINT(-0.311548 51.759299)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021502160",
                    common_name="Beechwood Ave",
                    latlong="POINT(-0.307895 51.759714)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021509645",
                    common_name="Jersey Lane",
                    latlong="POINT(-0.307702 51.761797)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021502000",
                    common_name="The Quadrant",
                    latlong="POINT(-0.305844 51.763425)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021509680",
                    common_name="Ardens Way",
                    latlong="POINT(-0.297324 51.761854)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021509650",
                    common_name="Beechwood Ave",
                    latlong="POINT(-0.305929 51.760000)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021509620",
                    common_name="Homewood Road",
                    latlong="POINT(-0.313182 51.759376)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021509600",
                    common_name="Churchill Road",
                    latlong="POINT(-0.317167 51.759002)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021505160",
                    common_name="St Albans City Railway Station",
                    latlong="POINT(-0.326838 51.750598)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021505100",
                    common_name="Lattimore Road",
                    latlong="POINT(-0.332185 51.750952)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021505080",
                    common_name="Police Station",
                    latlong="POINT(-0.337911 51.752032)",
                    active=True,
                ),
                StopPoint(
                    atco_code="210021503160",
                    common_name="St Peter's Street",
                    latlong="POINT(-0.336842 51.753914)",
                    active=True,
                ),
            ]
        )

        source = DataSource.objects.create()
        cls.service = Service.objects.create(tracking=True, slug="s9")
        StopUsage.objects.create(service=cls.service, stop_id="210021509680", order=0)
        route = Route.objects.create(source=source, service=cls.service)

        calendar = Calendar.objects.create(
            mon=True,
            tue=True,
            wed=True,
            thu=True,
            fri=True,
            sat=True,
            sun=True,
            start_date="2022-01-01",
        )

        trip = Trip.objects.create(
            route=route,
            start="10:30:00",
            end="10:54:00",
            destination_id="210021503158",
            calendar=calendar,
        )
        trip_after_midnight = Trip.objects.create(
            route=route,
            start="24:01:00",
            end="24:10:00",
            destination_id="210021503158",
            calendar=calendar,
        )

        StopTime.objects.bulk_create(
            [
                StopTime(trip=trip, stop_id="210021503160", departure="10:30:00"),
                StopTime(trip=trip, stop_id="210021505080", departure="10:31:00"),
                StopTime(trip=trip, stop_id="210021505100", departure="10:34:00"),
                StopTime(trip=trip, stop_id="210021505160", departure="10:36:00"),
                StopTime(trip=trip, stop_id="210021509600", departure="10:39:00"),
                StopTime(trip=trip, stop_id="210021509620", departure="10:40:00"),
                StopTime(trip=trip, stop_id="210021509650", departure="10:41:00"),
                StopTime(trip=trip, stop_id="210021509680", departure="10:43:00"),
                StopTime(trip=trip, stop_id="210021502000", departure="10:45:00"),
                StopTime(trip=trip, stop_id="210021509645", departure="10:45:00"),
                StopTime(trip=trip, stop_id="210021502160", departure="10:46:00"),
                StopTime(trip=trip, stop_id="210021502180", departure="10:47:00"),
                StopTime(trip=trip, stop_id="210021502200", departure="10:48:00"),
                StopTime(trip=trip, stop_id="210021506765", departure="10:51:00"),
                StopTime(trip=trip, stop_id="210021506690", departure="10:53:00"),
                StopTime(trip=trip, stop_id="210021503158", arrival="10:54:00"),
                StopTime(
                    trip=trip_after_midnight,
                    stop_id="210021503160",
                    departure="24:01:00",
                ),
                StopTime(
                    trip=trip_after_midnight, stop_id="210021503158", arrival="24:10:00"
                ),
            ]
        )
        cls.journey = VehicleJourney.objects.create(
            trip=trip, datetime="2022-01-04T00:00:00Z", date="2022-01-04", source=source
        )
        cls.journey_after_midnight = VehicleJourney.objects.create(
            trip=trip_after_midnight,
            datetime="2022-01-04T00:00:00Z",
            date="2022-01-04",
            source=source,
        )

    def test(self):
        response = self.client.get(f"/journeys/{self.journey.id}.json")
        self.assertTrue(response.content)

    def test_get_progress(self):
        progress = rtpi.get_progress(
            {
                "coordinates": [-0.320573, 51.75536],
                "trip_id": self.journey.trip_id,
                "heading": 200,
                "datetime": "2023-08-31T09:50:07Z",
                "date": "2023-08-31",
            }
        )
        self.assertEqual(progress.prev_stop_time.stop_id, "210021502200")
        progress = rtpi.get_progress(
            {
                "coordinates": [-0.320573, 51.75536],
                "trip_id": self.journey.trip_id,
                "heading": 84,
                "datetime": "2023-08-31T09:50:07Z",
                "date": "2023-08-31",
            }
        )
        self.assertEqual(progress.prev_stop_time.stop_id, "210021505160")
        progress = rtpi.get_progress(
            {
                "coordinates": [-0.307577, 51.75986],
                "trip_id": self.journey.trip_id,
                "heading": 200.0,
                "datetime": "2023-08-31T09:50:07Z",
                "date": "2023-08-31",
            }
        )
        self.assertEqual(progress.prev_stop_time.stop_id, "210021509645")
        progress = rtpi.get_progress(
            {
                "coordinates": [-0.307577, 51.75986],
                "trip_id": self.journey.trip_id,
                "heading": "90",
                "datetime": "2023-08-31T09:50:07Z",
                "date": "2023-08-31",
            }
        )
        self.assertEqual(progress.prev_stop_time.stop_id, "210021509620")

        item = {
            "coordinates": [-0.326838, 51.750598],
            "trip_id": self.journey.trip_id,
            "heading": None,
            "datetime": "2023-08-31T09:50:07Z",
            "date": "2023-08-31",
        }
        rtpi.add_progress_and_delay(item)
        self.assertEqual(item["progress"]["progress"], 1)
        self.assertEqual(item["delay"], 847)

        item = {
            "coordinates": [-0.332185, 51.750952],
            "trip_id": self.journey.trip_id,
            "heading": None,
            "datetime": "2023-08-31T09:50:07Z",
            "date": "2023-08-31",
        }
        rtpi.add_progress_and_delay(item)
        self.assertEqual(item["progress"]["progress"], 1)
        self.assertEqual(item["delay"], 967)

        # a long way off route
        item["coordinates"] = [0, 50]
        del item["progress"]
        del item["delay"]
        rtpi.add_progress_and_delay(item)
        self.assertNotIn("progress", item)
        self.assertNotIn("delay", item)

        # trip doesn't exist
        self.journey.trip.delete()
        rtpi.add_progress_and_delay(item)

    def test_get_progress_route_link(self):
        item = {
            # 1.7km south of the straight line from Lattimore Road to the station
            "coordinates": [-0.3295, 51.735],
            "trip_id": self.journey.trip_id,
            "heading": None,
            "datetime": "2023-08-31T09:50:07Z",
            "date": "2023-08-31",
        }
        self.assertIsNone(rtpi.get_progress(item))

        RouteLink.objects.create(
            service=self.service,
            from_stop_id="210021505100",
            to_stop_id="210021505160",
            geometry="LINESTRING(-0.332185 51.750952, -0.3295 51.735, -0.326838 51.750598)",
        )
        progress = rtpi.get_progress(item)
        self.assertEqual(progress.prev_stop_time.stop_id, "210021505100")
        self.assertEqual(progress.next_stop_time.stop_id, "210021505160")
        self.assertAlmostEqual(progress.progress, 0.5, 1)
        self.assertLess(progress.distance, 1)

    def test_get_progress_stops_hint(self):
        item = {
            "coordinates": [-0.320573, 51.75536],
            "trip_id": self.journey.trip_id,
            "heading": None,
            "datetime": "2023-08-31T09:50:07Z",
            "date": "2023-08-31",
        }
        progress = rtpi.get_progress(item | {"stops": [None, "210021509600"]})
        self.assertEqual(progress.prev_stop_time.stop_id, "210021505160")
        progress = rtpi.get_progress(item | {"stops": ["x", "210021506765"]})
        self.assertEqual(progress.prev_stop_time.stop_id, "210021502200")
        progress = rtpi.get_progress(item | {"stops": ["210021502200", "y"]})
        self.assertEqual(progress.prev_stop_time.stop_id, "210021502200")

        # unknown stops - ignored
        progress = rtpi.get_progress(item | {"stops": ["x", "y"]})
        self.assertIsNotNone(progress)

    def test_get_progress_no_stop_times(self):
        self.assertIsNone(
            rtpi.get_progress(
                {
                    "coordinates": [-0.320573, 51.75536],
                    "trip_id": self.journey.trip_id,
                    "heading": 200,
                    "datetime": "2023-08-31T09:50:07Z",
                    "date": "2023-08-31",
                },
                stop_times=[],
                pairs=rtpi.Pairs([], {}),
            )
        )

    @time_machine.travel("2024-02-16T00:00:07Z")
    def test_stop_times(self):
        redis_client = fakeredis.FakeStrictRedis()

        def set_vehicle(item):
            # as import_live_vehicles would
            rtpi.add_progress_and_delay(item)
            redis_client.set("vehicle1", json.dumps(item))

        with patch("departures.avl.redis_client", redis_client):
            redis_client.sadd(f"service{self.service.id}vehicles", 1)
            set_vehicle(
                {
                    "id": 1,
                    "journey_id": 1,
                    "coordinates": [-0.332185, 51.750952],
                    "heading": 0,
                    "trip_id": self.journey.trip_id,
                    "datetime": "2024-02-16T10:50:07Z",
                    "date": "2024-02-16",
                }
            )
            response_json = self.client.get("/stops/210021509680/times.json").json()
            self.assertEqual(response_json["times"][0]["delay"], "PT967S")
            self.assertEqual(
                response_json["times"][0]["expected_departure_time"],
                "2024-02-16T10:59:07Z",
            )

            with self.assertNumQueries(13):
                response = self.client.get("/stops/210021509680/departures")
                self.assertContains(response, "10:43")  # scheduled time
                self.assertContains(response, "10:59")  # expected time

            # past the scheduled time - should still show late departure
            with time_machine.travel("2024-02-16T10:50:00Z"), self.assertNumQueries(10):
                response = self.client.get("/stops/210021509680/departures")
                self.assertContains(response, "10:43")  # scheduled time
                self.assertContains(response, "10:59")  # expected time

            # on route, before scheduled start
            set_vehicle(
                {
                    "id": 1,
                    "journey_id": 1,
                    "coordinates": [-0.332185, 51.750952],
                    "heading": 0,
                    "trip_id": self.journey.trip_id,
                    "datetime": "2024-02-16T10:00:07Z",
                    "date": "2024-02-16",
                }
            )
            response_json = self.client.get("/stops/210021509680/times.json").json()
            self.assertEqual(response_json["times"][0]["delay"], "-PT2033S")
            self.assertEqual(
                response_json["times"][0]["expected_departure_time"],
                "2024-02-16T10:09:07Z",
            )

            # delay already calculated (eg TfW)
            set_vehicle(
                {
                    "id": 1,
                    "journey_id": 1,
                    "coordinates": [1, 50],
                    "heading": 0,
                    "trip_id": self.journey.trip_id,
                    "datetime": "2024-02-16T10:50:07Z",
                    "date": "2024-02-16",
                    "delay": 60,
                }
            )
            response_json = self.client.get("/stops/210021509680/times.json").json()
            self.assertEqual(response_json["times"][0]["delay"], "PT60S")
            self.assertEqual(
                response_json["times"][0]["expected_departure_time"],
                "2024-02-16T10:44:00Z",
            )

            # a long way off-route - no prediction
            set_vehicle(
                {
                    "id": 1,
                    "journey_id": 1,
                    "coordinates": [1, 50],
                    "heading": 0,
                    "trip_id": self.journey.trip_id,
                    "datetime": "2024-02-16T10:50:07Z",
                    "date": "2024-02-16",
                }
            )
            response_json = self.client.get("/stops/210021509680/times.json").json()
            self.assertNotIn("delay", response_json["times"][0])
            self.assertNotIn("expected_departure_time", response_json["times"][0])

    def test_get_stop_times_cache(self):
        trip_id = self.journey.trip_id
        date = datetime.date.fromisoformat(str(self.journey.date))

        rtpi.get_stop_times(trip_id, date, self.service.modified_at)

        with self.assertNumQueries(0):
            trip, stop_times, _ = rtpi.get_stop_times(
                trip_id, date, self.service.modified_at
            )

        self.assertEqual(trip.id, trip_id)
        self.assertEqual(16, len(stop_times))

        # route links shared by the service's other trips
        keys = rtpi.pair_keys(stop_times)
        with self.assertNumQueries(0):
            route_links = rtpi.get_route_links(self.service.id, keys, rtpi.UNKNOWN)
        self.assertIs(
            route_links,
            rtpi.get_route_links(self.service.id, keys, self.service.modified_at),
        )

        # service reimported
        self.service.save()
        with CaptureQueriesContext(connection) as queries:
            rtpi.get_stop_times(trip_id, date, self.service.modified_at)
        self.assertTrue(queries)

        # service not loaded - assume unchanged
        with self.assertNumQueries(0):
            rtpi.get_stop_times(trip_id, date, rtpi.UNKNOWN)

        # no modified_at - not cached
        with CaptureQueriesContext(connection) as queries:
            rtpi.get_stop_times(trip_id, date)
        self.assertTrue(queries)

    @time_machine.travel("2023-08-31T09:50:07Z")
    def test_save_computes_progress_and_delay(self):
        vehicle = Vehicle.objects.create(code="T1")

        # a fresh journey (not cls.journey, which other tests share and
        # whose date would give a nonsensical delay against this datetime)
        journey = VehicleJourney.objects.create(
            trip=self.journey.trip,
            service=self.service,
            datetime="2023-08-31T09:50:07Z",
            date=datetime.date.fromisoformat("2023-08-31"),
            source=DataSource.objects.create(),
        )

        location = VehicleLocation(Point(-0.326838, 51.750598, srid=4326))
        location.datetime = datetime.datetime.fromisoformat("2023-08-31T09:50:07Z")
        location.journey = journey
        location.id = vehicle.id

        command = ImportLiveVehiclesCommand()
        command.source = DataSource.objects.create()
        command.source.datetime = None  # so save() doesn't treat this as stale
        command.to_save = [(location, vehicle)]

        redis_client = fakeredis.FakeStrictRedis(version=7)

        with (
            patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            patch(
                "vehicles.management.import_live_vehicles.get_channel_layer",
                return_value=None,
            ),
        ):
            command.save()

        redis_json = json.loads(redis_client.get(f"vehicle{vehicle.id}"))
        self.assertEqual(redis_json["progress"]["progress"], 1)
        self.assertEqual(redis_json["delay"], 847)

        # location unchanged next time - nothing new, despite the added progress
        fresh = json.loads(json.dumps(location.get_redis_json(tz=command.tzinfo)))
        self.assertNotEqual(fresh, redis_json)
        self.assertTrue(command.same_as_latest(fresh, redis_json))
        fresh["heading"] = 90
        self.assertFalse(command.same_as_latest(fresh, redis_json))


class SplitTripDeparturesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="Stagecoach Cumbria")
        for atco_code, latitude in (
            ("a", 51.0),
            ("b", 51.02),
            ("c", 51.04),
            ("d", 51.06),
        ):
            StopPoint.objects.create(
                atco_code=atco_code, latlong=f"POINT(0 {latitude})", active=True
            )

        # same days, different date ranges (separately registered parts)
        calendar_1 = Calendar.objects.create(start_date="2026-09-01", mon=True)
        calendar_2 = Calendar.objects.create(start_date="2026-09-07", mon=True)

        service = Service.objects.create(
            line_name="555", trip_chaining="split", tracking=True
        )
        StopUsage.objects.create(service=service, stop_id="c", order=0)
        route_1 = Route.objects.create(
            line_name="555",
            code="555a",
            service_code="PB:555",
            service=service,
            source=source,
        )
        route_2 = Route.objects.create(
            line_name="555",
            code="555b",
            service_code="PB:556",
            service=service,
            source=source,
        )
        cls.trip_1 = Trip.objects.create(
            route=route_1, start="09:00:00", end="09:10:00", calendar=calendar_1
        )
        cls.trip_2 = Trip.objects.create(
            route=route_2, start="09:10:00", end="09:30:00", calendar=calendar_2
        )
        cls.trip_3 = Trip.objects.create(
            route=route_2,
            start="09:40:00",
            end="09:50:00",
            calendar=calendar_2,
            block="7",
        )
        StopTime.objects.bulk_create(
            [
                StopTime(trip=cls.trip_1, stop_id="a", departure="09:00:00"),
                StopTime(trip=cls.trip_1, stop_id="b", arrival="09:10:00"),
                StopTime(trip=cls.trip_2, stop_id="b", departure="09:10:00"),
                StopTime(trip=cls.trip_2, stop_id="c", departure="09:20:00"),
                StopTime(trip=cls.trip_2, stop_id="d", arrival="09:30:00"),
                StopTime(trip=cls.trip_3, stop_id="c", departure="09:40:00"),
                StopTime(trip=cls.trip_3, stop_id="d", arrival="09:50:00"),
            ]
        )
        vehicle = Vehicle.objects.create(code="1")
        cls.journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            trip=cls.trip_1,
            datetime="2026-09-07T08:00:00Z",
            date="2026-09-07",
            source=source,
        )

    def get_item(self, **kwargs):
        item = {
            "id": 1,
            "journey_id": self.journey.id,
            "coordinates": [0, 51.01],  # halfway between a and b
            "heading": 0,
            "trip_id": self.trip_1.id,
            "datetime": "2026-09-07T08:10:00Z",  # 09:10, scheduled 09:05
            "date": "2026-09-07",
        } | kwargs
        rtpi.add_progress_and_delay(item)
        return item

    def test_split_trip(self):
        item = self.get_item()
        self.assertEqual(item["delay"], 300)
        self.assertEqual(item["trip_ids"], [self.trip_1.id, self.trip_2.id])
        self.assertEqual(item["progress"]["trip_id"], self.trip_1.id)
        self.assertEqual(item["progress"]["prev_time"], 9 * 3600)

        redis_client = fakeredis.FakeStrictRedis()
        redis_client.sadd(f"service{self.trip_1.route.service_id}vehicles", 1)
        redis_client.set("vehicle1", json.dumps(item))

        with patch("departures.avl.redis_client", redis_client):
            with time_machine.travel("2026-09-07T08:06:00Z"):
                times = self.client.get("/stops/c/times.json").json()["times"]
                self.assertEqual(times[0]["trip_id"], self.trip_2.id)
                self.assertEqual(times[0]["delay"], "PT300S")

            # overdue, but the vehicle is still on the first part
            with time_machine.travel("2026-09-07T08:22:00Z"):
                times = self.client.get("/stops/c/times.json").json()["times"]
                self.assertEqual(times[0]["trip_id"], self.trip_2.id)
                self.assertEqual(
                    times[0]["expected_departure_time"], "2026-09-07T09:25:00+01:00"
                )

                response = self.client.get("/stops/c/departures")
                self.assertEqual(response.context["departures"][0]["live"].minute, 25)

            # wrong date
            redis_client.set("vehicle1", json.dumps(item | {"date": "2026-08-31"}))
            with time_machine.travel("2026-09-07T08:06:00Z"):
                times = self.client.get("/stops/c/times.json").json()["times"]
                self.assertNotIn("delay", times[0])

    def test_is_before_stop(self):
        def is_before_stop(stop_id, hour, **progress):
            progress = {
                "id": 100,
                "prev_stop": "a",
                "prev_time": 9 * 3600,
                "next_stop": "b",
                "progress": 0.5,
            } | progress
            item = {"progress": {k: v for k, v in progress.items() if v is not None}}
            stop_time = StopTime(
                id=50,
                trip_id=self.trip_1.id,
                stop_id=stop_id,
                departure=datetime.timedelta(hours=hour),
            )
            return rtpi.is_before_stop(item, stop_time)

        # stop time ids from before the timetable was reimported
        self.assertFalse(is_before_stop("x", 8, id=1))
        self.assertTrue(is_before_stop("x", 10, id=1000))

        # same time
        self.assertFalse(is_before_stop("a", 9, id=1))
        self.assertTrue(is_before_stop("a", 9, id=1000, progress=0))
        self.assertTrue(is_before_stop("b", 9, id=1000))
        self.assertTrue(is_before_stop("x", 9, id=1))
        self.assertFalse(is_before_stop("x", 9, id=1000))

        # no prev_time
        self.assertTrue(is_before_stop("x", 8, id=1, prev_time=None))

    def test_adjacent_pair(self):
        # 3/4 of the way from a to b, but also near enough to b-c
        item = self.get_item(coordinates=[0, 51.015], datetime="2026-09-07T08:10:00Z")
        self.assertEqual(item["progress"]["prev_stop"], "a")
        self.assertEqual(item["delay"], 150)

    def test_block(self):
        redis_client = fakeredis.FakeStrictRedis()
        redis_client.sadd(f"service{self.trip_1.route.service_id}vehicles", 1)

        def get_vehicle(**kwargs):
            item = self.get_item(block="7") | kwargs
            redis_client.set("vehicle1", json.dumps(item))
            response = self.client.get("/stops/c/departures")
            departure = next(
                departure
                for departure in response.context["departures"]
                if departure["stop_time"].trip_id == self.trip_3.id
            )
            return departure.get("vehicle")

        with (
            patch("departures.avl.redis_client", redis_client),
            time_machine.travel("2026-09-07T08:06:00Z"),
        ):
            self.assertEqual(get_vehicle(), self.journey.vehicle)
            self.assertIsNone(get_vehicle(date="2026-08-31"))
            self.assertIsNone(get_vehicle(operator_id="OTHR"))
