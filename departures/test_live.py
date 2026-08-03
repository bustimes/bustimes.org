# ruff: noqa: DTZ001 - naive datetimes here are only ever compared to each other
"""Tests for live departures"""

from datetime import datetime
from unittest.mock import patch

import time_machine
import vcr
from django.shortcuts import render
from django.test import TestCase, override_settings

from accounts.models import User
from busstops.models import (
    AdminArea,
    DataSource,
    Operator,
    Region,
    Service,
    SIRISource,
    StopPoint,
    StopUsage,
)
from bustimes.models import Calendar, Route, StopTime, Trip
from vehicles.models import Vehicle, VehicleJourney
from vehicles.tasks import log_vehicle_journey

from . import live, sources


class LiveDeparturesTest(TestCase):
    """Tests for live departures"""

    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(id="W", name="Wales")

        cls.london_stop = StopPoint.objects.create(
            pk="490014721F",
            common_name="Wilmot Street",
            locality_centre=False,
            active=True,
        )
        cls.cardiff_stop = StopPoint.objects.create(
            pk="5710WDB48471", common_name="Wood Street", active=True
        )

        cls.yorkshire_stop = StopPoint.objects.create(
            pk="3290YYA00215",
            naptan_code="32900215",
            common_name="Victoria Bar",
            active=True,
        )

        admin_area = AdminArea.objects.create(
            pk=109, atco_code=200, name="Worcestershire", region=cls.region
        )
        siri_source = SIRISource.objects.create(
            name="SPT",
            url="http://worcestershire-rt-http.trapezenovus.co.uk:8080",
            requestor_ref="Traveline_To_Trapeze",
        )
        cls.source = DataSource.objects.create()
        siri_source.admin_areas.add(admin_area)
        cls.worcester_stop = StopPoint.objects.create(
            pk="2000G000106",
            common_name="Crowngate Bus Station",
            locality_centre=False,
            active=True,
            admin_area=admin_area,
        )
        worcester_44 = Service.objects.create(
            service_code="44", line_name="44", region_id="W"
        )
        worcester_44.operator.add(
            Operator.objects.create(noc="FMR", name="First Midland Red", region_id="W")
        )
        StopUsage.objects.create(stop=cls.worcester_stop, service=worcester_44, order=0)

        calendar = Calendar.objects.create(
            mon=True,
            tue=True,
            wed=True,
            thu=True,
            fri=True,
            sat=True,
            sun=True,
            start_date="2019-02-09",
            end_date="2019-02-09",
        )
        worcester_route = Route.objects.create(
            service=worcester_44, start_date="2017-03-04", source=cls.source, code="44"
        )
        cls.trip = Trip.objects.create(
            calendar=calendar,
            route=worcester_route,
            destination=cls.worcester_stop,
            start="0",
            end="11:00:00",
        )
        cls.worcs_stop_time = StopTime.objects.create(
            trip=cls.trip,
            sequence=0,
            arrival="10:54:00",
            departure="10:54:00",
            stop=cls.worcester_stop,
        )
        StopUsage.objects.create(
            stop_id=cls.worcester_stop.pk, service=worcester_44, order=1
        )
        cls.user = User.objects.create(is_staff=True, is_superuser=True)

    def test_abstract(self):
        departures = sources.RemoteDepartures(None, ())
        self.assertRaises(
            NotImplementedError, departures.departures_from_response, None
        )

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    )
    def test_tfl(self):
        """Test the Transport for London live departures source"""

        service = Service.objects.create(
            service_code="tfl_60-8-_-y05",
            line_name="8",
            region_id="W",
        )
        StopUsage.objects.create(
            stop=self.london_stop, service=service, order=1, line_name="8"
        )
        route = Route.objects.create(source=self.source, service=service, line_name="8")
        trip = Trip.objects.create(route=route, start="0", end="1")
        StopTime.objects.create(trip=trip, stop=self.london_stop)

        with (
            override_settings(TFL={}),
            vcr.use_cassette("fixtures/vcr/tfl_arrivals.yaml"),
        ):
            row = sources.TflDepartures(self.london_stop, [service]).get_departures()[0]
        self.assertEqual("Bow Church", row["destination"])
        self.assertEqual(service, row["service"])
        self.assertEqual(2016, row["live"].date().year)
        self.assertEqual(7, row["live"].date().month)
        self.assertEqual(26, row["live"].date().day)

        with vcr.use_cassette("fixtures/vcr/tfl_arrivals.yaml"):
            response = self.client.get("/stops/" + self.london_stop.pk)

        self.assertContains(
            response,
            """
                <table>
                    <thead><tr>
                            <th></th>
                            <th scope="col">To</th>
                            <th scope="col">Expected</th>
                    </tr></thead>
                    <tbody>
                    <tr><td><a href="/services/8">8</a></td><td>Bow Church
                        <div class="vehicle">LTZ1414</div></td>
                        <td><a href="/vehicles/tfl/LTZ1414">18:22</a></td></tr>
                    <tr><td>D3</td><td>Bethnal Green, Chest Hospital
                        <div class="vehicle">LX59AOM</div></td>
                        <td><a href="/vehicles/tfl/LX59AOM">18:23</a></td></tr>
                    <tr><td><a href="/services/8">8</a></td><td>Bow Church
                        <div class="vehicle">LTZ1243</div></td>
                        <td><a href="/vehicles/tfl/LTZ1243">18:26</a></td></tr>
                    <tr><td>388</td><td>Stratford City
                        <div class="vehicle">YR59NPF</div></td>
                        <td><a href="/vehicles/tfl/YR59NPF">18:26</a></td></tr>
                    <tr><td><a href="/services/8">8</a></td><td>Bow Church
                        <div class="vehicle">LTZ1407</div></td>
                        <td><a href="/vehicles/tfl/LTZ1407">18:33</a></td></tr>
                    <tr><td>D3</td><td>Bethnal Green, Chest Hospital
                        <div class="vehicle">LX59AOL</div></td>
                        <td><a href="/vehicles/tfl/LX59AOL">18:33</a></td></tr>
                    <tr><td><a href="/services/8">8</a></td><td>Bow Church
                        <div class="vehicle">LTZ1412</div></td>
                        <td><a href="/vehicles/tfl/LTZ1412">18:37</a></td></tr>
                    <tr><td>388</td><td>Stratford City
                        <div class="vehicle">PF52TFX</div></td>
                        <td><a href="/vehicles/tfl/PF52TFX">18:44</a></td></tr>
                    <tr><td>D3</td><td>Bethnal Green, Chest Hospital
                        <div class="vehicle">LX59AOA</div></td>
                        <td><a href="/vehicles/tfl/LX59AOA">18:44</a></td></tr>
                    <tr><td><a href="/services/8">8</a></td><td>Bow Church
                        <div class="vehicle">LTZ1269</div></td>
                        <td><a href="/vehicles/tfl/LTZ1269">18:44</a></td></tr>
                    <tr><td><a href="/services/8">8</a></td><td>Bow Church
                        <div class="vehicle">LTZ1393</div></td>
                        <td><a href="/vehicles/tfl/LTZ1393">18:49</a></td></tr>
                </tbody></table>
        """,
            html=True,
        )

        # live departures debug view
        self.client.force_login(self.user)
        response = self.client.get(f"/stops/{self.london_stop.pk}/debug")
        self.assertContains(response, "<code>")

    def test_blend(self):
        service = Service(line_name="X98")
        a = [
            {
                "service": "X98",
                "time": datetime(2017, 4, 21, 20, 10),
                "live": datetime(2017, 4, 21, 20, 2),
            }
        ]
        b = [
            {
                "service": service,
                "time": datetime(2017, 4, 21, 20, 10),
                "live": datetime(2017, 4, 21, 20, 5),
            }
        ]

        live.blend(a, b)
        self.assertEqual(
            a,
            [
                {
                    "service": "X98",
                    "time": datetime(2017, 4, 21, 20, 10),
                    "live": datetime(2017, 4, 21, 20, 5),
                }
            ],
        )

        live.blend(b, a)
        self.assertEqual(
            b,
            [
                {
                    "service": service,
                    "time": datetime(2017, 4, 21, 20, 10),
                    "live": datetime(2017, 4, 21, 20, 5),
                }
            ],
        )

    def test_render(self):
        response = render(
            None,
            "departures.html",
            {
                "departures": [
                    {
                        "time": datetime(1994, 5, 4, 11, 53),
                        "service": "X98",
                        "destination": "Bratislava",
                    },
                    {
                        "time": datetime(1994, 5, 7, 11, 53),
                        "service": "9",
                        "destination": "Shilbottle",
                    },
                ],
                "has_scheduled": True,
            },
        )
        self.assertContains(
            response,
            """
                <h3>Wednesday 4 May</h3>

                <table>
                    <thead><tr>
                        <th></th>
                        <th scope="col">To</th>
                        <th scope="col">Scheduled</th>
                    </tr></thead>
                    <tbody>
                    <tr><td>X98</td><td>Bratislava</td><td>11:53</td><td></td></tr>
                </tbody></table>
                <h3>Saturday 7 May</h3>
                <table>
                    <thead><tr>
                        <th></th>
                        <th scope="col">To</th>
                        <th scope="col">Scheduled</th>
                    </tr></thead>
                    <tbody>
                    <tr><td>9</td><td>Shilbottle</td><td>11:53</td><td></td></tr>
                </tbody></table>
        """,
            html=True,
        )

    @patch("departures.live.log_vehicle_journey")
    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    )
    def test_worcestershire(self, mocked_log_vehicle_journey):
        with (
            time_machine.travel("Sat Feb 09 10:45:45 GMT 2019"),
            vcr.use_cassette("fixtures/vcr/worcester.yaml"),
        ):
            with self.assertNumQueries(10):
                response = self.client.get(self.worcester_stop.get_absolute_url())

            self.client.force_login(self.user)
            debug_response = self.client.get(
                f"{self.worcester_stop.get_absolute_url()}/debug"
            )

        trip_url = f"{self.trip.get_absolute_url()}"

        self.assertContains(
            response,
            f"""
            <tr>
                <td>
                    <a href="/services/44">44</a>
                </td>
                <td>Crowngate Bus Station</td>
                <td><a href="{trip_url}">10:54</a></td>
                <td></td>
            </tr>
        """,
            html=True,
        )
        self.assertContains(response, "EVESHAM Bus Station")
        self.assertNotContains(response, "WORCESTER")

        self.assertContains(debug_response, "<code>")

        args = (
            None,
            {
                "LineRef": "X50",
                "DirectionRef": "O",
                "FramedVehicleJourneyRef": {
                    "DataFrameRef": "2019_02_09_311_4560_220",
                    "DatedVehicleJourneyRef": "311_4560_220",
                },
                "OperatorRef": "FMR",
                "OriginRef": "2000G000106",
                "OriginName": "Crowngate Bus Station",
                "DestinationRef": "2000G000400",
                "DestinationName": "EVESHAM Bus Station",
                "OriginAimedDepartureTime": "2019-02-09T12:10:00Z",
                "Monitored": "true",
                "Delay": "PT0M0S",
                "VehicleRef": "FMR-66692",
                "MonitoredCall": {
                    "AimedDepartureTime": "2019-02-09T12:10:00Z",
                    "ExpectedDepartureTime": "2019-02-09T12:10:00Z",
                    "DepartureStatus": "onTime",
                },
            },
            None,
            "EVESHAM Bus Station",
            "SPT",
            "http://worcestershire-rt-http.trapezenovus.co.uk:8080",
            None,
        )

        # test that the task is called
        mocked_log_vehicle_journey.assert_called_with(*args)
        self.assertEqual(0, VehicleJourney.objects.count())

        # test the actual task
        with self.assertNumQueries(18):
            log_vehicle_journey(*args[:-1], self.trip.id)

        with self.assertNumQueries(5):
            log_vehicle_journey(*args[:-1], self.trip.id)

        Vehicle.objects.update(latest_journey=None)

        with self.assertNumQueries(5):
            log_vehicle_journey(*args[:-1], self.trip.id)

        journey = VehicleJourney.objects.get()
        self.assertEqual(journey.vehicle.latest_journey_data, args[1])
        self.assertEqual(journey.trip, self.trip)
        self.assertEqual(str(journey.datetime), "2019-02-09 12:10:00+00:00")

        response = self.client.get(f"{journey.vehicle.get_absolute_url()}/debug")
        self.assertEqual(response.headers["content-type"], "application/json")
