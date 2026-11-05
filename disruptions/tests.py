from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.test import TestCase

from busstops.models import DataSource, Operator, Region, Service, StopPoint
from bustimes.models import Route, StopTime, Trip
from vehicles.models import Vehicle, VehicleJourney

from .models import AffectedJourney, Call, Consequence, Situation, ValidityPeriod


class DisruptionsTest(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        source = Situation.source.field.remote_field.model.objects.create(
            name="Test", url="http://example.com"
        )
        cls.situation = Situation.objects.create(
            source=source,
            summary="A pigeon got in the cab and bit the driver",
            publication_window=DateTimeTZRange(
                "2021-05-10T09:00:00Z", "2021-05-10T10:00:00Z", "[]"
            ),
        )

    def test_validity_periods_daily(self):
        self.assertEqual(self.situation.list_validity_periods(), [])
        ValidityPeriod.objects.bulk_create(
            [
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-10T09:00:00Z", "2021-05-10T10:00:00Z", "[]"
                    ),
                ),
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-11T09:00:00Z", "2021-05-11T10:00:00Z", "[]"
                    ),
                ),
            ]
        )
        self.assertEqual(
            self.situation.list_validity_periods(),
            [
                "10:00\u2009\u2013\u200911:00, Monday 10\u2009\u2013\u2009Tuesday 11 May 2021"
            ],
        )

    def test_validity_periods_nightly(self):
        self.assertEqual(self.situation.list_validity_periods(), [])
        ValidityPeriod.objects.bulk_create(
            [
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-10T20:00:00Z", "2021-05-11T06:00:00Z", "[]"
                    ),
                ),
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-11T20:00:00Z", "2021-05-12T06:00:00Z", "[]"
                    ),
                ),
            ]
        )
        self.assertEqual(
            self.situation.list_validity_periods(),
            [
                "21:00\u2009\u2013\u200907:00, Monday 10\u2009\u2013\u2009Wednesday 12 May 2021"
            ],
        )

    def test_validity_periods_contiguous(self):
        # one continuous period split into daily chunks,
        # each ending at 23:59 with the next starting 1 minute later at 00:00
        ValidityPeriod.objects.bulk_create(
            [
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-09T23:00:00Z", "2021-05-10T22:59:00Z", "[]"
                    ),
                ),
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-10T23:00:00Z", "2021-05-11T22:59:00Z", "[]"
                    ),
                ),
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-11T23:00:00Z", "2021-05-12T22:59:00Z", "[]"
                    ),
                ),
            ]
        )
        self.assertEqual(
            self.situation.list_validity_periods(),
            ["Monday 10 – Wednesday 12 May 2021"],
        )

    def test_validity_periods_one_night(self):
        self.assertEqual(self.situation.list_validity_periods(), [])
        ValidityPeriod.objects.bulk_create(
            [
                ValidityPeriod(
                    situation=self.situation,
                    period=DateTimeTZRange(
                        "2021-05-10T20:00:00Z", "2021-05-11T06:00:00Z", "[]"
                    ),
                )
            ]
        )
        self.assertEqual(
            self.situation.list_validity_periods(),
            ["Monday 10\u2009\u2013\u2009Tuesday 11 May 2021"],
        )


