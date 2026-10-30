from datetime import UTC, date, datetime, timedelta

from django.test import TestCase

from busstops.models import DataSource, Service, StopPoint
from vehicles.models import VehicleJourney

from .management.commands.import_transxchange import Command, delete_unused_trips
from .models import Calendar, Route, StopTime, Trip


class DeleteUnusedTripsTest(TestCase):
    def test_delete_unused_trips(self):
        source = DataSource.objects.create(name="test")
        service = Service.objects.create(line_name="1")
        stop = StopPoint.objects.create(
            atco_code="test", common_name="Test Stop", active=True
        )

        current_route = Route.objects.create(
            source=source, code="current", service=service, file_hash="a"
        )
        empty_route = Route.objects.create(source=source, code="empty", file_hash="b")
        partly_used_route = Route.objects.create(
            source=source, code="partly used", file_hash="c"
        )

        def create_trip(route):
            trip = Trip.objects.create(
                route=route, start=timedelta(0), end=timedelta(minutes=1)
            )
            StopTime.objects.create(trip=trip, stop=stop, sequence=0)
            return trip

        current_trip = create_trip(current_route)
        create_trip(empty_route)
        used_trip = create_trip(partly_used_route)
        create_trip(partly_used_route)

        VehicleJourney.objects.create(
            trip=used_trip,
            source=source,
            datetime=datetime(2026, 8, 18, 9, tzinfo=UTC),
            date=date(2026, 8, 18),
        )

        self.assertEqual(delete_unused_trips(source.route_set), 2)

        self.assertEqual(
            set(Trip.objects.values_list("id", flat=True)),
            {current_trip.id, used_trip.id},
        )
        self.assertEqual(StopTime.objects.count(), 2)

        self.assertFalse(Route.objects.filter(id=empty_route.id).exists())

        partly_used_route.refresh_from_db()
        self.assertIsNone(partly_used_route.file_hash)

        current_route.refresh_from_db()
        self.assertEqual(current_route.file_hash, "a")

    def test_mark_old_services_as_not_current(self):
        source = DataSource.objects.create(name="test")
        other_source = DataSource.objects.create(name="other")
        route = Route.objects.create(source=source, code="old")
        unused_calendar = Calendar.objects.create(
            source=source, start_date=date(2026, 1, 1)
        )
        used_calendar = Calendar.objects.create(
            source=source, start_date=date(2026, 1, 1)
        )
        other_calendar = Calendar.objects.create(
            source=other_source, start_date=date(2026, 1, 1)
        )
        Trip.objects.create(
            route=route,
            calendar=unused_calendar,
            start=timedelta(0),
            end=timedelta(minutes=1),
        )
        new_route = Route.objects.create(
            source=source,
            code="new",
            service=Service.objects.create(line_name="1"),
        )
        Trip.objects.create(
            route=new_route,
            calendar=used_calendar,
            start=timedelta(0),
            end=timedelta(minutes=1),
        )

        command = Command()
        command.set_up()
        command.source = source
        command.route_ids = {new_route.id}
        command.calendar_cache = {"hash": unused_calendar}

        command.mark_old_services_as_not_current()

        self.assertFalse(Route.objects.filter(id=route.id).exists())
        self.assertEqual(
            set(Calendar.objects.values_list("id", flat=True)),
            {used_calendar.id, other_calendar.id},
        )
        self.assertEqual(command.calendar_cache, {})
