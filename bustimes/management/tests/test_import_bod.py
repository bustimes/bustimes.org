import datetime
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, ANY

import fakeredis
import time_machine
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.test import TestCase, override_settings
from vcr import use_cassette

from accounts.models import User
from busstops.models import (
    AdminArea,
    DataSource,
    Operator,
    OperatorCode,
    Region,
    Service,
    ServiceCode,
    StopArea,
    StopPoint,
)
from vehicles.models import VehicleJourney, VehicleLocation

from ...models import (
    BankHoliday,
    BankHolidayDate,
    CalendarBankHoliday,
    Garage,
    Route,
    RouteLink,
    TimetableDataSource,
    VehicleType,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class ImportBusOpenDataTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        ea = Region.objects.create(pk="EA", name="East Anglia")
        lynx = Operator.objects.create(noc="LYNX", region=ea, name="Lynx")
        scpb = Operator.objects.create(noc="SCCM", region=ea, name="Stagecoach East")
        schu = Operator.objects.create(noc="SCHU", region=ea, name="Huntingdon")
        source = DataSource.objects.create(name="National Operator Codes")
        OperatorCode.objects.bulk_create(
            [
                OperatorCode(operator=lynx, source=source, code="LYNX"),
                OperatorCode(operator=scpb, source=source, code="SCPB"),
                OperatorCode(operator=schu, source=source, code="SCHU"),
                OperatorCode(operator=schu, source=source, code="SCCM"),
            ]
        )
        cls.user = User.objects.create()

    @use_cassette(str(FIXTURES_DIR / "bod_lynx.yaml"))
    @time_machine.travel(datetime.datetime(2020, 5, 1), tick=False)
    def test_import_bod(self):
        admin_area = AdminArea.objects.create(
            id=91, atco_code="290", name="Norfolk", region_id="EA"
        )
        stop_area = StopArea.objects.create(
            id="2900A", active=True, admin_area=admin_area
        )
        StopPoint.objects.bulk_create(
            [
                StopPoint(
                    atco_code="2900w0321",
                    common_name="Lion Store",
                    indicator="opp",
                    admin_area=admin_area,
                    stop_area=stop_area,
                    active=True,
                ),
                StopPoint(
                    atco_code="2900w0322",
                    common_name="Lion Store",
                    indicator="adj",
                    admin_area=admin_area,
                    stop_area=stop_area,
                    active=True,
                ),
                StopPoint(
                    atco_code="2900W0314",
                    common_name="Holt Court",
                    indicator="opp",
                    admin_area=admin_area,
                    stop_area=stop_area,
                    active=True,
                ),
                StopPoint(
                    atco_code="2900K132",
                    common_name="Kings Lynn Transport Interchange",
                    admin_area=admin_area,
                    stop_area=stop_area,
                    active=True,
                ),
            ]
        )

        source = TimetableDataSource.objects.create(name="LYNX", search="LYNX")
        source.operators.add("LYNX")

        with (
            TemporaryDirectory() as directory,
            override_settings(DATA_DIR=Path(directory)),
            patch("busstops.models.DataSource.upload_to_s3_etc") as upload_to_s3_etc,
        ):
            api_key = "0123456789abc19abc190123456789abc19abc19"

            call_command("import_bod_timetables", api_key)

            with self.assertNumQueries(1):
                # no matching operator
                call_command("import_bod_timetables", api_key, "POOP")

            # no changes
            with self.assertNumQueries(6):
                call_command("import_bod_timetables", api_key)

            route = Route.objects.get()

            self.client.force_login(self.user)
            response = self.client.get(route.get_absolute_url())
            self.assertEqual(200, response.status_code)
            self.client.logout()

        upload_to_s3_etc.assert_called_once_with(
            Path(directory) / f"bod/{route.source_id}"
        )

        self.assertEqual(route.source.name, "Lynx_Clenchwarton_54_20200330")
        self.assertEqual(
            route.source.url,
            "https://data.bus-data.dft.gov.uk/timetable/dataset/35/download/",
        )
        self.assertEqual(route.source.sha1, "ed6f5e835884781ea314bc13cc9054592a4c4f96")

        self.assertEqual(route.code, "")
        self.assertEqual(route.service_code, "54")
        with self.assertNumQueries(4):
            response = self.client.get(f"/services/{route.service_id}.json")
        self.assertTrue(response.json()["geometry"])

        # a TicketMachineServiceCode should have been created
        service_code = ServiceCode.objects.get()
        self.assertEqual(service_code.code, "1")
        self.assertEqual(service_code.scheme, "SIRI")

        # PublicUse is false
        self.assertFalse(route.service.public_use)
        response = self.client.get(route.service.get_absolute_url())
        self.assertContains(response, "school or works service")

        self.assertEqual(route.service.colour.background, "#12198B")
        self.assertEqual(route.service.colour.foreground, "#fff")

        # LineColour
        response = self.client.get(f"/services/{route.service_id}/timetable")

        self.assertContains(
            response,
            """
            <tr>
                <th class="stop-name" scope="row">
                    <a href="/stops/2900w0321">Lion Store (opp)</a>
                </th>
                <td>12:19</td>
            </tr>""",
            html=True,
        )

        self.assertContains(
            response,
            """Timetable data from \
<a href="https://data.bus-data.dft.gov.uk/timetable/dataset/35/" rel="nofollow">\
Lynx/Bus Open Data Service (BODS)</a>, <time datetime="2020-04-01">1 April 2020</time>""",
        )

        # test views:

        trip = route.trip_set.first()

        response = self.client.get(f"{trip.get_absolute_url()}/block")
        self.assertEqual(response.status_code, 404)  # no block

        response = self.client.get(f"/api/trips/{trip.id}.json")
        self.assertEqual(27, len(response.json()["times"]))

        response = self.client.get(f"/api/trips/?service={route.service_id}")
        self.assertEqual(4, len(response.json()["results"]))

        response = self.client.get(trip.get_absolute_url())

        self.assertContains(response, "Clenchwarton Post Box")
        self.assertContains(response, "09:33")

        # stop times json
        expected_json = {
            "times": [
                {
                    "id": response.context_data["stops"][0].id,
                    "trip_id": trip.id,
                    "service": {
                        "line_name": "54",
                        "operators": [
                            {
                                "id": "LYNX",
                                "name": "Lynx",
                                "vehicle_mode": "",
                            }
                        ],
                    },
                    "destination": {
                        "atco_code": "2900K132",
                        "name": "Kings Lynn Transport Interchange",
                        "locality": None,
                    },
                    "aimed_arrival_time": None,
                    "aimed_departure_time": "2020-05-01T09:15:00+01:00",
                }
            ]
        }

        with self.assertNumQueries(6):
            response = self.client.get("/stops/2900W0321/times.json")
        self.assertEqual(response.json(), expected_json)

        with self.assertNumQueries(6):
            response = self.client.get(
                "/stops/2900W0321/times.json?when=2020-05-01T09:15:00%2b01:00"
            )
        self.assertEqual(response.json(), expected_json)

        with self.assertNumQueries(6):
            response = self.client.get(
                "/stops/2900W0321/times.json?when=2020-05-01T09:15:00"
            )
        self.assertEqual(response.json(), expected_json)

        with self.assertNumQueries(6):
            response = self.client.get("/stops/2900W0321/times.json?limit=10")
        self.assertEqual(1, len(response.json()["times"]))

        with self.assertNumQueries(1):
            response = self.client.get("/stops/2900W0321/times.json?limit=nine")
        self.assertEqual(400, response.status_code)

        with self.assertNumQueries(1):
            response = self.client.get("/stops/2900W0321/times.json?when=yesterday")
        self.assertEqual(400, response.status_code)

        with self.assertNumQueries(10):
            response = self.client.get("/stops/2900W0321?date=2038-01-19")
            self.assertEqual(str(response.context["when"]), "2038-01-19 00:00:00")
            self.assertEqual(
                str(response.context["departures"][0]["time"]),
                "2038-01-19 09:15:00+00:00",
            )
        # with patch(
        #     "departures.live.NorfolkDepartures.get_departures", return_value=[]
        # ) as mocked:

        with self.assertNumQueries(10):
            response = self.client.get("/stops/2900W0321?date=2020-05-02")
        self.assertEqual(1, len(response.context["departures"]))
        self.assertEqual(str(response.context["when"]), "2020-05-02 00:00:00")

        self.assertContains(response, "Nearby stops")  # other stop in StopArea
        self.assertContains(response, "<small>54</small>")

        with self.assertNumQueries(9):
            response = self.client.get("/stops/2900W0321?date=2020-05-02&time=11:00")
        self.assertEqual(str(response.context["when"]), "2020-05-02 11:00:00")
        self.assertContains(response, '<a href="?date=2020-05-03"')  # next day

        with self.assertNumQueries(10):
            response = self.client.get("/stops/2900w0321/departures?date=poop")
        self.assertEqual(str(response.context["when"]), "2020-05-01 01:00:00+01:00")
        self.assertEqual(len(response.context["departures"]), 1)

        # services with current=False should be excluded
        Service.objects.update(current=False)
        response = self.client.get("/stops/2900w0321/departures")
        self.assertEqual(len(response.context["departures"]), 0)
        Service.objects.update(current=True)

        with self.assertNumQueries(8):
            response = self.client.get("/stations/2900A")
        self.assertEqual(str(response.context["when"]), "2020-05-01 01:00:00+01:00")
        self.assertEqual(len(response.context["departures"]), 3)

        with self.assertNumQueries(8):
            response = self.client.get("/stations/2900A/departures?date=2020-05-02")
        self.assertEqual(str(response.context["when"]), "2020-05-02 00:00:00")

        # mocked.assert_called()

        # test get_trip
        journey = VehicleJourney(
            datetime=datetime.datetime.fromisoformat("2020-11-02T15:07:06+00:00"),
            service=Service.objects.get(),
            code="1",
            source=route.source,
        )
        journey.trip = journey.get_trip()
        self.assertEqual(journey.trip.ticket_machine_code, "1")
        journey.save()  # for use later

        trip = journey.get_trip(journey_code="0915")
        self.assertEqual(trip.ticket_machine_code, "1")

        trip = journey.get_trip(destination_ref="2900K132")
        self.assertEqual(trip.ticket_machine_code, "1")

        with self.assertNumQueries(2):
            trip = journey.get_trip(
                origin_ref="2900K132",
                destination_ref="2900K132",
                operator_ref="TFLO",
                departure_time=journey.datetime,
            )
        journey.code = "0916"
        trip = journey.get_trip()
        self.assertIsNone(trip)

        trip = journey.get_trip(destination_ref="2900K132")
        self.assertIsNone(trip)

        # test trip copy:
        trip = route.trip_set.first()
        trip.copy(datetime.timedelta(hours=1))

        fake_redis = fakeredis.FakeStrictRedis()
        # test journey with trip json
        with (
            patch("vehicles.views.redis_client", fake_redis),
            patch("api.views.redis_client", fake_redis),
        ):
            response = self.client.get(f"/journeys/{journey.id}.json")
            json = response.json()
            self.assertIn("stops", json)
            self.assertNotIn("locations", json)

            # journey locations but no stop locations
            location = VehicleLocation(Point(0.23, 52.729))
            location.journey = journey
            location.datetime = datetime.datetime.fromisoformat(
                "2019-05-29T13:03:34+01:00"
            )

            fake_redis.rpush(*location.get_appendage())

            response = self.client.get(
                f"/services/{journey.service_id}/journeys/{journey.id}.json"
            )
            json = response.json()
            self.assertIn("stops", json)
            self.assertIn("locations", json)

            # journey locations and stop location
            StopPoint.objects.filter(atco_code="2900W0314").update(
                latlong="POINT(0.23 52.729)"
            )
            response = self.client.get(f"/journeys/{journey.id}.json")
            json = response.json()
            self.assertEqual(
                json["stops"][2]["actual_departure_time"], "2019-05-29T13:03:34+01:00"
            )

            # newer API
            response = self.client.get(f"/api/vehiclejourneys/{journey.id}/details/")
            json = response.json()
            self.assertEqual(json["time_aware_polyline"], "o|k@gsy`Ikpyx|{A")
            stop = next(
                t
                for t in json["trip"]["times"]
                if t["stop"]["atco_code"] == "2900W0314"
            )
            self.assertEqual(stop["actual_departure_time"], "2019-05-29T13:03:34+01:00")

    def test_ticketer(self):
        source = TimetableDataSource.objects.create(
            name="Completely Coach Travel",
            region_id="EA",
            url="https://opendata.ticketer.com/uk/Completely_Coach_Travel/routes_and_timetables/current.zip",
        )

        with (
            TemporaryDirectory() as directory,
            override_settings(DATA_DIR=Path(directory)),
        ):
            with (
                use_cassette(str(FIXTURES_DIR / "bod_ticketer.yaml")),
                patch(
                    "busstops.models.DataSource.upload_to_s3_etc"
                ) as upload_to_s3_etc,
            ):
                with self.assertLogs(
                    "bustimes.management.commands.import_transxchange", "WARNING"
                ) as cm:
                    call_command("import_bod_timetables", "ticketer")

                with self.assertNumQueries(2):
                    call_command("import_bod_timetables", "ticketer")  # not modified

                with self.assertNumQueries(1):
                    call_command(
                        "import_bod_timetables", "ticketer", "POOP"
                    )  # no matching operator

            upload_to_s3_etc.assert_called_once()

            source = DataSource.objects.get(name="Completely Coach Travel")
            service = source.service_set.first()
            route = service.route_set.first()

            self.client.force_login(self.user)
            response = self.client.get(route.get_absolute_url())
            self.assertEqual(response.status_code, 200)
            self.client.logout()

        response = self.client.get(service.get_absolute_url())
        self.assertContains(
            response,
            "Timetable data from "
            "https://opendata.ticketer.com/uk/Completely_Coach_Travel/routes_and_timetables/current.zip, "
            """<time datetime="2021-09-24">24 September 2021</time>""",
        )

        self.assertEqual(
            cm.output,
            [
                "WARNING:bustimes.management.commands.import_transxchange:{'NationalOperatorCode': 'CPLT', "
                "'OperatorShortName': 'Completely Coach Travel', 'LicenceNumber': 'PF2024545'}"
            ],
        )

    @time_machine.travel(datetime.datetime(2020, 6, 10))
    def test_import_stagecoach(self):
        source = TimetableDataSource.objects.create(
            name="Stagecoach East",
            region_id="EA",
            url="https://opendata.stagecoachbus.com/stagecoach-sccm-route-schedule-data-transxchange_2_4.zip",
        )
        source.operators.add("SCCM")

        StopPoint.objects.bulk_create(
            [
                StopPoint(
                    atco_code="0500HYAXL033",
                    common_name="Folly Close",
                    indicator="opp",
                    active=True,
                ),
                StopPoint(
                    atco_code="0500HYAXL007",
                    common_name="Motel",
                    indicator="opp",
                    active=True,
                ),
                StopPoint(
                    atco_code="0500HFOLK002",
                    common_name="Folksworth Road",
                    indicator="opp",
                    active=True,
                ),
                StopPoint(
                    atco_code="0500HSTIV054",
                    common_name="St John's Road",
                    indicator="opp",
                    active=True,
                ),
                StopPoint(
                    atco_code="0500HHUNT049",
                    common_name="Church Lane",
                    indicator="near",
                    active=True,
                ),
            ]
        )

        with (
            TemporaryDirectory() as directory,
            override_settings(DATA_DIR=Path(directory)),
        ):
            archive_name = "stagecoach-sccm-route-schedule-data-transxchange_2_4.zip"
            path = Path(directory) / archive_name

            with zipfile.ZipFile(path, "a") as open_zipfile:
                for filename in (
                    "904_FE_PF_904_20210102.xml",
                    "904_VI_PF_904_20200830.xml",
                ):
                    open_zipfile.write(FIXTURES_DIR / filename, filename)

            with patch(
                "bustimes.management.commands.import_bod_timetables.download_if_modified",
                return_value=(
                    True,
                    datetime.datetime.fromisoformat("2020-06-10T12:00:00+01:00"),
                ),
            ) as download_if_modified:
                with self.assertNumQueries(118):
                    call_command("import_bod_timetables", "stagecoach")
                download_if_modified.assert_called_with(
                    path, DataSource.objects.get(name="Stagecoach East"), ANY
                )

                route_links = RouteLink.objects.order_by("id")
                self.assertEqual(len(route_links), 210)
                route_link = route_links[0]
                route_link.geometry = (
                    "SRID=4326;LINESTRING(0 0, 0 0)"  # should be overwritten later
                )
                route_link.save()

                with self.assertNumQueries(6):
                    call_command("import_bod_timetables", "stagecoach")

                with self.assertNumQueries(1):
                    call_command("import_bod_timetables", "stagecoach", "SCOX")

                with self.assertNumQueries(55):
                    call_command("import_bod_timetables", "stagecoach", "SCCM")

                route_link.refresh_from_db()
                self.assertEqual(len(route_link.geometry.coords), 14)

            self.client.force_login(self.user)
            source = DataSource.objects.filter(name="Stagecoach East").first()
            response = self.client.get(f"/sources/{source.id}/routes/")

            self.assertEqual(
                response.text,
                "904_FE_PF_904_20210102.xml\n904_VI_PF_904_20200830.xml",
            )

            route = Route.objects.first()
            response = self.client.get(route.get_absolute_url())
            self.assertEqual(200, response.status_code)
            self.assertEqual("", response.filename)

            self.client.logout()

        self.assertEqual(BankHoliday.objects.count(), 13)
        self.assertEqual(CalendarBankHoliday.objects.count(), 65)
        self.assertEqual(VehicleType.objects.count(), 3)
        self.assertEqual(Garage.objects.count(), 4)

        with self.assertNumQueries(4):
            response = self.client.get(f"/services/{route.service_id}.json")
        self.assertTrue(response.json()["geometry"])

        self.assertEqual(2, Service.objects.count())
        self.assertEqual(2, Route.objects.count())

        self.assertEqual(RouteLink.objects.count(), 210)

        trip = route.trip_set.last()
        response = self.client.get(f"/api/trips/{trip.id}/")
        self.assertTrue(response.json()["times"][8]["track"])

        with self.assertNumQueries(21):
            response = self.client.get("/services/904-huntingdon-peterborough")
        self.assertContains(response, "Possibly similar services")
        self.assertContains(
            response, '<a href="/services/904-huntingdon-peterborough-2">'
        )
        self.assertContains(response, '<a href="/operators/huntingdon">Huntingdon</a>')

        with time_machine.travel("2021-01-11"):
            response = self.client.get("/services/904-huntingdon-peterborough")
        self.assertContains(
            response,
            'Try a previous date like <a href="?date=2021-01-10">Sunday 10 January 2021</a>?',
        )

        BankHolidayDate.objects.create(
            bank_holiday=BankHoliday.objects.get(name="ChristmasDay"), date="2020-12-25"
        )
        with self.assertNumQueries(19):
            response = self.client.get(
                "/services/904-huntingdon-peterborough?date=2020-12-25"
            )
            self.assertContains(response, "Sorry, no journeys found")
