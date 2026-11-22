from datetime import UTC, datetime

from django.test import TestCase

from busstops.models import DataSource, Service
from vehicles.models import VehicleJourney

from .models import Calendar, Route, Trip


class GetTripTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="First Norwich")

        thursdays = Calendar.objects.create(
            start_date="2025-07-01",
            mon=True,
            tue=True,
            wed=True,
            thu=True,
        )
        fridays = Calendar.objects.create(
            start_date="2025-07-01",
            fri=True,
        )
        saturdays = Calendar.objects.create(
            start_date="2025-07-01",
            sat=True,
        )

        cls.service = Service.objects.create(line_name="25")
        route = Route.objects.create(line_name="25", service=cls.service, source=source)

        Trip.objects.bulk_create(
            [
                Trip(
                    route=route,
                    ticket_machine_code="1111",
                    start="26:00:00",
                    end="27:00:00",
                    calendar=thursdays,
                ),
                Trip(
                    route=route,
                    ticket_machine_code="1111",
                    start="26:00:00",
                    end="27:00:00",
                    calendar=fridays,
                ),
                Trip(
                    route=route,
                    ticket_machine_code="1111",
                    start="26:00:00",
                    end="27:00:00",
                    calendar=saturdays,
                ),
            ]
        )

    def test_after_midnight(self):
        """It's 2am on Saturday - get_trip should get the trip with the Friday calendar"""

        journey = VehicleJourney(
            service=self.service,
            code="1111",
            datetime=datetime(2025, 7, 12, 1, 0, 0, tzinfo=UTC),  # 2am BST
        )
        trip = journey.get_trip()
        self.assertFalse(trip.calendar.thu)
        self.assertTrue(trip.calendar.fri)
        self.assertFalse(trip.calendar.sat)

        # Friday mornihg
        journey.datetime = datetime(2025, 7, 11, 1, 0, 0, tzinfo=UTC)
        trip = journey.get_trip()
        self.assertTrue(trip.calendar.thu)
        self.assertFalse(trip.calendar.fri)
        self.assertFalse(trip.calendar.sat)

        # Sunday mornihg
        journey.datetime = datetime(2025, 7, 13, 1, 0, 0, tzinfo=UTC)
        trip = journey.get_trip()
        self.assertFalse(trip.calendar.thu)
        self.assertFalse(trip.calendar.fri)
        self.assertTrue(trip.calendar.sat)

    def test_line_name(self):
        source = DataSource.objects.get()
        calendar = Calendar.objects.create(start_date="2025-07-01", sun=True)
        service = Service.objects.create(line_name="X44")
        route_44 = Route.objects.create(
            line_name="44", code="44", service=service, source=source
        )
        route_x41 = Route.objects.create(
            line_name="X41", code="X41", service=service, source=source
        )
        for route in (route_44, route_x41):
            Trip.objects.create(
                route=route,
                ticket_machine_code="1245",
                start="12:45:00",
                end="13:05:00",
                calendar=calendar,
            )

        journey = VehicleJourney(
            service=service,
            route_name="X41",
            code="1245",
            datetime=datetime(2025, 7, 13, 11, 50, tzinfo=UTC),
        )
        self.assertEqual(journey.get_trip().route, route_x41)

        journey.route_name = "44"
        self.assertEqual(journey.get_trip().route, route_44)