class DisruptedTimetableTest(TestCase):
    """A stop affected by a situation should be marked with a \u26a0\ufe0f in the timetable."""

    @classmethod
    def setUpTestData(cls) -> None:
        region = Region.objects.create(id="NW", name="North West")
        cls.service = Service.objects.create(
            line_name="156", region=region, current=True
        )

        disrupted_stop = StopPoint.objects.create(
            atco_code="1800DISRUPT", common_name="Disrupted Place", active=True
        )
        StopPoint.objects.create(
            atco_code="1800SAFE", common_name="Safe Place", active=True
        )

        # a (calendar-less) timetable calling at both stops
        source = DataSource.objects.create(name="Test")
        route = Route.objects.create(
            service=cls.service, source=source, line_name="156"
        )
        trip = Trip.objects.create(route=route, start="0", end="3600")
        StopTime.objects.bulk_create(
            [
                StopTime(trip=trip, stop_id="1800DISRUPT", departure="0", sequence=0),
                StopTime(trip=trip, stop_id="1800SAFE", arrival="3600", sequence=1),
            ]
        )

        cls.situation = Situation.objects.create(
            source=source,
            summary="Stop closed",
            publication_window=DateTimeTZRange(
                "2020-01-01T00:00:00Z", "2050-01-01T00:00:00Z", "[]"
            ),
        )
        consequence = Consequence.objects.create(situation=cls.situation)
        consequence.services.add(cls.service)
        consequence.stops.add(disrupted_stop)

        cls.timetable_url = f"/services/{cls.service.id}/timetable"

    def test_disrupted_stop_marked_in_timetable(self):
        response = self.client.get(self.service.get_absolute_url())

        # the disrupted stop has the warning marker
        self.assertContains(response, "\u26a0\ufe0f&#xfe0f; Disrupted Place")
        # the unaffected stop does not
        self.assertContains(response, "Safe Place")
        self.assertNotContains(response, "\u26a0\ufe0f&#xfe0f; Safe Place")

    def test_disrupted_stop_marked_in_timetable_fragment(self):
        # the fragment fetched by JS when changing the date
        response = self.client.get(self.timetable_url)

        self.assertContains(response, "\u26a0\ufe0f&#xfe0f; Disrupted Place")
        self.assertNotContains(response, "\u26a0\ufe0f&#xfe0f; Safe Place")

    def test_operator_situations(self):
        operator = Operator.objects.create(noc="BLUE", name="Blue Buses")
        self.service.operator.add(operator)
        other_service = Service.objects.create(line_name="157", current=True)
        window = self.situation.publication_window

        # applies to all the operator's services
        situation = Situation.objects.create(
            source=self.situation.source,
            summary="Driver shortage",
            publication_window=window,
        )
        Consequence.objects.create(situation=situation).operators.add(operator)

        # applies to the operator, but only to another service
        situation = Situation.objects.create(
            source=self.situation.source,
            summary="Diversion on the 157",
            publication_window=window,
        )
        consequence = Consequence.objects.create(situation=situation)
        consequence.operators.add(operator)
        consequence.services.add(other_service)

        response = self.client.get(self.service.get_absolute_url())
        self.assertContains(response, "Stop closed")
        self.assertContains(response, "Driver shortage")
        self.assertNotContains(response, "Diversion on the 157")

    def test_validity_period_filtering(self):
        # the situation is only in effect for a few days in June 2026
        ValidityPeriod.objects.create(
            situation=self.situation,
            period=DateTimeTZRange(
                "2026-06-10T00:00:00Z", "2026-06-20T00:00:00Z", "[]"
            ),
        )

        # a date within the validity period: stop is marked
        response = self.client.get(f"{self.timetable_url}?date=2026-06-15")
        self.assertContains(response, "\u26a0\ufe0f&#xfe0f; Disrupted Place")

        # a date outside it: not marked
        response = self.client.get(f"{self.timetable_url}?date=2026-06-25")
        self.assertNotContains(response, "\u26a0\ufe0f&#xfe0f; Disrupted Place")
        self.assertContains(response, "Disrupted Place")


class CancelledStopTest(TestCase):
    def test_cancelled_stop_marked_in_journey(self):
        source = DataSource.objects.create(name="BODS cancellations")
        service = Service.objects.create(line_name="17A", current=True)
        StopPoint.objects.bulk_create(
            [
                StopPoint(atco_code="2800S40055A", common_name="Anfield", active=True),
                StopPoint(atco_code="2800S40057B", common_name="Priory", active=True),
            ]
        )
        route = Route.objects.create(service=service, source=source, line_name="17A")
        trip = Trip.objects.create(route=route, start="0", end="3600")
        cancelled, _ = StopTime.objects.bulk_create(
            [
                StopTime(trip=trip, stop_id="2800S40055A", departure="0", sequence=0),
                StopTime(trip=trip, stop_id="2800S40057B", arrival="3600", sequence=1),
            ]
        )
        journey = VehicleJourney.objects.create(
            trip=trip,
            service=service,
            datetime="2026-10-28T23:04:00Z",
            date="2026-10-28",
            vehicle=Vehicle.objects.create(code="80511"),
            source=source,
        )
        situation = Situation.objects.create(source=source)
        affected_journey = AffectedJourney.objects.create(
            situation=situation, trip=trip, date="2026-10-28", condition="altered"
        )
        Call.objects.create(
            journey=affected_journey,
            stop_time=cancelled,
            condition="notStopping",
            order=1,
        )

        response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details.json")
        times = response.json()["trip"]["times"]
        self.assertEqual(times[0]["call_condition"], "notStopping")
        self.assertNotIn("call_condition", times[1])

        # a different day
        affected_journey.date = "2026-10-29"
        affected_journey.save()
        response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details.json")
        self.assertNotIn("call_condition", response.json()["trip"]["times"][0])
