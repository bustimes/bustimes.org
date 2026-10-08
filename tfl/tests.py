from datetime import UTC, date, datetime, timedelta

from django.core.management import call_command
from django.test import TestCase

from api.views import VehicleJourneyViewSet
from busstops.models import DataSource, Region, Service, StopPoint
from bustimes.models import Route, Trip
from vehicles.models import VehicleJourney

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
        self.assertTrue(old.matches("97", departure))
        self.assertFalse(old.matches("97", departure + timedelta(minutes=1)))
        self.assertFalse(new.matches("97", departure))

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

        journey.route_name = "98"
        self.assertIsNone(VehicleJourneyViewSet.trip_from_tfl(journey))

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
