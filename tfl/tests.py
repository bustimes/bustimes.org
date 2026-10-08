from datetime import UTC, date, datetime, timedelta

from django.test import TestCase

from api.views import VehicleJourneyViewSet
from vehicles.models import VehicleJourney

from . import models


class JourneyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # the same journey idx means a different journey in each version
        for version, line_name, start_time in (
            (20260926, "97", timedelta(hours=17, minutes=22)),
            (20261009, "W15", timedelta(hours=9)),
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
        trip = VehicleJourneyViewSet.trip_from_tfl(journey)
        self.assertEqual(trip.start, timedelta(hours=17, minutes=22))

        journey.route_name = "98"
        self.assertIsNone(VehicleJourneyViewSet.trip_from_tfl(journey))
