from datetime import date

from django.test import TestCase

from busstops.models import DataSource, Service, StopPoint

from .models import Calendar, Route, StopTime, Trip


class SplitTripsTest(TestCase):
    """the timetable and Trip.get_parts() should agree about which trips are parts
    of the same journey"""

    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="Stagecoach Cumbria")
        for atco_code in ("a", "b", "c"):
            StopPoint.objects.create(atco_code=atco_code, active=True)

        # same days, different date ranges (separately registered parts)
        calendar_1 = Calendar.objects.create(start_date="2026-09-01", mon=True)
        calendar_2 = Calendar.objects.create(start_date="2026-09-07", mon=True)

        cls.service = Service.objects.create(line_name="555")
        route_1 = Route.objects.create(
            line_name="555",
            code="555a",
            service_code="PB0002032:555",
            service=cls.service,
            source=source,
        )
        route_2 = Route.objects.create(
            line_name="555",
            code="555b",
            service_code="PB0002032:556",
            service=cls.service,
            source=source,
        )

        # no ticket machine codes
        cls.trip_1 = Trip.objects.create(
            route=route_1,
            start="09:00",
            end="09:30",
            calendar=calendar_1,
            destination_id="b",
        )
        cls.trip_2 = Trip.objects.create(
            route=route_2,
            start="09:35",
            end="10:00",
            calendar=calendar_2,
            destination_id="c",
        )
        # starts too late to be part of trip_1 (trip_2 is the earlier match)
        cls.trip_3 = Trip.objects.create(
            route=route_2,
            start="09:40",
            end="10:05",
            calendar=calendar_2,
            destination_id="c",
        )
        # a future revision of route_2 (shouldn't be matched when no date is given)
        route_2_future = Route.objects.create(
            line_name="555",
            code="555b-future",
            service_code="PB0002032:556",
            service=cls.service,
            source=source,
            revision_number=2,
            start_date="2099-01-01",
        )
        trip_4 = Trip.objects.create(
            route=route_2_future,
            start="09:35",
            end="10:00",
            calendar=calendar_2,
            destination_id="c",
        )
        StopTime.objects.bulk_create(
            [
                StopTime(trip=cls.trip_1, stop_id="a", departure="09:00"),
                StopTime(trip=cls.trip_1, stop_id="b", arrival="09:30"),
                StopTime(trip=cls.trip_2, stop_id="b", departure="09:35"),
                StopTime(trip=cls.trip_2, stop_id="c", arrival="10:00"),
                StopTime(trip=cls.trip_3, stop_id="b", departure="09:40"),
                StopTime(trip=cls.trip_3, stop_id="c", arrival="10:05"),
                StopTime(trip=trip_4, stop_id="b", departure="09:35"),
                StopTime(trip=trip_4, stop_id="c", arrival="10:00"),
            ]
        )

    def test_split_trips(self):
        day = date(2026, 9, 7)

        timetable = self.service.get_timetable(day).render()
        self.assertEqual(
            [trip.id for trip in timetable.groupings[0].trips],
            [self.trip_1.id, self.trip_3.id],
        )

        parts = [self.trip_1.id, self.trip_2.id]
        self.assertEqual([trip.id for trip in self.trip_1.get_parts(day)], parts)
        self.assertEqual([trip.id for trip in self.trip_2.get_parts(day)], parts)
        self.assertEqual([trip.id for trip in self.trip_1.get_parts()], parts)
        self.assertEqual([trip.id for trip in self.trip_2.get_parts()], parts)
        self.assertEqual(
            [trip.id for trip in self.trip_3.get_parts(day)], [self.trip_3.id]
        )

        # trip_2 doesn't run on 2026-09-01
        self.assertEqual(
            [trip.id for trip in self.trip_1.get_parts(date(2026, 9, 1))],
            [self.trip_1.id],
        )
