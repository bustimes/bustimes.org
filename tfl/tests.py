import json
from datetime import UTC, date, datetime, timedelta
from unittest import mock

import fakeredis
from django.core.management import call_command
from django.test import TestCase

from api.views import VehicleJourneyViewSet
from busstops.models import DataSource, Operator, Region, Service, StopPoint
from bustimes.models import Route, Trip
from vehicles.management.commands import import_bod_avl
from vehicles.management.tests.test_bod_avl import patch_redis_client
from vehicles.models import Livery, Vehicle, VehicleJourney

from . import models


class JourneyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        StopPoint.objects.create(
            atco_code="490001063C", common_name="Chingford Station", active=True
        )
        # the same journey idx means a different journey in each version
        for version, line_name, start_time, block_no in (
            (20260926, "97", timedelta(hours=17, minutes=22), 105401),
            (20261009, "W15", timedelta(hours=9), 105402),
        ):
            base_version = models.BaseVersion.objects.create(version=version)
            models.Line.objects.create(
                base_version=base_version,
                contract_line_no=line_name,
                service_line_no=line_name,
                logical_line_no=586,
            )
            models.Pattern.objects.create(
                base_version=base_version,
                idx=1316,
                contract_line_no=line_name,
                direction=1,
                type=1,
            )
            models.Block.objects.create(
                base_version=base_version,
                idx=11358,
                operator_code="LI",
                block_no=block_no,
                running_no=401,
            )
            for day in range(7, 11):
                models.BlockCalendarDay.objects.create(
                    base_version=base_version,
                    block_idx=11358,
                    calendar_day=date(2026, 10, day),
                    runs=True,
                )
            models.Stop.objects.create(
                base_version=base_version,
                idx=14780,
                naptan_code="490001063C",
                name="Chingford Station",
            )
            models.Journey.objects.create(
                base_version=base_version,
                idx=255600,
                pattern_idx=1316,
                block_idx=11358,
                trip_no_lbsl=185,
                type=1,
                start_time=start_time,
            )
            models.StopInPattern.objects.create(
                base_version=base_version,
                idx=11530,
                pattern_idx=1316,
                stop_idx=14780,
                sequence_no=1,
            )

    def test_matches(self):
        journeys = models.Journey.objects.select_related("pattern__line").order_by(
            "base_version"
        )
        old, new = journeys
        self.assertEqual(old.pattern.line.service_line_no, "97")
        self.assertEqual(new.pattern.line.service_line_no, "W15")

        departure = datetime(2026, 10, 8, 16, 22, tzinfo=UTC)  # 17:22 BST
        self.assertTrue(old.matches(departure))
        self.assertFalse(old.matches(departure + timedelta(minutes=1)))
        self.assertFalse(new.matches(departure))
        self.assertTrue(new.matches(None))

    def test_trip_from_tfl(self):
        journey = VehicleJourney(
            code="255600",
            route_name="97",
            date=date(2026, 10, 8),
            datetime=datetime(2026, 10, 8, 16, 22, tzinfo=UTC),
        )
        with self.assertNumQueries(4):
            trip = VehicleJourneyViewSet.trip_from_tfl(journey)
        self.assertEqual(trip.start, timedelta(hours=17, minutes=22))
        self.assertEqual(trip.stops[0].stop.common_name, "Chingford Station")
        self.assertTrue(trip.stops[0].stop.active)  # the real StopPoint
        self.assertTrue(trip.stops[0].pick_up)

        models.Stop.objects.update(naptan_code="")
        trip = VehicleJourneyViewSet.trip_from_tfl(journey)
        self.assertFalse(trip.stops[0].pick_up)
        self.assertFalse(trip.stops[0].set_down)
        models.Stop.objects.update(naptan_code="490001063C")

        journey.route_name = "98"
        self.assertIsNone(VehicleJourneyViewSet.trip_from_tfl(journey))

    def test_trip_from_tfl_after_midnight(self):
        models.Journey.objects.filter(base_version__version=20260926).update(
            start_time=timedelta(hours=26, minutes=34)
        )
        journey = VehicleJourney(
            code="255600",
            route_name="97",
            date=date(2026, 10, 11),
            datetime=datetime(2026, 10, 11, 1, 34, tzinfo=UTC),  # 02:34 BST
        )
        trip = VehicleJourneyViewSet.trip_from_tfl(journey)
        self.assertEqual(trip.start, timedelta(hours=2, minutes=34))
        self.assertEqual(trip.stops[0].departure, timedelta(hours=2, minutes=34))

        # journey date already the previous day - leave the time alone
        journey.date = date(2026, 10, 10)
        trip = VehicleJourneyViewSet.trip_from_tfl(journey)
        self.assertEqual(trip.start, timedelta(hours=26, minutes=34))

    def test_match_tfl_trip_blocks(self):
        Region.objects.create(id="L", name="London")
        source = DataSource.objects.create(name="L")
        service = Service.objects.create(line_name="97", current=True, region_id="L")
        route = Route.objects.create(source=source, service=service, code="97")
        trip = Trip.objects.create(
            route=route,
            start=timedelta(hours=17, minutes=22),
            end=timedelta(hours=18),
        )

        call_command("match_tfl_trip_blocks")

        # matched against 20260926 - in 20261009 that journey is a W15
        trip.refresh_from_db()
        self.assertEqual(trip.block, "105401")

    def test_predictions(self):
        old = models.BaseVersion.objects.get(version=20260926)
        models.Journey.objects.create(
            base_version=old,
            idx=255601,
            pattern_idx=1316,
            block_idx=11358,
            trip_no_lbsl=186,
            type=1,
            start_time=timedelta(hours=18, minutes=30),
        )
        source = DataSource.objects.create(name="Bus Open Data")
        vehicle = Vehicle.objects.create(
            code="LJ123",
            latest_journey_data={"MonitoredVehicleJourney": {"OperatorRef": "TFLO"}},
        )
        vehicle.latest_journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            source=source,
            code="255600",
            route_name="97",
            date=date(2026, 10, 8),
            datetime=datetime(2026, 10, 8, 16, 22, tzinfo=UTC),
        )
        vehicle.save(update_fields=["latest_journey"])

        response = self.client.get(f"{vehicle.get_absolute_url()}?date=2026-10-08")
        (prediction,) = response.context_data["predictions"]
        self.assertEqual(str(prediction), "18:30")
        self.assertEqual(prediction.line_name, "97")
        self.assertEqual(prediction.destination_name, "Chingford Station")
        self.assertContains(response, "<td>105401</td>")

        # run in to the garage
        models.Journey.objects.filter(base_version=old, idx=255601).update(type=3)
        models.Stop.objects.create(
            base_version=old, idx=14781, naptan_code="", name="Chingford Garage"
        )
        models.StopInPattern.objects.create(
            base_version=old, idx=11531, pattern_idx=1316, stop_idx=14781, sequence_no=2
        )
        models.Garage.objects.create(
            base_version=old,
            number=1,
            operator_code="LI",
            code="CH",
            name="CHINGFORD GARAGE",
        )
        response = self.client.get(f"{vehicle.get_absolute_url()}?date=2026-10-08")
        (prediction,) = response.context_data["predictions"]
        self.assertEqual(prediction.destination_name, "CHINGFORD GARAGE")

        # block doesn't run that day in this base version
        models.BlockCalendarDay.objects.filter(
            base_version=old, calendar_day=date(2026, 10, 8)
        ).delete()
        response = self.client.get(f"{vehicle.get_absolute_url()}?date=2026-10-08")
        self.assertNotIn("predictions", response.context_data)

    def test_import_bod_avl(self):
        models.Journey.objects.filter(base_version__version=20260926).update(
            start_time=timedelta(hours=26, minutes=34)
        )
        base_version = models.BaseVersion.objects.get(version=20260926)
        StopPoint.objects.filter(atco_code="490001063C").update(
            latlong="POINT(0.0094 51.6331)"
        )
        StopPoint.objects.create(
            atco_code="490005181E",
            common_name="Chingford Mount",
            latlong="POINT(-0.0004 51.6182)",
            active=True,
        )
        models.Stop.objects.create(
            base_version=base_version,
            idx=14781,
            naptan_code="490005181E",
            name="Chingford Mount",
        )
        models.StopInPattern.objects.create(
            base_version=base_version,
            idx=11531,
            pattern_idx=1316,
            stop_idx=14781,
            sequence_no=2,
        )
        models.JourneyDriveTime.objects.create(
            base_version=base_version,
            journey_idx=255600,
            stop_in_pattern_from_idx=11530,
            stop_in_pattern_to_idx=11531,
            drive_time=timedelta(minutes=10),
        )
        Livery.objects.create(id=262, name="TfL", published=True)
        Operator.objects.create(noc="TFLO", name="Transport for London")

        command = import_bod_avl.Command()
        command.source = DataSource.objects.create(name="Bus Open Data")
        item = {
            "RecordedAtTime": "2026-10-11T01:39:00+00:00",
            "MonitoredVehicleJourney": {
                "LineRef": "97",
                "PublishedLineName": "97",
                "OperatorRef": "TFLO",
                "DestinationName": "Chingford Mount",
                "OriginAimedDepartureTime": "2026-10-11T01:34:00+00:00",
                # halfway between the stops
                "VehicleLocation": {"Longitude": "0.0045", "Latitude": "51.62565"},
                "VehicleJourneyRef": "255600",
                "VehicleRef": "LJ16NAC",
            },
            "Extensions": None,
        }
        redis_client = fakeredis.FakeStrictRedis()
        with patch_redis_client(redis_client):
            command.handle_item(item)
            command.save()

        journey = VehicleJourney.objects.get()
        # after midnight - the service date is the day before
        self.assertEqual(journey.date, date(2026, 10, 10))
        self.assertIsNone(journey.trip)

        location = json.loads(redis_client.get(f"vehicle{journey.vehicle_id}"))
        self.assertEqual(location["progress"]["prev_stop"], "490001063C")
        self.assertEqual(location["progress"]["next_stop"], "490005181E")
        self.assertAlmostEqual(location["delay"], 0, delta=30)

        # timetable trip starting at 01:34 (not 25:34) - its date wins
        service = Service.objects.create(line_name="97", current=True)
        route = Route.objects.create(source=command.source, service=service, code="97")
        trip = Trip.objects.create(
            route=route,
            start=timedelta(hours=1, minutes=34),
            end=timedelta(hours=2),
        )
        item["MonitoredVehicleJourney"]["VehicleRef"] = "LJ16NAD"
        with (
            patch_redis_client(redis_client),
            mock.patch.object(command, "get_service", return_value=service),
            mock.patch.object(VehicleJourney, "get_trip", return_value=trip),
        ):
            command.handle_item(item)
            command.save()

        journey = VehicleJourney.objects.get(vehicle__code="LJ16NAD")
        self.assertEqual(journey.trip, trip)
        self.assertEqual(journey.date, date(2026, 10, 11))
