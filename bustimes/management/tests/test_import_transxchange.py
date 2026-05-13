import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import time_machine
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from busstops.models import (
    DataSource,
    Operator,
    OperatorCode,
    Region,
    Service,
    ServiceColour,
    StopPoint,
)
from vehicles.models import Vehicle, VehicleJourney
from vosa.models import Licence, Registration

from ...models import (
    BankHoliday,
    BankHolidayDate,
    Calendar,
    CalendarDate,
    Garage,
    Note,
    Route,
    RouteLink,
    Trip,
)
from ..commands import import_transxchange

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@override_settings(TNDS_DIR=FIXTURES_DIR, ABBREVIATE_HOURLY=True)
class ImportTransXChangeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ea = Region.objects.create(pk="EA", name="L")
        cls.w = Region.objects.create(pk="W", name="Wales")
        cls.gb = Region.objects.create(pk="GB", name="Großbritannien")
        cls.sc = Region.objects.create(pk="S", name="Scotland")
        Region.objects.bulk_create(
            [
                Region(pk="NE", name="North East"),
                Region(pk="NW", name="North West"),
                Region(pk="IM", name="Isle of Man"),
            ]
        )

        cls.fecs = Operator.objects.create(
            noc="FECS", region_id="EA", name="First in Norfolk & Suffolk"
        )
        Operator.objects.create(noc="bus-vannin", region_id="EA", name="Bus Vannin")
        cls.megabus = Operator.objects.create(
            noc="MEGA", region_id="GB", name="Megabus"
        )
        cls.fabd = Operator.objects.create(
            noc="FABD", region_id="S", name="First Aberdeen"
        )

        cls.nocs = DataSource.objects.create(name="National Operator Codes")
        london = DataSource.objects.create(name="L")
        OperatorCode.objects.bulk_create(
            [
                OperatorCode(operator=cls.megabus, source=cls.nocs, code="MEGA"),
                OperatorCode(operator=cls.fabd, source=cls.nocs, code="FABD"),
                OperatorCode(operator=cls.fabd, source=cls.nocs, code="SDVN"),
                OperatorCode(operator=cls.fabd, source=cls.nocs, code="CBNL"),
                OperatorCode(operator=cls.fabd, source=london, code="LC"),
                OperatorCode(operator=cls.fabd, source=london, code="BE"),
            ]
        )

        Operator.objects.bulk_create(
            [
                Operator(noc="MWLM", name="Mid Wales Motorways"),
                Operator(noc="WHIP", name="Whippet Coaches"),
                Operator(noc="GRYC", name="Grayscroft Coaches"),
                Operator(noc="MHCO", name="M & H Coaches"),
                Operator(noc="FSRV", name="Faresaver"),
                Operator(noc="FESX", name="First in Essex"),
                Operator(noc="SCMY", name="Stagecoach in Lancashire"),
                Operator(noc="ADER", name="Arriva Derby"),
                Operator(noc="EYMS", name="EYMS"),
                Operator(noc="DAGC", name="D&G Bus"),
                Operator(noc="PLYC", name="Plymouth Citybus"),
                Operator(noc="FCWL", name="First Kernow"),
            ]
        )

        StopPoint.objects.bulk_create(
            StopPoint(
                atco_code=atco_code,
                locality_centre=False,
                active=True,
                common_name=common_name,
                indicator=indicator,
                latlong=Point(lng, lat, srid=4326),
            )
            for atco_code, common_name, indicator, lng, lat in (
                ("639004572", "Bulls Head", "adj", -2.5042125060, 53.7423055225),
                ("639004562", "Markham Road", 'by"', -2.5083672338, 53.7398252112),
                ("639004554", "Witton Park", "opp", -2.5108434749, 53.7389877672),
                ("639004592", "Cloverleaf Grange", "", -2.5108434749, 53.7389877672),
                ("639004552", "The Griffin", "adj", -2.4989239373, 53.7425523688),
                ("049004705400", "Kingston District Centre", "o/s", 0, 0),
                ("1000DDDV4248", "Dinting Value Works", "", 0, 0),
                ("2900A181", "", "", 0, 0),
                ("090079682980", "Victoria Road", "", 0, 0),
                ("090079680705", "Booths", "", 0, 0),
                ("260006514a", "Sports Ground", "opp", -1.122736635, 52.668973839),
                # to test handling of leading zero missing in TxC data:
                ("0260006515", "Acorn Close", "adj", -1.121080085, 52.671200066),
                ("0260006516", "Church Hill", "opp", -1.121200186, 52.673322583),
                # excel split reg
                ("0500FWISH025", "Wisbech Bus Station", "", 0.161173, 52.666239),
                ("0590PQG10", "Peterborough Bus Station", "", -0.246691, 52.57392),
            )
        )
        cls.user = User.objects.create()

    @staticmethod
    def handle_files(archive_name, filenames):
        command = import_transxchange.Command()
        command.set_up()
        command.service_ids = set()
        command.route_ids = set()
        command.open_data_operators = set()
        command.incomplete_operators = set()

        command.set_region(archive_name)
        command.source.datetime = timezone.now()
        for filename in filenames:
            path = FIXTURES_DIR / filename
            with open(path, "rb") as open_file:
                command.handle_file(open_file, filename)
        command.finish_services()

    @classmethod
    def write_files_to_zipfile_and_import(cls, zipfile_name, filenames):
        with TemporaryDirectory() as directory:
            zipfile_path = Path(directory) / zipfile_name
            with zipfile.ZipFile(zipfile_path, "a") as open_zipfile:
                for filename in filenames:
                    cls.write_file_to_zipfile(open_zipfile, filename)
            call_command("import_transxchange", zipfile_path)

    @staticmethod
    def write_file_to_zipfile(open_zipfile, filename, arcname=None):
        open_zipfile.write(FIXTURES_DIR / filename, arcname=arcname or filename)

    @time_machine.travel("3 October 2016")
    def test_east_anglia(self):
        self.handle_files("EA.zip", ["ea_20-12-_-y08-1.xml", "ea_21-13B-B-y08-1.xml"])

        route = Route.objects.get(line_name="12")
        self.assertEqual("12", route.service.line_name)

        res = self.client.get(route.service.get_absolute_url())
        timetable = res.context_data["timetable"]
        self.assertEqual(1, len(timetable.groupings))
        self.assertEqual(21, len(timetable.groupings[0].rows))
        self.assertEqual(3, len(timetable.groupings[0].rows[0].times))
        self.assertEqual(3, timetable.groupings[0].rows[0].times[1].colspan)
        self.assertEqual(21, timetable.groupings[0].rows[0].times[1].rowspan)
        self.assertEqual(2, len(timetable.groupings[0].rows[1].times))
        self.assertEqual(2, len(timetable.groupings[0].rows[20].times))

        # Test operating profile days of non operation
        res = self.client.get(route.service.get_absolute_url() + "?date=2016-12-28")
        timetable = res.context_data["timetable"]
        self.assertEqual(0, len(timetable.groupings))

        # Test bank holiday non operation (Boxing Day)
        res = self.client.get(route.service.get_absolute_url() + "?date=2016-12-28")
        timetable = res.context_data["timetable"]
        self.assertEqual(0, len(timetable.groupings))

        #    __     _____     ______
        #   /  |   / ___ \   | ___  \
        #  /_  |   \/   \ \  | |  \  |
        #    | |       _/ /  | |__/  /
        #    | |      |_  |  | ___  |
        #    | |        \ \  | |  \  \
        #    | |   /\___/ /  | |__/  |
        #   /___\  \_____/   |______/

        route = Route.objects.get(line_name="13B", line_brand="Turquoise Line")

        self.assertEqual(75, Trip.objects.count())
        self.assertEqual(9, Calendar.objects.count())
        self.assertEqual(8, CalendarDate.objects.count())

        self.assertEqual(
            str(route), "13B – Turquoise Line – Norwich - Wymondham - Attleborough"
        )
        self.assertEqual(route.line_name, "13B")
        self.assertEqual(route.line_brand, "Turquoise Line")
        self.assertEqual(route.start_date, date(2016, 4, 18))
        self.assertEqual(route.end_date, date(2016, 10, 21))

        service = route.service

        self.assertEqual(
            str(service), "13B - Turquoise Line - Norwich - Wymondham - Attleborough"
        )
        self.assertEqual(service.line_name, "13B")
        self.assertEqual(service.line_brand, "Turquoise Line")
        self.assertTrue(service.current)
        self.assertEqual(service.operator.first(), self.fecs)

        res = self.client.get(service.get_absolute_url())
        self.assertEqual(res.context_data["breadcrumb"], [self.ea, self.fecs])
        self.assertContains(res, "Ivy Road - Queens Square")
        self.assertContains(res, "Queens Square - Ivy Road")
        self.assertContains(res, "<td>19:48</td><td>22:56</td>")
        self.assertContains(
            res, '<option selected value="2016-10-03">Monday 3 October 2016</option>'
        )

        res = self.client.get(service.get_absolute_url())
        self.assertContains(
            res, '<option selected value="2016-10-03">Monday 3 October 2016</option>'
        )
        self.assertContains(res, "<td>19:47</td><td>22:55</td>")

        # test caching
        url = service.get_absolute_url() + "?date=2016-10-16"
        with override_settings(
            CACHES={
                "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
            }
        ):
            with self.assertNumQueries(19):
                res = self.client.get(url)

            with self.assertNumQueries(15):
                res_2 = self.client.get(url)

        self.assertEqual(res.text, res_2.text)

        timetable = res.context_data["timetable"]

        self.assertEqual("Ivy Road - Queens Square", str(timetable.groupings[0]))
        self.assertEqual("Queens Square - Ivy Road", str(timetable.groupings[1]))

        self.assertTrue(timetable.groupings[1].has_minor_stops())
        self.assertTrue(timetable.groupings[0].has_minor_stops())

        self.assertEqual(87, len(timetable.groupings[1].rows))
        self.assertEqual(91, len(timetable.groupings[0].rows))

        self.assertEqual(5, len(timetable.groupings[1].rows[0].times))
        self.assertEqual(4, len(timetable.groupings[0].rows[0].times))

        self.assertEqual("", timetable.groupings[1].rows[0].times[-1])

        # Test the fallback version without a timetable (just a list of stops)
        service.route_set.all().delete()
        res = self.client.get(service.get_absolute_url() + "?date=2020-01-01")
        self.assertContains(
            res,
            """
            <li>
                <a href="/stops/2900A181"></a>
            </li>""",
        )
        self.assertContains(res, "Norwich - Wymondham - Attleborough")
        # self.assertContains(res, 'Attleborough - Wymondham - Norwich')

        res = self.client.get(f"/services/{service.id}/timetable?date=2020-01-01")
        self.assertContains(res, "Sorry, no journeys found")

    @time_machine.travel("30 October 2016")
    def test_service_with_empty_pattern(self):
        self.handle_files("EA.zip", ["swe_33-9A-A-y10-2.xml"])

        route = Route.objects.get(line_name="9A")
        self.assertEqual("9A – Sidwell Street - Marine Place", str(route))

        res = self.client.get(route.service.get_absolute_url() + "?date=2016-12-28")
        timetable = res.context_data["timetable"]
        self.assertEqual(timetable.date, date(2016, 12, 28))

        self.assertEqual([], timetable.groupings)

        self.assertEqual(157, route.service.stopusage_set.count())

    @time_machine.travel("23 January 2017")
    def test_do_service_wales(self):
        """Test a timetable from Wales (with SequenceNumbers on Journeys),
        with a university ServicedOrganisation
        """
        self.handle_files("W.zip", ["CGAO305.xml"])

        service = Service.objects.get(service_code="CGAO305")

        service_code = service.servicecode_set.first()
        self.assertEqual(service_code.scheme, "Traveline Cymru")
        self.assertEqual(service_code.code, "305MFMWA1")

        response = self.client.get(service.get_absolute_url() + "?date=2017-01-23")
        timetable = response.context_data["timetable"]
        self.assertEqual("2017-01-23", str(timetable.date))
        # self.assertEqual(0, len(timetable.groupings))

        self.assertContains(
            response,
            'data from <a href="https://www.travelinedata.org.uk/" rel="nofollow">'
            "the Traveline National Dataset (TNDS)</a>",
        )

        self.assertEqual(
            response.context_data["links"],
            [
                {
                    "url": "https://www.traveline.cymru/timetables/?routeNum=305&direction_id=0&timetable_key=305MFMWA1",
                    "text": "Timetable on the Traveline Cymru website",
                }
            ],
        )

        response = self.client.get(service.get_absolute_url() + "/debug")
        self.assertContains(response, "2017-04-12–2017-05-30")

        timetable = service.get_timetable(date(2017, 4, 20)).render()
        self.assertEqual("2017-04-20", str(timetable.date))
        self.assertEqual(1, len(timetable.groupings))
        self.assertEqual(3, len(timetable.groupings[0].rows[0].times))

        self.assertEqual(18, service.stopusage_set.count())

    @time_machine.travel("2016-12-15")
    def test_timetable_ne(self):
        """Test timetable with some abbreviations and a missing leading 0 in an ATCO code"""

        with self.assertLogs(
            "bustimes.management.commands.import_transxchange", "WARNING"
        ) as cm:
            self.handle_files("NE.zip", ["NE_03_SCC_X6_1.xml"])

        self.assertEqual(
            cm.output,
            [
                "WARNING:bustimes.management.commands.import_transxchange:"
                "90079682980 090079682980",
                "WARNING:bustimes.management.commands.import_transxchange:"
                "{'NationalOperatorCode': 'SCMB', 'OperatorCode': 'SCC', 'OperatorShortName': 'SCC'}",
            ],
        )

        service = Service.objects.get()
        response = self.client.get(service.get_absolute_url())
        timetable = response.context_data["timetable"]

        self.assertContains(response, "Kendal - Barrow-in-Furness")

        self.assertEqual("2016-12-15", str(timetable.date))

        self.assertEqual(
            str(timetable.groupings[0].rows[0].times[:3]), "[07:00, 08:00, 09:00]"
        )
        self.assertEqual(
            str(timetable.groupings[1].rows[0].times[:3]), "[05:20, 06:20, 07:15]"
        )

        # Test abbreviations (check the colspan and rowspan attributes of Cells)
        self.assertEqual(timetable.groupings[0].rows[0].times[3].colspan, 6)
        # self.assertEqual(timetable.groupings[1].rows[0].times[3].rowspan, 104)
        self.assertFalse(timetable.groupings[0].rows[43].has_waittimes)
        # self.assertTrue(timetable.groupings[1].rows[44].has_waittimes)
        # self.assertFalse(timetable.groupings[1].rows[45].has_waittimes)
        (
            self.assertEqual(
                str(timetable.groupings[1].rows[0].times[:6]),
                "[05:20, 06:20, 07:15, 08:10, 09:10, 10:10]",
            ),
        )

        self.assertEqual(149, service.stopusage_set.order_by().distinct("stop").count())
        # (.distinct() because of flaky varying/nondeterministic number of StopUsages)

    @time_machine.travel("2021-03-25")
    def test_delaine_101(self):
        """Test timetable with some batshit year 2099 dates"""

        with self.assertLogs(
            "bustimes.management.commands.import_transxchange", "WARNING"
        ) as cm:
            self.handle_files("EA.zip", ["lincs_DELA_101_13101_.xml"])
        prefix = "WARNING:bustimes.management.commands.import_transxchange"
        self.assertEqual(
            cm.output,
            [
                prefix
                + ":{'NationalOperatorCode': 'DELA', 'OperatorCode': 'DELA', 'OperatorShortName': 'Delaine Buses'}",
                prefix + ":2021-04-19 to 2021-05-28 is 39 days long",
                prefix + ":2021-06-07 to 2021-07-21 is 44 days long",
            ],
        )

        service = Service.objects.get()
        timetable = service.get_timetable().render()

        self.assertEqual(14, len(timetable.date_options))
        self.assertEqual(
            4, CalendarDate.objects.filter(operation=True, special=True).count()
        )
        self.assertEqual(
            0, CalendarDate.objects.filter(operation=True, special=False).count()
        )
        self.assertEqual(19, CalendarDate.objects.filter(operation=False).count())

    @time_machine.travel("2021-04-03")
    def test_other_public_holiday(self):
        """Test timetable with an OtherPublicHoliday and a BODS profile compliant ServiceCode"""

        lic = Licence.objects.create(
            licence_number="PF0007024", discs=0, authorised_discs=0
        )
        Registration.objects.create(
            licence=lic,
            registration_number="PF0007024/15",
            registered=True,
            service_number="69",
            start_point="Ham",
            finish_point="Sandwich",
        )

        self.handle_files("EA.zip", ["Grayscroft Coaches_Mablethorpe_28_20210419.xml"])

        service = Service.objects.get()
        self.assertEqual(service.service_code, "PF0007024:15:28")
        self.assertEqual(
            5, CalendarDate.objects.filter(summary="Christmas week").count()
        )
        self.assertEqual(
            1, CalendarDate.objects.filter(summary="Christmas Week").count()
        )
        self.assertIsNone(service.public_use)

        response = self.client.get(f"{service.get_absolute_url()}/debug")
        self.assertContains(response, "not 2021-12-31 (Christmas week)")

        trip = Trip.objects.first()
        self.assertEqual("09:02", str(trip))
        self.assertTrue(trip.route.public_use)

        # test matching to traffic commissioner (VOSA) registration
        response = self.client.get(service.get_absolute_url())
        self.assertContains(
            response, '<li><a href="/registrations/PF0007024/15">PF0007024/15</a>'
        )

        response = self.client.get("/registrations/PF0007024/15")
        self.assertContains(response, "Timetables")
        self.assertContains(response, service.get_absolute_url())

    @time_machine.travel("2017-08-29")
    def test_timetable_abbreviations_notes(self):
        """Test a timetable with a note which should determine the bounds of an abbreviation"""

        self.handle_files("EA.zip", ["set_5-28-A-y08.xml"])
        service = Service.objects.get()
        response = self.client.get(service.get_absolute_url())
        timetable = response.context_data["timetable"]
        self.assertEqual("2017-08-29", str(timetable.date))

        grouping = timetable.groupings[1]
        self.assertEqual(
            str(grouping.rows[0].times[:16]),
            "[05:06, '', 05:33, '', '', 06:08, 06:29, 06:46, 06:58, 07:21, 07:35, 07:44, 07:59, 08:14, 08:49, 09:07]",
        )
        self.assertEqual(str(grouping.rows[0].times[17]), "then every 20 minutes until")
        self.assertEqual(grouping.rows[0].times[17].colspan, 17)
        self.assertEqual(grouping.rows[0].times[17].rowspan, 60)
        self.assertEqual(
            str(timetable.groupings[1].rows[0].times[18:22]),
            "[15:33, 15:53, 15:58, 16:13]",
        )
        self.assertEqual(str(grouping.rows[0].times[22]), "then every 20 minutes until")
        self.assertEqual(grouping.rows[0].times[22].colspan, 2)
        self.assertEqual(grouping.rows[0].times[22].rowspan, 60)
        self.assertEqual(
            str(grouping.rows[0].times[23:]),
            "[17:13, 17:29, 17:59, 18:07, 18:44, 19:54]",
        )

        self.assertEqual(
            str(grouping.rows[1].times[15:20]), "[09:08, 09:34, 15:34, 15:54, 15:59]"
        )
        self.assertEqual(
            str(grouping.rows[11].times[15:20]), "[09:24, 09:47, 15:47, 16:07, 16:12]"
        )

        feet = list(grouping.column_feet.values())[0]
        self.assertEqual(feet[0].span, 9)
        self.assertEqual(feet[1].span, 2)
        self.assertEqual(feet[2].span, 24)
        self.assertEqual(feet[3].span, 1)
        self.assertEqual(feet[4].span, 10)

        self.assertEqual(131, service.stopusage_set.count())

    @time_machine.travel("2017-12-10")
    def test_timetable_derby_alvaston_circular(self):
        """Test a weird timetable where 'Wilmorton Ascot Drive' is visited twice consecutively on on one journey"""

        self.handle_files("EA.zip", ["em_11-1-J-y08-1.xml"])
        service = Service.objects.get()
        timetable = service.get_timetable().render()
        self.assertEqual("2017-12-10", str(timetable.date))

        self.assertEqual(
            "Wilmorton Ascot Drive (Adj)",
            timetable.groupings[0].rows[49].stop.common_name,
        )
        self.assertEqual(
            "Wilmorton Ascot Drive (Adj)",
            timetable.groupings[0].rows[50].stop.common_name,
        )
        self.assertEqual(60, len(timetable.groupings[0].rows))

    @time_machine.travel("2017-04-13")
    def test_timetable_deadruns(self):
        """Test a timetable with some dead runs which should be respected"""

        self.handle_files("NE.zip", ["SVRLABO024A.xml"])
        service = Service.objects.get()
        response = self.client.get(service.get_absolute_url())
        timetable = response.context_data["timetable"]
        self.assertEqual("2017-04-13", str(timetable.date))

        outbound, inbound = timetable.groupings

        self.assertEqual(len(inbound.rows), 49)
        self.assertEqual(len(outbound.rows), 29)

        self.assertEqual(str(inbound.rows[0].times), "[19:12, '', '']")
        self.assertEqual(str(inbound.rows[21].times), "[19:29, '', '']")
        self.assertEqual(str(inbound.rows[22].times), "[19:30, 21:00, 22:30]")
        self.assertEqual(str(inbound.rows[-25].times), "[19:32, 21:02, 22:32]")
        self.assertEqual(str(inbound.rows[-24].times), "[19:33, 21:03, 22:33]")
        self.assertEqual(str(inbound.rows[-12].times), "[19:42, 21:12, 22:42]")
        self.assertEqual(str(inbound.rows[-8].times), "[19:47, 21:17, 22:47]")
        self.assertEqual(str(inbound.rows[-7].times), "[19:48, 21:18, 22:48]")
        self.assertEqual(str(inbound.rows[-1].times), "[19:53, 21:23, 22:53]")

        self.assertEqual(str(outbound.rows[0].times), "[20:35, 22:05, 23:30]")
        self.assertEqual(str(outbound.rows[-25].times), "[20:38, 22:08, 23:33]")
        self.assertEqual(str(outbound.rows[-24].times), "[20:39, 22:09, 23:34]")
        self.assertEqual(str(outbound.rows[-12].times), "[20:52, 22:22, 23:47]")
        self.assertEqual(str(outbound.rows[-8].times), "[20:55, 22:25, 23:50]")
        self.assertEqual(str(outbound.rows[-7].times), "[20:55, 22:25, 23:50]")
        self.assertEqual(str(outbound.rows[-1].times), "[20:58, 22:28, 23:53]")

        self.assertEqual(str(inbound.rows[-3].stop), "Eldon Street")
        self.assertEqual(str(inbound.rows[-2].stop), "Railway Station")
        self.assertEqual(str(inbound.rows[-1].stop), "Bus Station")
        self.assertEqual(str(outbound.rows[-3].stop), "Twist Moor Lane")
        self.assertEqual(str(outbound.rows[-2].stop), "Gladstone Terrace")
        self.assertEqual(str(outbound.rows[-1].stop), "Hare and Hounds")

        timetable = service.get_timetable(date(2017, 4, 16)).render()
        outbound = timetable.groupings[0]
        self.assertEqual("2017-04-16", str(timetable.date))
        self.assertEqual(
            str(outbound.rows[0].times[2:]), "[15:28, 16:27, 17:28, 18:28, 19:28]"
        )
        self.assertEqual(str(outbound.rows[-26].times[-1]), "19:50")
        self.assertEqual(str(outbound.rows[-25].times[-1]), "19:51")
        self.assertEqual(str(outbound.rows[-24].times[-1]), "")
        self.assertEqual(str(outbound.rows[-23].times[-1]), "")
        self.assertEqual(str(outbound.rows[-6].times[-1]), "")
        self.assertEqual(str(outbound.rows[-5].times[-1]), "")
        self.assertEqual(str(outbound.rows[-4].times[-1]), "")
        self.assertEqual(str(outbound.rows[-3].times[2:]), "[17:04, 18:05, 19:05, '']")
        self.assertEqual(str(outbound.rows[-2].times[2:]), "[17:04, 18:05, 19:05, '']")
        self.assertEqual(str(outbound.rows[-1].times[2:]), "[17:06, 18:07, 19:07, '']")

        self.assertEqual(102, service.stopusage_set.count())

        # Several journeys a day on bank holidays
        BankHolidayDate.objects.create(
            bank_holiday=BankHoliday.objects.get(name="AllBankHolidays"),
            date="2017-04-14",
        )
        timetable = service.get_timetable(date(2017, 4, 14)).render()
        self.assertEqual(7, len(timetable.groupings[0].rows[0].times))

    @time_machine.travel("2017-08-30")
    def test_timetable_servicedorg(self):
        """Test a timetable with a ServicedOrganisation"""

        self.handle_files("EA.zip", ["swe_34-95-A-y10.xml"])
        service = Service.objects.get()

        # Doesn't stop at Budehaven School during holidays
        response = self.client.get(service.get_absolute_url())
        self.assertEqual("2017-08-30", str(response.context_data["timetable"].date))
        self.assertNotContains(response, "Budehaven School")

        # Does stop at Budehaven School twice a day on school days
        response = self.client.get(service.get_absolute_url() + "?date=2017-09-13")
        timetable = response.context_data["timetable"]
        self.assertEqual("2017-09-13", str(timetable.date))
        self.assertContains(response, "Budehaven School")
        rows = timetable.groupings[0].rows
        self.assertEqual(str(rows[-6].times), "[08:32, '', '', '', 15:29, '']")
        self.assertEqual(str(rows[-5].times), "[08:33, '', '', '', 15:30, '']")
        self.assertEqual(str(rows[-4].times), "[08:33, '', '', '', 15:30, '']")

        self.assertEqual(114, service.stopusage_set.count())

    @time_machine.travel("2017-01-23")
    def test_timetable_holidays_only(self):
        """Test a service with a HolidaysOnly operating profile"""
        self.handle_files("EA.zip", ["twm_6-14B-_-y11-1.xml"])
        service = Service.objects.get()

        calendar_date = CalendarDate.objects.get()
        self.assertEqual(calendar_date.summary, "Early May Bank Holiday")

        response = self.client.get(service.get_absolute_url())
        self.assertEqual([], response.context_data["timetable"].groupings)

        BankHolidayDate.objects.create(
            bank_holiday=BankHoliday.objects.get(name="SpringBank"),
            date="2017-05-15",  # not a real bank holiday - this is a test
        )

        # Has some journeys that operate on 1 May 2017
        with time_machine.travel(date(2017, 4, 28)):
            response = self.client.get(service.get_absolute_url())
        timetable = response.context_data["timetable"]
        self.assertEqual(timetable.date, date(2017, 5, 1))
        self.assertEqual(8, len(timetable.groupings[0].rows[0].times))
        self.assertEqual(8, len(timetable.groupings[1].rows[0].times))

        # BankHolidayDate
        self.assertContains(
            response, '<option value="2017-05-15">Monday 15 May 2017</option>'
        )

        self.client.force_login(self.user)
        response = self.client.get(f"{service.get_absolute_url()}/debug")
        self.assertContains(response, "SpringBank")
        self.assertContains(response, "GoodFriday")

    @time_machine.travel("2012-06-27")
    def test_timetable_goole(self):
        self.handle_files("W.zip", ["SVRYEAGT00.xml"])
        service = Service.objects.get()

        # try date outside of operating period:
        response = self.client.get(service.get_absolute_url() + "?date=2007-06-27")
        self.assertRedirects(response, service.get_absolute_url(), status_code=301)

        # should show next day of operation:
        response = self.client.get(service.get_absolute_url())
        timetable = response.context_data["timetable"]
        self.assertEqual("2012-06-30", str(timetable.date))
        # self.assertIsNone(timetable.date)
        self.assertEqual(1, len(timetable.groupings))

        response = self.client.get(service.get_absolute_url() + "?date=2017-01-27")
        timetable = response.context_data["timetable"]
        self.assertEqual("2017-01-27", str(timetable.date))

        lines = timetable.groupings[0].txt().splitlines()
        self.assertEqual(
            lines[0],
            "Goole Interchange                        "
            "09:48  10:28  11:08  11:48  12:28  13:08  13:48  14:28         15:08       ",
        )
        self.assertEqual(
            lines[9],
            "Goole North Street         "
            "08:57  09:37  10:17  10:57  11:37  12:17  12:57  13:37  14:17  14:57  15:45              ",
        )

        timetable.today = date(2016, 2, 21)  # Sunday
        date_options = list(timetable.get_date_options())
        self.assertEqual(date_options[0], date(2016, 2, 22))  # Monday
        self.assertEqual(date_options[-1], date(2017, 1, 27))

        self.assertEqual(34, service.stopusage_set.count())

    @time_machine.travel("2018-09-24")
    def test_timetable_plymouth(self):
        self.handle_files("EA.zip", ["20-plymouth-city-centre-plympton.xml"])
        service = Service.objects.get()
        response = self.client.get(service.get_absolute_url())
        timetable = response.context_data["timetable"]

        self.assertEqual(
            str(timetable.groupings[1].rows[0].stop), "Plympton Mudge Way (NW-bound)"
        )
        # self.assertEqual(str(timetable.groupings[1].rows[1].stop), "Plympton St Mary's Bridge")
        self.assertEqual(
            str(timetable.groupings[1].rows[1].stop),
            "Underwood (Plymouth) Old Priory Junior School (N",
        )
        # self.assertEqual(str(timetable.groupings[1].rows[2].stop), "Plympton Priory Junior School")
        self.assertEqual(
            str(timetable.groupings[1].rows[2].stop),
            "Plympton St Mary's Church (NW-bound)",
        )
        # self.assertEqual(str(timetable.groupings[1].rows[3].stop), "Plympton Dark Street Lane")
        self.assertEqual(
            str(timetable.groupings[1].rows[3].stop),
            "Plympton Colebrook Tunnel (NE-bound)",
        )
        self.assertEqual(
            str(timetable.groupings[1].rows[4].stop),
            "Plympton Glenside Surgey (E-bound)",
        )
        self.assertFalse(timetable.groupings[1].rows[3].has_waittimes)
        # self.assertTrue(timetable.groupings[1].rows[4].has_waittimes)
        self.assertFalse(timetable.groupings[1].rows[5].has_waittimes)
        self.assertFalse(timetable.groupings[1].rows[6].has_waittimes)

        self.assertEqual(74, service.stopusage_set.count())

        route = service.route_set.get()

        self.client.force_login(self.user)
        response = self.client.get(service.get_absolute_url() + "/debug")
        self.assertContains(response, route.get_absolute_url())

        response = self.client.get("/sources")
        self.assertContains(response, "EA.zip")

        response = self.client.get(f"/sources/{service.source_id}")
        self.assertContains(response, "32-20-_-y10-1")

        # test route_xml
        with (
            TemporaryDirectory() as data_dir,
            override_settings(DATA_DIR=Path(data_dir)),
        ):
            response = self.client.get(route.get_absolute_url())
            self.assertEqual(response.status_code, 404)

            service.source.datetime = "2025-01-01 00:00:00Z"
            service.source.save()

            zipfile_path = Path(data_dir) / "TNDS" / "EA.zip"

            # instead of downloading from S3, make our own zipfile
            def side_effect(Bucket, Key, Filename):
                with zipfile.ZipFile(zipfile_path, "a") as open_zipfile:
                    self.write_file_to_zipfile(open_zipfile, route.code)

            with patch("boto3.client") as boto3_client:
                boto3_client.return_value.download_file.side_effect = side_effect

                response = self.client.get(route.get_absolute_url())
                self.assertEqual(response.headers["content-type"], "text/plain")

                response = self.client.get(f"/sources/{route.source_id}/routes/")
                self.assertContains(response, route.code)

                response = self.client.get(f"/sources/{route.source_id}/routes/404")
                self.assertEqual(response.status_code, 404)

    def test_multiple_operators(self):
        """
        file has two Operators (SBLB and and BAIN) but only one operates any journeys
        """

        with (
            self.assertLogs(
                "bustimes.management.commands.import_transxchange", "WARNING"
            ) as cm,
            time_machine.travel(1582385679),
        ):
            self.write_files_to_zipfile_and_import("EA.zip", ["SVRABAO421.xml"])
        service = Service.objects.get()
        self.assertTrue(service.current)

        self.assertEqual(
            [
                "WARNING:bustimes.management.commands.import_transxchange:{'NationalOperatorCode': 'SBLB', "
                "'OperatorCode': 'BLB', 'OperatorShortName': 'Stagecoach North Scotlan'}"
            ],
            cm.output,
        )

        service.slug = "abao421"
        service.save(update_fields=["slug"])

        self.assertEqual(service.slug, "abao421")

        with time_machine.travel("1 January 2022"):
            response = self.client.get("/services/abao421")
            self.assertContains(response, "Saturdays until Saturday 14 August 2021")

        # after operating period
        with self.assertLogs(
            "bustimes.management.commands.import_transxchange", "WARNING"
        ) as cm:
            with patch("os.path.getmtime", return_value=1645544079):
                self.write_files_to_zipfile_and_import("EA.zip", ["SVRABAO421.xml"])
            self.assertEqual(
                cm.output[0],
                "WARNING:bustimes.management.commands.import_transxchange:"
                "SVRABAO421.xml: ABAO421 end 2021-08-19 is in the past",
            )

    def test_multiple_services(self):
        with patch("os.path.getmtime", return_value=1582385679):
            self.handle_files("IOM.zip", ["Ser 16 16A 16B.xml"])
        services = Service.objects.filter(region="IM")
        self.assertEqual(3, len(services))

        self.assertEqual(1, Trip.objects.filter(route__service=services[0]).count())
        self.assertEqual(1, Trip.objects.filter(route__service=services[1]).count())
        self.assertEqual(2, Trip.objects.filter(route__service=services[2]).count())

    @time_machine.travel("2021-03-08")
    def test_start_dead_run(self):
        """Turns out WaitTimes and RunTimes should be ignored during a StartDeadRun"""

        # create existing service with a RouteLink
        service_22a = Service.objects.create(
            line_name="22a",
        )
        Route.objects.create(
            service_code="SER22A", service=service_22a, source=self.nocs
        )
        service_22a.operator.add(self.fabd, self.fecs)
        RouteLink.objects.create(
            from_stop_id="260006514a",
            to_stop_id="0260006515",
            service=service_22a,
            geometry="SRID=4326;LINESTRING (-1.12267861 52.668932971, -1.122161887 52.669204097, "
            "-1.121984987 52.669298697, -1.121571887 52.669561097, -1.121253687 52.669807497, "
            "-1.120949487 52.670157297, -1.120839687 52.670452097, -1.120864987 52.670727597, "
            "-1.120919287 52.670949997, -1.120982582 52.671208876)",
        )

        garage = Garage.objects.create(code="LE", name="Leicester")

        call_command("import_transxchange", FIXTURES_DIR / "22A 22B 22C 08032021.xml")

        # re-import to test handling of already-existing ServiceCode
        call_command("import_transxchange", FIXTURES_DIR / "22A 22B 22C 08032021.xml")

        self.assertEqual(str(Trip.objects.get(ticket_machine_code="1935")), "19:35")

        trips = Trip.objects.filter(ticket_machine_code="2045")
        self.assertEqual(str(trips[0]), "20:45")
        self.assertEqual(str(trips[1]), "20:45")

        self.assertEqual(trips[0].garage, garage)  # Leicester = LEICESTER

        trips = Trip.objects.filter(ticket_machine_code="2145")
        self.assertEqual(str(trips[0]), "21:45")
        self.assertEqual(str(trips[1]), "21:45")

        garage = Garage.objects.last()
        self.assertEqual(garage.code, "GR")
        self.assertEqual(garage.name, "")

        self.assertEqual(Service.objects.count(), 3)
        self.assertEqual(RouteLink.objects.count(), 271)

        # had 2 operators before running import_transxchange,
        # should now have just 1
        self.assertEqual(list(service_22a.operator.all()), [self.fabd])

        # test timetable with multiple operators
        service_22a.operator.add(self.fecs)
        response = self.client.get(f"/services/{service_22a.id}/timetable")
        self.assertContains(response, '<th scope="row">Operator</th>')
        self.assertContains(
            response,
            """<td colspan="19">
                                            First Aberdeen
                                        </td>""",
        )

    @time_machine.travel("2021-06-28")
    def test_difficult_layout(self):
        with self.assertLogs(
            "bustimes.management.commands.import_transxchange", "WARNING"
        ) as cm:
            call_command(
                "import_transxchange", FIXTURES_DIR / "square_COMT_100_06100B.xml"
            )

        self.assertEqual(
            cm.output,
            [
                "WARNING:bustimes.management.commands.import_transxchange:{'NationalOperatorCode': 'COMT', "
                "'OperatorCode': 'COMT', 'OperatorShortName': 'Compass Travel'}"
            ],
        )

        response = self.client.get(Service.objects.get().get_absolute_url())
        timetable = response.context_data["timetable"]

        self.assertEqual(25, len(timetable.groupings[0].trips))
        self.assertEqual(27, len(timetable.groupings[1].trips))
        self.assertEqual(179, len(timetable.groupings[0].rows))
        self.assertEqual(179, len(timetable.groupings[1].rows))

        self.assertNotContains(response, "set down only")

    @time_machine.travel("2021-06-28")
    def test_different_notes_in_same_row(self):
        with (
            self.assertLogs(
                "bustimes.management.commands.import_transxchange", "WARNING"
            ) as cm,
            patch("os.path.getmtime", return_value=0),
        ):
            call_command("import_transxchange", FIXTURES_DIR / "twm_3-74-_-y11-1.xml")

        self.assertEqual(
            cm.output,
            [
                "WARNING:bustimes.management.commands.import_transxchange:{'NationalOperatorCode': 'YEOC', "
                "'OperatorCode': 'YEC', 'OperatorShortName': 'Yeomans Travel', 'OperatorNameOnLicence': 'Yeomans Travel', "
                "'TradingName': 'Yeomans Travel'}"
            ],
        )

        response = self.client.get(Service.objects.get().get_absolute_url())
        timetable = response.context_data["timetable"]

        self.assertEqual(26, len(timetable.groupings[0].rows))

        feet = list(timetable.groupings[0].column_feet.values())[0]

        self.assertEqual(3, feet[0].span)
        self.assertEqual(1, feet[1].span)
        self.assertEqual(3, feet[2].span)
        self.assertEqual(22, feet[3].span)
        self.assertEqual(1, feet[4].span)
        self.assertEqual(6, feet[5].span)

    @time_machine.travel("2021-07-07")
    def test_multiple_lines(self):
        filename = "904_SCD_PH_903_20210530.xml"

        call_command("import_transxchange", FIXTURES_DIR / filename)

        route_1, route_2 = Route.objects.filter(code__contains=filename).order_by(
            "-line_name"
        )

        trip = route_1.trip_set.first()
        self.assertEqual(trip.garage.name, "Barnstaple")

        service = route_2.service
        # detailed version
        response = self.client.get(f"{service.get_absolute_url()}?detailed=true")
        self.assertContains(response, '">9032</a>')  # block number
        self.assertContains(response, '">1554</a>')  # block number
        self.assertContains(response, "<td>Barnstaple</td>")  # garage

        # combined timetable
        response = self.client.get(
            service.get_absolute_url(),
            query_params={
                "service": [f"{route_2.service_id}:903", f"{route_1.service_id}:904"],
                "detailed": True,
            },
        )
        self.assertContains(response, '<td colspan="2">Barnstaple</td>')  # garage
        self.assertContains(response, '">904<')  # service
        self.assertContains(response, '<td colspan="2">LFDD</td>')  # vehicle type
        self.assertContains(response, "<td>LFDD</td>")

        # check combined timetable row ordering (sequence number tiebreaker)
        timetable = response.context_data["timetable"]
        codes = [r.stop.atco_code.split(":")[-1] for r in timetable.groupings[1].rows]
        self.assertEqual(
            codes[2:8],
            [
                "1100DEA11187",
                "1100DEA11186",
                "1100DEA11184",
                "1100DEZ05106",
                "1100DEA56800",
                "1100DEA09189",
            ],
        )

        self.assertEqual(Route.objects.filter(service__current=True).count(), 2)

        # test re-import
        call_command("import_transxchange", FIXTURES_DIR / filename)
        self.assertEqual(Route.objects.filter(service__current=True).count(), 2)

    @time_machine.travel("2021-07-07")
    def test_confusing_start_date(self):
        with self.assertLogs(
            "bustimes.management.commands.import_transxchange", "WARNING"
        ) as cm:
            call_command(
                "import_transxchange", FIXTURES_DIR / "notts_KRWL_DS_180DS_.xml"
            )

        self.assertEqual(1, len(cm.output))

        service = Service.objects.get()
        response = self.client.get(service.get_absolute_url())
        self.assertContains(response, "Tuesdays")
        self.assertContains(response, "from Tuesday 3 August 2021")

    def test_service_error(self):
        """A file with some wrong references should be handled gracefully"""
        with self.assertLogs(level="ERROR"):
            self.handle_files("NW.zip", ["NW_05_PBT_6_1.xml"])

    @time_machine.travel("1 September 2017")
    def test_services_nw(self):
        self.handle_files(
            "NW.zip",
            ["NW_04_GMN_2_1.xml", "NW_04_GMS_237_1.xml", "NW_04_GMS_237_2.xml"],
        )

        # 2
        service = Service.objects.get(service_code="NW_04_GMN_2_1")
        self.assertEqual(
            service.description, "intu Trafford Centre - Eccles - Swinton - Bolton"
        )

        self.assertEqual(23, service.stopusage_set.count())

        # Stagecoach Manchester 237
        service = Service.objects.get(service_code="NW_04_GMS_237_2")

        self.assertEqual(service.description, "Glossop - Stalybridge - Ashton")

        service.geometry = "SRID=4326;MULTILINESTRING((1.31326925542 51.1278853356,1.08276947772 51.2766792559))"
        service.save(update_fields=["geometry"])

        with self.assertNumQueries(15):
            res = self.client.get(service.get_absolute_url() + "?date=2017-09-01")
        self.assertEqual(str(res.context_data["timetable"].date), "2017-09-01")
        # self.assertContains(res, 'Timetable changes from <a href="?date=2017-09-03">Sunday 3 September 2017</a>')
        # self.assertContains(res, f'data-service="{service.id},{duplicate.id}"></div')

        with time_machine.travel("1 October 2017"), self.assertNumQueries(17):
            res = self.client.get(service.get_absolute_url())
        # self.assertContains(res, """
        #         <thead>
        #             <tr>
        #                 <th></th>
        #                 <td>237</td>
        #                 <td colspan="17"><a href="/services/237-glossop-stalybridge-ashton-2">237</a></td>
        #             </tr>
        #         </thead>
        # """, html=True)
        self.assertEqual(str(res.context_data["timetable"].date), "2017-10-01")
        # self.assertNotContains(res, 'Timetable changes from <a href="?date=2017-09-03">Sunday 3 September 2017</a>')
        self.assertEqual(18, len(res.context_data["timetable"].groupings[0].trips))

        # notes
        self.assertContains(
            res,
            "<td>Night Service, runs Saturday night/Sunday morning ONLY;special fares may</td>",
            html=True,
            count=1,
        )
        self.assertContains(
            res, "<p>Low floor bus - access for pushchairs and wheelchairs</p>", count=2
        )
        self.assertEqual(Note.objects.filter(trip__start="02:30:00").count(), 4)

        self.assertContains(
            res,
            "Piccadilly Gardens, Manchester City Centre or Ashton Under Lyne - Glossop",
        )
        self.assertContains(
            res,
            "Glossop - Piccadilly Gardens, Manchester City Centre or Ashton Under Lyne",
        )

        with time_machine.travel("1 October 2017"), self.assertNumQueries(9):
            timetable = service.get_timetable(date(2017, 10, 3)).render()
        self.assertEqual(str(timetable.date), "2017-10-03")
        self.assertEqual(27, len(timetable.groupings[1].trips))
        self.assertEqual(30, len(timetable.groupings[0].trips))

    @time_machine.travel("25 June 2016")
    def test_do_service_scotland(self):
        colour = ServiceColour.objects.create(
            name="Navy Blue Line",
            foreground="#111111",
            background="#c0c0c0",
            use_name_as_brand=True,
        )
        source = DataSource.objects.create(
            name="S", url="ftp://ftp.tnds.basemap.co.uk/S.zip"
        )
        service = Service.objects.create(
            service_code="ABBN017", line_name="N17", colour=colour, source=source
        )
        service.operator.add(self.fabd)

        # simulate a Scotland zipfile:
        self.handle_files("S.zip", ["SVRABBN017.xml"])

        service = Service.objects.get(line_name="N17")

        self.assertEqual(str(service), "N17 - Navy Blue Line - Aberdeen - Dyce")
        self.assertEqual(service.operator.first(), self.fabd)

        self.assertEqual([], list(service.get_traveline_links()))

        service.service_code = "LOTH_N3"
        self.assertEqual(
            list(service.get_traveline_links()),
            [
                (
                    "https://www.travelinescotland.com/timetables?serviceId=LOTH+N3",
                    "Timetable on the Traveline Scotland website",
                )
            ],
        )

        self.assertEqual(
            service.geometry.coords,
            (
                (
                    (-2.5108434749, 53.7389877672),
                    (-2.5108434749, 53.7425523688),
                    (-2.4989239373, 53.7425523688),
                    (-2.4989239373, 53.7389877672),
                    (-2.5108434749, 53.7389877672),
                ),
            ),
        )

        res = self.client.get(service.get_absolute_url())
        self.assertEqual(res.context_data["breadcrumb"], [self.sc, self.fabd])
        self.assertContains(
            res,
            '<td rowspan="63" class="then-every">then every 30 minutes until</td>',
            html=True,
        )

        timetable = res.context_data["timetable"]
        self.assertEqual("2016-06-25", str(timetable.date))
        self.assertEqual(3, len(timetable.groupings[0].rows[0].times))
        self.assertEqual(3, len(timetable.groupings[1].rows[0].times))
        self.assertEqual(timetable.groupings[0].column_feet, {})

        # Within operating period, but with no journeys
        res = self.client.get(service.get_absolute_url() + "?date=2026-04-18")
        self.assertContains(res, "Sorry, no journeys found")

        # Test the fallback version without a timetable (just a list of stops)
        service.timetable_wrong = True
        service.save(update_fields=["timetable_wrong"])
        res = self.client.get(service.get_absolute_url())
        self.assertContains(res, "Outbound")
        self.assertContains(
            res,
            """
            <li class="minor">
                <a href="/stops/639004554">Witton Park (opp)</a>
            </li>
        """,
            html=True,
        )

        self.assertEqual(88, service.stopusage_set.count())

        # Test service colour
        response = self.client.get("/stops/639004592")
        self.assertContains(response, "background: #c0c0c0;")
        self.assertContains(response, "border-color: #111111;")
        self.assertContains(response, "color: #111111;")

        response = self.client.get("/operators/FABD")
        self.assertContains(response, "background: #c0c0c0;")
        self.assertContains(response, "border-color: #111111;")
        self.assertContains(response, "color: #111111;")

        self.assertEqual(
            colour.preview(),
            """<div style="background:#c0c0c0;color:#111111">Navy Blue Line</div>""",
        )

        # set current=False and re-import - should reset operator etc:
        service.operator.add(self.fecs)
        service.current = False
        service.save(update_fields=["current"])

        self.handle_files("S.zip", ["SVRABBN017.xml"])
        service = Service.objects.get(line_name="N17")
        self.assertEqual(service.operator.count(), 1)

    @time_machine.travel("22 January 2017")
    def test_megabus(self):
        # simulate a National Coach Service Database zip file
        with TemporaryDirectory() as directory, patch("boto3.client") as mock_client:
            zipfile_path = Path(directory) / "NCSD.zip"
            with zipfile.ZipFile(zipfile_path, "a") as open_zipfile:
                self.write_file_to_zipfile(
                    open_zipfile,
                    Path("NCSD_TXC") / "Megabus_Megabus14032016 163144_MEGA_M11A.xml",
                )
                self.write_file_to_zipfile(
                    open_zipfile,
                    Path("NCSD_TXC") / "Megabus_Megabus14032016 163144_MEGA_M12.xml",
                )

            with self.assertLogs(
                "bustimes.management.commands.import_transxchange", "WARNING"
            ) as cm:
                call_command("import_transxchange", zipfile_path)

                # warning about missing stop
                self.assertEqual(
                    cm.output,
                    [
                        "WARNING:bustimes.management.commands.import_transxchange:370010201"
                    ],
                )

            m11a_trip_ids = Trip.objects.filter(route__line_name="M11A").last().id
            m12_trip_ids = Trip.objects.filter(route__line_name="M12").last().id

            # ensure that file is re-imported
            Route.objects.filter(line_name="M12").update(file_hash=None)

            # change departure time, so trip id should not be reused
            Trip.objects.filter(route__line_name="M12").update(start="00:00")

            # test re-importing a previously imported service again
            with self.assertLogs(
                "bustimes.management.commands.import_transxchange", "WARNING"
            ) as cm:
                call_command("import_transxchange", zipfile_path)

                # warning about missing stop (again)
                self.assertEqual(
                    cm.output,
                    [
                        "WARNING:bustimes.management.commands.import_transxchange:370010201"
                    ],
                )

            # ids should have kept the same
            self.assertEqual(
                m11a_trip_ids, Trip.objects.filter(route__line_name="M11A").last().id
            )
            # ids should not have kept the same
            self.assertNotEqual(
                m12_trip_ids, Trip.objects.filter(route__line_name="M12").last().id
            )

            mock_client.assert_called()

        # M11A

        res = self.client.get(
            "/services/m11a-belgravia-liverpool?date=ceci n'est pas une date"
        )

        service = res.context_data["object"]

        self.assertEqual(str(service), "M11A - Belgravia - Liverpool")
        self.assertEqual(service.operator.first(), self.megabus)
        self.assertEqual(list(service.get_traveline_links()), [])

        self.assertEqual(res.context_data["breadcrumb"], [self.gb, self.megabus])
        self.assertContains(res, "M11A - Belgravia - Liverpool")
        self.assertContains(
            res, '<option selected value="2017-01-22">Sunday 22 January 2017</option>'
        )
        self.assertContains(res, "/js/timetable.")

        timetable = res.context_data["timetable"]
        self.assertFalse(timetable.groupings[0].has_minor_stops())
        self.assertFalse(timetable.groupings[1].has_minor_stops())
        self.assertEqual(
            str(timetable.groupings[0].rows[0].times),
            "[13:00, 15:00, 16:00, 16:30, 18:00, 20:00, 23:45]",
        )

        # should only be 6, despite running 'import_services' twice
        self.assertEqual(6, service.stopusage_set.count())

        # trip timetable
        trip = Trip.objects.first()
        self.assertEqual(str(trip), "02:10")
        note = trip.notes.get()
        self.assertEqual(f"/trips/{trip.id}", note.get_absolute_url())

        # M12

        service = Service.objects.get(service_code="M12_MEGA")

        with time_machine.travel("1 January 2017"):
            res = self.client.get(service.get_absolute_url())

        self.assertContains(
            res, '<option selected value="2017-01-01">Sunday 1 January 2017</option>'
        )

        groupings = res.context_data["timetable"].groupings
        self.assertEqual(len(groupings[0].rows), 15)
        self.assertEqual(len(groupings[1].rows), 15)

        # check M12 row ordering (sequence number tiebreaker)
        codes = [(r.stop.atco_code or "").split(":")[-1] for r in groupings[0].rows]

        self.assertEqual(
            codes[10:14], ["450017207", "3390BB01", "079073001Z", "4100024PARWS"]
        )

        # for r in groupings[0].rows:
        #     print(r.stop.atco_code, [cell if type(cell) is str else cell.stoptime.sequence for cell in r.times])

        self.assertContains(
            res,
            """<td></td><td>06:15</td><td rowspan="2">09:20</td><td rowspan="2">10:20</td><td></td><td></td><td></td>
                <td></td><td></td><td rowspan="2"></td>""",
            html=True,
        )
        self.assertContains(
            res,
            """
            <tr class="dep">
                <td>02:45</td><td>06:20</td><td>11:30</td><td>12:30</td><td>13:45</td><td>16:20</td><td>18:40</td>
            </tr>
        """,
            html=True,
        )

        # operator note is applied to all M12 trips
        m12_trip = Trip.objects.filter(route__line_name="M12").first()
        operator_note = m12_trip.notes.get(code="")
        self.assertEqual(
            "You must book this service in advance via the operator's website or telephone",
            operator_note.text,
        )

    @time_machine.travel("4 June 2022")
    def test_rail(self):
        Operator.objects.create(noc="LM", name="West Midlands Railroad")
        OperatorCode.objects.create(operator_id="LM", code="LM", source=self.nocs)
        StopPoint.objects.create(
            atco_code="9100STRBDGT",
            common_name="Stourbridge Town Rail Station",
            active=True,
        )

        with patch("os.path.getmtime", return_value=1582385679):
            call_command("import_transxchange", FIXTURES_DIR / "nrc_90-72-_-r08-1.xml")

        service = Service.objects.get()
        self.assertEqual("Stourbridge Shuttle", service.line_brand)
        self.assertEqual("Stourbridge Junction - Stourbridge Town", service.description)

        response = self.client.get(service.get_absolute_url())
        self.assertContains(
            response, '<a href="/stops/9100STRBDGT">Stourbridge Town Rail Station</a>'
        )
        self.assertContains(
            response,
            '<a href="/operators/west-midlands-railroad">West Midlands Railroad</a>',
        )
        self.assertContains(response, ">Stourbridge Junction Rail Station<")
        self.assertEqual(4, service.stopusage_set.count())

    def test_get_service_code(self):
        self.assertEqual(
            import_transxchange.get_service_code("ea_21-2-_-y08-1.xml"), "ea_21-2-_-y08"
        )
        self.assertEqual(
            import_transxchange.get_service_code("ea_21-27-D-y08-1.xml"),
            "ea_21-27-D-y08",
        )
        self.assertEqual(
            import_transxchange.get_service_code("tfl_52-FL2-_-y08-1.xml"),
            "tfl_52-FL2-_-y08",
        )
        self.assertEqual(
            import_transxchange.get_service_code("suf_56-FRY-1-y08-15.xml"),
            "suf_56-FRY-1-y08",
        )
        self.assertIsNone(import_transxchange.get_service_code("NATX_330.xml"))
        self.assertIsNone(import_transxchange.get_service_code("NE_130_PB2717_21A.xml"))
        self.assertIsNone(
            import_transxchange.get_service_code("SVRABAN007-20150620-9.xml")
        )
        self.assertIsNone(
            import_transxchange.get_service_code("SVRWLCO021-20121121-13693.xml")
        )
        self.assertIsNone(
            import_transxchange.get_service_code(
                "National Express_NX_atco_NATX_T61.xml"
            )
        )
        self.assertIsNone(
            import_transxchange.get_service_code(
                "SnapshotNewportBus_TXC_2015714-0317_NTAO155.xml"
            )
        )
        self.assertIsNone(
            import_transxchange.get_service_code(
                "ArrivaCymru51S-Rhyl-StBrigid`s-Denbigh1_TXC_2016108-0319_DGAO051S.xml"
            )
        )

    @time_machine.travel("2023-07-24")
    def test_pick_up_and_set_down(self):
        with patch("os.path.getmtime", return_value=1690162625):
            call_command(
                "import_transxchange", FIXTURES_DIR / "t8_pick_up_set_down.xml"
            )

        service = Service.objects.get()
        response = self.client.get(service.get_absolute_url())
        self.assertContains(response, '<abbr title="set down only">s</abbr>', 17)
        self.assertContains(response, '<abbr title="pick up only">p</abbr>', 19)

    @time_machine.travel("2023-07-20")
    def test_stop_usage_notes(self):
        with patch("os.path.getmtime", return_value=1690162625):
            call_command("import_transxchange", FIXTURES_DIR / "swe_40-228-_-y10-1.xml")

        service = Service.objects.get()
        response = self.client.get(service.get_absolute_url())

        self.assertContains(response, '<abbr title="set down only">s</abbr>', 10)
        self.assertContains(response, "<strong>Sch</strong> Schooldays Only", 1)
        self.assertContains(response, "<strong>Sch</strong>", 15)

    def test_get_operator_name(self):
        blue_triangle_element = ET.fromstring(
            """
            <Operator id='OId_BE'>
                <OperatorCode>BE</OperatorCode>
                <OperatorShortName>BLUE TRIANGLE BUSES LIM</OperatorShortName>
                <OperatorNameOnLicence>BLUE TRIANGLE BUSES LIMITED</OperatorNameOnLicence>
                <TradingName>BLUE TRIANGLE BUSES LIMITED</TradingName>
            </Operator>
        """
        )
        self.assertEqual(
            import_transxchange.get_operator_name(blue_triangle_element),
            "BLUE TRIANGLE BUSES LIMITED",
        )

    def test_get_operator(self):
        command = import_transxchange.Command()
        command.missing_operators = []
        command.set_region("EA.zip")
        element = ET.fromstring(
            """
            <Operator id="OId_RRS">
                <OperatorCode>LUTD</OperatorCode>
                <OperatorShortName>London Transit</OperatorShortName>
                <OperatorNameOnLicence>London Transit</OperatorNameOnLicence>
                <TradingName>London Transit</TradingName>
            </Operator>
        """
        )
        self.assertIsNone(command.get_operator(element))
        self.assertEqual(
            command.missing_operators,
            [
                {
                    "OperatorCode": "LUTD",
                    "OperatorNameOnLicence": "London Transit",
                    "OperatorShortName": "London Transit",
                    "TradingName": "London Transit",
                }
            ],
        )

        self.assertIsNone(
            command.get_operator(
                ET.fromstring(
                    """
            <Operator id="OId_RRS">
                <OperatorCode>BEAN</OperatorCode>
                <TradingName>Bakers</TradingName>
            </Operator>
        """
                )
            )
        )
        self.assertEqual(2, len(command.missing_operators))

        command.source.name = "L"
        element = ET.fromstring(
            """
    <Operator id="OId_BE">
      <OperatorCode>BE</OperatorCode>
      <OperatorShortName>BLUE TRIANGLE BUSES LIM</OperatorShortName>
      <OperatorNameOnLicence>BLUE TRIANGLE BUSES LIMITED</OperatorNameOnLicence>
      <TradingName>BLUE TRIANGLE BUSES LIMITED</TradingName>
    </Operator>
"""
        )
        self.assertEqual(self.fabd, command.get_operator(element))

        element.find("OperatorCode").text = "PO"
        self.assertIsNone(command.get_operator(element))

        element.find("OperatorCode").text = "LC"
        self.assertEqual(self.fabd, command.get_operator(element))

    def test_get_registration(self):
        lic = Licence.objects.create(
            licence_number="PH0000153", discs=0, authorised_discs=0
        )
        reg = Registration.objects.create(
            licence=lic,
            registration_number="PH0000153/159",
            registered=True,
            service_number="69",
            start_point="Ham",
            finish_point="Sandwich",
        )
        self.assertEqual(reg, import_transxchange.get_registration("PH0000153:159"))
        self.assertEqual(reg, import_transxchange.get_registration("PH000000153:0159"))
        self.assertEqual(reg, import_transxchange.get_registration("PH0153:159"))
        self.assertIsNone(import_transxchange.get_registration("P000153:159"))

    def test_summary(self):
        self.assertEqual(
            import_transxchange.get_summary(
                "School vacation in free public holidays regulation days"
            ),
            "school days",
        )
        self.assertEqual(
            import_transxchange.get_summary("University days days only"),
            "University days only",
        )
        self.assertEqual(
            import_transxchange.get_summary(
                "Staffordshire School Holidays holidays only"
            ),
            "Staffordshire School Holidays only",
        )

        self.assertEqual(
            import_transxchange.get_summary("Schooldays days only"), "school days only"
        )
        self.assertEqual(import_transxchange.get_summary("Schools days"), "school days")
        self.assertEqual(
            import_transxchange.get_summary("SCHOOLDAYS days"), "school days"
        )
        self.assertEqual(
            import_transxchange.get_summary("Schooldays holidays"), "school holidays"
        )
        self.assertEqual(import_transxchange.get_summary("AnySchool"), "school")
        self.assertEqual(
            import_transxchange.get_summary("SCHOOLDAYS holidays"), "school holidays"
        )

    @time_machine.travel("2023-10-23")
    def test_split_registration(self):
        with self.assertLogs(
            "bustimes.management.commands.import_transxchange", "WARNING"
        ) as cm:
            self.handle_files(
                "FECS.zip",
                [
                    "A_B_C-FECS_A_B_C--FECS-NORWICH-2023-10-22-NO3BSH-BODS_V1_1.xml",
                    "B_C_A-FECS_B_C_A--FECS-NORWICH-2023-10-22-NO3BSH-BODS_V1_1.xml",
                ],
            )

        self.assertEqual(
            cm.output[-1][-68:],
            ":0500FWISM032 is 0.24674205822477827 from POINT (-0.260562 52.59902)",
        )
        self.assertEqual(3, Service.objects.filter(current=True).count())  # A, B, C
        self.assertEqual(6, Route.objects.count())

        service = Service.objects.first()
        response = self.client.get(service.get_absolute_url())

        self.assertContains(response, "Kings Lynn,Bus Station")
        self.assertContains(response, "Peterborough Bus Station")

        trip = Trip.objects.first()
        response = self.client.get(f"/trips/{trip.id}")
        self.assertContains(response, "Kings Lynn,Bus Station")
        self.assertContains(response, "Peterborough Bus Station")

        # test modern trip API too:
        with self.assertNumQueries(6):
            response = self.client.get(f"/api/trips/{trip.id}.json")
        self.assertEqual(response.json()["block"], "6001")

        with self.assertNumQueries(2):
            response = self.client.get("/api/trips/").json()
            self.assertEqual(len(response["results"]), 100)

        with self.assertNumQueries(2):
            response = self.client.get("/api/trips/?date=2025-10-12").json()
            self.assertEqual(len(response["results"]), 46)

        with self.assertNumQueries(6):
            response = self.client.get(f"/trips/{trip.id}/block")
        self.assertContains(response, "07:55")
        self.assertContains(response, "12:25")

        with self.assertNumQueries(6):
            response = self.client.get(f"/trips/{trip.id}/block?date=2025-01-26")
        self.assertContains(response, "15:05")
        self.assertContains(response, "16:00")

        # test "next trips in block"
        v = Vehicle.objects.create(code="BB69BUS")
        trip_1 = Trip.objects.get(vehicle_journey_code="VJ2937")
        trip_2 = Trip.objects.get(vehicle_journey_code="VJ2938")
        vj_1 = VehicleJourney.objects.create(
            trip=trip_1,
            service=trip.route.service,
            datetime="2025-10-12T08:05:00+01:00",
            date="2025-10-12",
            source_id=service.source_id,
            vehicle=v,
        )
        vj_2 = VehicleJourney.objects.create(
            trip=trip_2,
            service=trip.route.service,
            datetime="2025-10-12T09:00:00+01:00",
            date="2025-10-12",
            source_id=service.source_id,
            vehicle=v,
        )
        v.latest_journey = vj_2
        v.save(update_fields=["latest_journey_id"])

        response = self.client.get(v.get_absolute_url())
        self.assertEqual(5, len(response.context_data["predictions"]))

        response = self.client.get(f"/journeys/{vj_1.id}.json").json()

        self.assertEqual(vj_2.id, response["next"]["id"])
        self.assertEqual(20, len(response["stops"]))

    @time_machine.travel("2024-01-01")
    def test_frequency(self):
        with self.assertLogs(level="WARNING") as cm:
            # import a document with a Frequency structure (journey repeats every 10 minutes)
            self.handle_files(
                "FECS.zip",
                [
                    "BNSM_59.xml",
                    "CBBH_10LU.xml",
                    "CBNL_22.xml",
                ],
            )

        self.assertEqual(
            cm.output[-1],
            "WARNING:bustimes.management.commands.import_transxchange:"
            "CBNL_22.xml has {'tkt_oid': FABD: First Aberdeen} but unexpected filename format",
        )

        # automatically created journey every 10 minutes
        route = Route.objects.get(line_name="59")
        self.assertEqual(route.trip_set.count(), 155)

        # PastTheHour, but all journeys specified in file
        route = Route.objects.get(line_name="22")
        self.assertEqual(route.trip_set.count(), 97)

        # all journeys specified in file
        route = Route.objects.get(line_name="10")
        self.assertEqual(route.trip_set.count(), 125)

    @time_machine.travel("2024-01-01")
    def test_multiple_wait_times(self):
        # Nottingham City Transport 34/34C
        self.handle_files("FECS.zip", ["PB0002362-132_NCT_2025-1-12.xml"])

        trip = Trip.objects.filter(start="23:15:00").first()
        self.assertEqual(str(trip.start), "23:15")
        self.assertEqual(str(trip.end), "23:44")

        response = self.client.get(f"/services/{trip.route.service_id}/timetable.csv")
        self.assertContains(
            response,
            f'"UoN Main Campus Beeston La, East Mids Conf Ctr",,{trip.route.source_id}:3390UN48,18:45,then every 15 minutes until,23:15',
        )

    @time_machine.travel("2025-12-31")
    def test_highland_council(self):
        Operator.objects.create(noc="HIGH", name="Highland Council")

        # test the special Highland Council timetable data
        # whose routelink geometries have to be multiplied by 1000000

        self.handle_files(
            "highlandcouncilbuses_1772097639.zip", ["hit_2-252-A-y20-1.xml"]
        )

        service = Service.objects.get(line_name="252A")
        self.assertTrue(service.current)
        self.assertEqual(10, service.stops.count())

        route_link = service.routelink_set.get(
            from_stop=f"{service.source_id}:6700713000"
        )
        self.assertEqual(4326, route_link.geometry.srid)
        # geometry should be in Scotland (near Nairn, Highland)
        x, y = route_link.geometry[0]
        self.assertAlmostEqual(x, -3.883, places=2)
        self.assertAlmostEqual(y, 57.578, places=2)
