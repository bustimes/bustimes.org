import os
from datetime import date, datetime, timedelta, timezone

from django.test import TestCase
from vcr import use_cassette

from busstops.models import DataSource, Service
from vehicles.models import Livery, Vehicle, VehicleCode

from .models import (
    Calendar,
    CalendarDate,
    Garage,
    Route,
    StopTime,
    Trip,
    TimetableDataSource,
    Version,
)
from .utils import get_routes


class BusTimesTest(TestCase):
    def test_tfl_vehicle_view(self):
        DataSource.objects.create(id=7, name="London")
        Livery.objects.create(id=262, name="London", colours="#dc241f", published=True)
        v = Vehicle.objects.create(code="LTZ1243", reg="LTZ1243")
        VehicleCode.objects.create(vehicle=v, code="TFLO:LTZ1243", scheme="BODS")

        with use_cassette(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "vcr", "tfl_vehicle.yaml"
            ),
            decode_compressed_response=True,
        ):
            with self.assertNumQueries(5):
                response = self.client.get("/vehicles/tfl/LTZ1243")

            self.assertEqual("LTZ1243", response.context["object"].reg)
            self.assertContains(response, "Old Ford Road")
            self.assertContains(response, '"OB"')
            self.assertContains(response, '"18:56"')

            response = self.client.get("/vehicles/tfl/LJ53NHP")
            self.assertEqual(response.status_code, 404)

            # Vehicle.objects.create(code="LJ53NHP", reg="LJ53NHP")
            # cassette.rewind()
            # response = self.client.get("/vehicles/tfl/LJ53NHP")
            # self.assertContains(response, "LJ53 NHP")

    def test_calendar(self):
        calendar = Calendar(
            start_date=date(2021, 1, 3),
        )

        self.assertEqual("", str(calendar))

        calendar.wed = True
        calendar.thu = True
        calendar.fri = True
        self.assertEqual("Wednesday to Friday", str(calendar))

        calendar.wed = False
        calendar.thu = False
        calendar.fri = False
        calendar.mon = True
        self.assertEqual("Mondays", str(calendar))

        calendar.tue = True
        self.assertEqual("Mondays and Tuesdays", str(calendar))

        calendar.sat = True
        self.assertEqual("Mondays, Tuesdays and Saturdays", str(calendar))

        calendar.mon = False
        calendar.sat = False
        calendar.wed = True

        calendar.start_date = date(2020, 2, 5)
        calendar.end_date = date(2020, 2, 10)
        self.assertEqual("Tuesdays and Wednesdays", str(calendar))
        calendar.bank_holiday_exclusions = [date(2021, 1, 2)]
        calendar.bank_holiday_inclusions = []
        calendar.save()
        self.assertEqual(
            "Wednesday 5 February 2020 only", calendar.describe_for_timetable()
        )
        calendar.end_date = None
        self.assertEqual(
            "Tuesdays and Wednesdays",
            calendar.describe_for_timetable(),
        )
        calendar.bank_holiday_exclusions = [date(2021, 1, 5)]
        self.assertEqual(
            "Tuesdays and Wednesdays (not bank holidays)",
            calendar.describe_for_timetable(),
        )
        calendar.bank_holiday_inclusions = [date(2021, 1, 2)]
        calendar.bank_holiday_exclusions = []
        self.assertEqual(
            "Tuesdays and Wednesdays and bank holidays from Wednesday 5 February 2020",
            calendar.describe_for_timetable(date(2020, 1, 1)),
        )

        calendar.summary = "Toby Young School of Assholery days"
        self.assertEqual(
            "Tuesdays and Wednesdays, Toby Young School of Assholery days",
            str(calendar),
        )

        calendar.summary = ""
        calendar.tue = False
        calendar.wed = False
        calendar.sat = True
        calendar.start_date = date(
            2022, 6, 12
        )  # a Sunday – won't actually operate til Saturday...
        self.assertEqual(
            "Saturdays and bank holidays from Saturday 18 June 2022",
            calendar.describe_for_timetable(date(2022, 6, 10)),
        )
        self.assertEqual(
            "Saturdays and bank holidays",  # (from this Saturday, no need to specify)
            calendar.describe_for_timetable(date(2022, 6, 16)),
        )

        calendar.bank_holiday_inclusions = []
        calendar.mon = True
        calendar.tue = True
        calendar.wed = True
        calendar.thu = True
        calendar.fri = True
        calendar.end_date = date(2022, 7, 29)
        self.assertEqual(
            "Monday to Saturday until Friday 29 July 2022",
            calendar.describe_for_timetable(date(2022, 7, 20)),
        )

        calendar.save()
        CalendarDate.objects.create(
            start_date=date(2022, 7, 22),
            end_date=date(2022, 7, 22),
            operation=False,
            calendar=calendar,
        )
        CalendarDate.objects.create(
            start_date=date(2022, 7, 24),  # "and Sunday 24 July"
            end_date=date(2022, 7, 24),
            operation=True,
            calendar=calendar,
        )

        # calendar date outside calendar date range – should have no effect
        CalendarDate.objects.create(
            start_date=date(2022, 8, 28),
            end_date=date(2022, 8, 29),
            operation=False,
            calendar=calendar,
        )
        self.assertEqual(
            "Monday to Saturday (not Friday 22 July) (and Sunday 24 July) until Friday 29 July 2022",
            calendar.describe_for_timetable(date(2022, 7, 20)),
        )

    def test_trip(self):
        trip = Trip()

        trip.start = timedelta(hours=10, minutes=47, seconds=30)
        trip.end = timedelta(hours=11, minutes=00, seconds=00)
        self.assertEqual(
            trip.start_datetime(date(2021, 6, 20)),
            datetime(2021, 6, 20, 10, 47, 30, tzinfo=timezone(timedelta(hours=1))),
        )
        self.assertEqual(
            trip.end_datetime(date(2021, 6, 20)),
            datetime(2021, 6, 20, 11, tzinfo=timezone(timedelta(hours=1))),
        )
        self.assertEqual(
            trip.start_datetime(date(2021, 11, 1)),
            datetime(2021, 11, 1, 10, 47, 30, tzinfo=timezone(timedelta())),
        )

        trip.start = timedelta(hours=25, minutes=47, seconds=30)
        self.assertEqual(
            trip.start_datetime(date(2021, 6, 20)),
            datetime(2021, 6, 21, 1, 47, 30, tzinfo=timezone(timedelta(hours=1))),
        )
        self.assertEqual(
            trip.start_datetime(date(2021, 10, 31)),
            datetime(2021, 11, 1, 1, 47, 30, tzinfo=timezone(timedelta())),
        )

        self.assertEqual(str(trip), "01:47⁺¹")

        trip.save()

        response = self.client.get(trip.get_absolute_url())
        self.assertContains(response, '"11:00:00"')

    def test_stop_time(self):
        time = StopTime(departure=timedelta(hours=10, minutes=47, seconds=30))
        self.assertEqual(str(time), "10:47")

        time.arrival = timedelta(hours=10, minutes=30, seconds=2)
        time.departure = None
        self.assertEqual(
            time.departure_or_arrival(), timedelta(hours=10, minutes=30, seconds=2)
        )

    def test_get_routes(self):
        sources = DataSource.objects.bulk_create(
            [
                DataSource(name="Lynx A"),
                DataSource(name="Lynx B"),
                DataSource(name="Leith Lynx"),
                DataSource(name="Ticketer", url=""),
            ]
        )
        service = Service.objects.create(line_name="55")

        routes = [
            Route(
                service=service,
                description="1",
                code="1",
                service_code="55",
                revision_number=3,
                source=sources[0],
                start_date=date(2022, 2, 1),
            ),
            Route(
                service=service,
                description="2",
                code="2",
                service_code="55",
                revision_number=3,
                source=sources[1],
                start_date=date(2022, 2, 1),
            ),
            Route(
                service=service,
                description="3",
                code="3",
                service_code="55",
                revision_number=4,
                source=sources[0],
                start_date=date(2022, 3, 1),
            ),
            Route(
                service=service,
                description="4",
                service_code="55",
                code="4",
                revision_number=4,
                source=sources[0],
                start_date=date(2022, 3, 1),
            ),
            Route(
                service=service,
                description="5",
                code="5",
                revision_number=5,
                source=sources[2],
                start_date=date(2022, 4, 1),
                end_date=date(2022, 4, 4),
            ),
            # Ticketer:
            Route(
                service=service,
                description="5B",
                service_code="PF0002189:81",
                code="KCTB_5B_KCTBPF0002189815B_20220113_-_1d735f37-ef5d-4b8c-b853-c418e6c48882.xml",
                revision_number=3,
                source=sources[3],
            ),
            Route(
                service=service,
                description="5BH",
                service_code="PF0002189:81",
                code="KCTB_5BH_KCTBPF0002189815B_20220113_-_b282207a-ed52-4cdf-a4e0-17f4bd6b7a55.xml",
                revision_number=5,
                source=sources[3],
            ),
        ]
        Route.objects.bulk_create(routes)

        # maximum revision number for each source
        self.assertEqual(
            list(get_routes(routes[:5], when=date(2022, 4, 4))), routes[1:5]
        )

        # Ticketer filename - treat '5B' and '5BH' despite having the same service_code
        self.assertEqual(
            list(get_routes(routes[5:7], when=date(2022, 4, 4))), routes[5:7]
        )

        # # from_date - include future versions
        # self.assertEqual(
        #     get_routes(routes[2:4], from_date=date(2022, 4, 3)), routes[2:4]
        # )
        # self.assertEqual(
        #     get_routes(routes[2:4], from_date=date(2022, 4, 4)), routes[2:4]
        # )
        # # ignore old versions:
        # self.assertEqual(
        #     get_routes(routes[2:4], from_date=date(2022, 4, 5)), routes[3:4]
        # )

        routes = [
            Route(
                service=service,
                code="6",
                source=sources[0],
                revision_number=171,
                start_date=date(2023, 2, 26),
            ),
            Route(
                service=service,
                code="7",
                source=sources[0],
                revision_number=165,
                start_date=date(2023, 2, 19),
            ),
            Route(
                service=service,
                code="8",
                source=sources[0],
                revision_number=172,
                start_date=date(2023, 3, 5),
            ),
        ]
        Route.objects.bulk_create(routes)

        self.assertQuerySetEqual(
            get_routes(routes, when=date(2023, 2, 22)), routes[1:2]
        )
        self.assertQuerySetEqual(get_routes(routes, when=date(2023, 3, 22)), routes[2:])

        # a soft-deleted route (no service) with a higher revision number
        # should not suppress the current route
        Route.objects.create(
            service=None,
            code="9",
            source=sources[0],
            revision_number=173,
            start_date=date(2023, 3, 5),
        )
        self.assertQuerySetEqual(
            get_routes(routes, when=date(2023, 3, 22)),
            routes[2:],
        )

    def test_get_routes_tfl(self):
        source = DataSource.objects.create(id=1, name="L")
        service = Service.objects.create(slug="bus-34")

        routes = [
            Route(
                service_code="86-683-_-y05-60196",
                code="tfl_86-683-_-y05-60196",
                revision_number=3,
                start_date=date(2023, 2, 11),
                source=source,
                service=service,
            ),
            Route(
                service_code="86-683-_-y05-60197",
                code="tfl_86-683-_-y05-60197",  # highest Service Change Number in filename
                revision_number=3,
                start_date=date(2023, 2, 11),
                source=source,
                service=service,
            ),
            Route(
                service_code="86-683-_-y05-59862",
                code="tfl_86-683-_-y05-59862",
                revision_number=3,
                start_date=date(2023, 2, 11),
                source=source,
                service=service,
            ),
        ]
        Route.objects.bulk_create(routes)

        gotten_routes = get_routes(routes, when=date(2023, 2, 12))
        self.assertEqual(len(gotten_routes), 1)
        self.assertEqual(gotten_routes[0].code, "tfl_86-683-_-y05-60197")
        self.assertEqual(gotten_routes[0], routes[1])

    def test_get_routes_passenger(self):
        tds = TimetableDataSource.objects.create()
        ds = DataSource.objects.create(id=1, name="McGill", source=tds)
        versions = Version.objects.bulk_create(
            [
                Version(
                    source=tds,
                    name="mcgills_1714387585.zip",
                    start_date=date(2024, 5, 6),
                    end_date=date(2024, 5, 7),
                ),
                Version(
                    source=tds,
                    name="mcgills_1714388037.zip",
                    start_date=date(2024, 5, 13),
                    end_date=date(2025, 5, 14),
                ),
                Version(
                    source=tds,
                    name="mcgills_1714389553.zip",
                    start_date=date(2024, 4, 29),
                    end_date=date(2025, 4, 30),
                ),
            ]
        )
        routes = Route.objects.bulk_create(
            [
                Route(
                    version=versions[0],
                    code="mcgills_1714387585.zip/foo.xml",  # starts 2024-05-06
                    source=ds,
                    line_name="1",
                ),
                Route(
                    version=versions[1],
                    code="mcgills_1714388037.zip/foo.xml",  # starts 2024-05-13
                    source=ds,
                    line_name="2",
                ),
                Route(
                    version=versions[2],
                    code="mcgills_1714389553.zip/foo.xml",  # starts 2024-04-29
                    source=ds,
                    line_name="3",
                ),
            ]
        )

        self.assertQuerySetEqual(get_routes(routes, date(2024, 4, 1)), [])
        self.assertQuerySetEqual(get_routes(routes, date(2024, 4, 29)), [routes[2]])
        self.assertQuerySetEqual(get_routes(routes, date(2024, 5, 6)), [routes[0]])
        self.assertQuerySetEqual(get_routes(routes, date(2024, 5, 14)), [routes[1]])

        # two versions covering the same dates - the one with the higher
        # timestamp in its name wins (most recently published .zip)
        extra_versions = Version.objects.bulk_create(
            [
                Version(
                    source=tds,
                    name="mcgills_1714389999.zip",
                    start_date=date(2024, 4, 29),
                    end_date=date(2025, 4, 30),
                ),
            ]
        )
        extra_routes = Route.objects.bulk_create(
            [
                Route(
                    version=extra_versions[0],
                    code="mcgills_1714389999.zip/foo.xml",
                    source=ds,
                    line_name="3",
                ),
            ]
        )
        self.assertQuerySetEqual(
            get_routes(routes + extra_routes, date(2024, 4, 29)), [extra_routes[0]]
        )

    def test_garage(self):
        garage = Garage(code="LOW", name="LOWESTOFT TOWN")
        self.assertEqual(str(garage), "Lowestoft Town")
        garage.name = "LOW"
        self.assertEqual(str(garage), "LOW")
