import os
import zipfile
from tempfile import TemporaryDirectory

import time_machine
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.test import TestCase

from busstops.models import Operator, Region, Service, StopPoint, StopUsage

from ...models import Route

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def write_file_to_zipfile(open_zipfile, filename):
    open_zipfile.write(os.path.join(FIXTURES_DIR, filename), filename)


def write_files_to_zipfile(zipfile_path, filenames):
    with zipfile.ZipFile(zipfile_path, "a") as open_zipfile:
        for filename in filenames:
            write_file_to_zipfile(open_zipfile, filename)


class ImportAtcoCifTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ni = Region.objects.create(pk="NI", name="Northern Ireland")
        cls.gle = Operator.objects.create(
            noc="GLE", name="Goldline Express", region=cls.ni
        )

        StopPoint.objects.bulk_create(
            StopPoint(atco_code=atco_code, latlong=Point(0, 0), active=True)
            for atco_code in (
                "700000015363",
                "700000015687",
                "700000004923",
                "700000005645",
            )
        )

    def test_ulsterbus(self):
        with TemporaryDirectory() as directory:
            zipfile_path = os.path.join(directory, "ulb.zip")

            write_files_to_zipfile(zipfile_path, ["218 219.cif"])

            with time_machine.travel("2019-10-09"):
                with self.assertNumQueries(359):
                    call_command("import_atco_cif", zipfile_path)
                with self.assertNumQueries(365):
                    call_command("import_atco_cif", zipfile_path)

        self.assertEqual(5, Route.objects.count())
        self.assertEqual(5, Service.objects.count())
        self.assertEqual(106, StopUsage.objects.count())

        service = Service.objects.get(service_code="219A_GLE")
        route = Route.objects.get(service__service_code="219A_GLE")
        self.assertEqual(
            "Belfast, Europa Buscentre - Antrim, Buscentre", service.description
        )
        self.assertEqual(
            "Belfast, Europa Buscentre - Antrim, Buscentre",
            route.outbound_description,
        )
        self.assertEqual(
            "Antrim, Buscentre - Belfast, Europa Buscentre", route.inbound_description
        )

        trip = route.trip_set.first()
        self.assertEqual("", trip.block)
        self.assertEqual("Goldline Express", trip.operator.name)
        self.assertEqual("1700", trip.ticket_machine_code)

        with time_machine.travel("2019-10-01"), self.assertNumQueries(14):
            response = self.client.get(
                f"/services/{service.id}/timetable?date=2019-10-01"
            )
        self.assertContains(
            response,
            '<option selected value="2019-10-01">Tuesday 1 October 2019</option>',
        )
        self.assertNotContains(response, "Sunday")
        self.assertContains(response, '<abbr title="pick up only">p</abbr>')

        with time_machine.travel("2019-08-12"), self.assertNumQueries(10):
            response = self.client.get(
                f"/services/{service.id}/timetable?date=2019-08-12"
            )
        self.assertContains(
            response,
            '<option selected value="2019-08-12">Monday 12 August 2019</option>',
        )
        self.assertNotContains(response, "Sunday")
        self.assertContains(response, "Sorry, no journeys found")

        with time_machine.travel("2019-08-12"), self.assertNumQueries(14):
            response = self.client.get(
                f"/services/{service.id}/timetable?date=2019-12-25"
            )
        self.assertContains(
            response,
            '<option selected value="2019-12-25">Wednesday 25 December 2019</option>',
        )
        self.assertNotContains(response, "Sunday")

        with time_machine.travel("2019-08-12"), self.assertNumQueries(14):
            response = self.client.get(
                f"/services/{service.id}/timetable?date=2019-12-25"
            )
        self.assertContains(
            response,
            '<option selected value="2019-12-25">Wednesday 25 December 2019</option>',
        )
        self.assertNotContains(response, "Sunday")

        # no journeys on this date - CalendarDate with operation = False - so should skip to next date of operation
        with time_machine.travel("2019-07-20"), self.assertNumQueries(21):
            response = self.client.get(
                "/services/219-belfast-europa-buscentre-ballymena-buscentre"
            )
            self.assertEqual("2019-07-27", str(response.context_data["timetable"].date))
            self.assertEqual(1, len(response.context_data["timetable"].groupings))
        self.assertContains(response, "set down only")

        service = Service.objects.get(service_code="218_GLE")
        with time_machine.travel("2019-10-01"), self.assertNumQueries(21):
            response = self.client.get(service.get_absolute_url() + "?date=2019-10-01")
        self.assertContains(response, "set down only")
