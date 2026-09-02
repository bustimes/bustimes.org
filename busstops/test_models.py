from django.test import TestCase

from accounts.models import User
from bustimes.models import Route

from .models import (
    AdminArea,
    DataSource,
    District,
    Locality,
    Operator,
    Region,
    Service,
    StopPoint,
)


class RegionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Create some regions
        cls.midlands = Region.objects.create(pk="NM", name="North Midlands")
        cls.east = Region.objects.create(pk="ME", name="Middle East")
        cls.ireland = Region.objects.create(pk="IE", name="Ireland")

    def test_string(self):
        self.assertEqual(str(self.midlands), "North Midlands")

    def test_the(self):
        "Regions with certain names should have 'the' prepended, and others shouldn't."
        self.assertEqual(self.midlands.the(), "the North Midlands")
        self.assertEqual(self.east.the(), "the Middle East")
        self.assertEqual(self.ireland.the(), "Ireland")

    def test_get_absolute_url(self):
        self.assertEqual(self.midlands.get_absolute_url(), "/regions/NM")


class LocalityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.midlands = Region.objects.create(pk="NM", name="North Midlands")
        cls.dorset = AdminArea.objects.create(
            pk="4", atco_code=2, region=cls.midlands, name="Dorset"
        )

        cls.north_yorkshire = District.objects.create(
            pk="2", admin_area=cls.dorset, name="North Yorkshire"
        )

        cls.brinton = Locality.objects.create(
            pk="1", admin_area=cls.dorset, name="Brinton"
        )
        cls.york = Locality.objects.create(
            pk="2", admin_area=cls.dorset, name="York", qualifier_name="York"
        )

    def test_get_qualified_name(self):
        self.assertEqual(self.brinton.get_qualified_name(), "Brinton")
        self.assertEqual(self.york.get_qualified_name(), "York, York")

    def test_string(self):
        self.assertEqual(str(self.brinton), "Brinton")
        self.assertEqual(str(self.york), "York")

        self.assertEqual(str(self.brinton.admin_area), "Dorset")


class OperatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.north = Region.objects.create(pk="N", name="North")
        cls.chariots = Operator.objects.create(
            pk="CHAR", region=cls.north, name="Ainsley's Chariots"
        )
        cls.user = User.objects.create(
            username="josh", is_staff=True, is_superuser=True
        )

    def test_get_qualified_name(self):
        self.assertEqual(str(self.chariots), "Ainsley's Chariots")

    def test_admin(self):
        self.client.force_login(self.user)

        response = self.client.get("/admin/busstops/operator/")
        self.assertContains(response, '<td class="field-operator_codes">-</td>')
        self.assertContains(
            response,
            '<td class="field-services"><a href="/admin/busstops/service/?operator__noc__exact=CHAR">0</a></td>'
            '<td class="field-vehicles"><a href="/admin/vehicles/vehicle/?operator__noc__exact=CHAR">0</a></td>',
        )
        self.assertEqual(1, response.context_data["cl"].result_count)

        response = self.client.get("/admin/busstops/operator/?q=ainsley")
        self.assertEqual(1, response.context_data["cl"].result_count)

        response = self.client.get("/admin/busstops/operator/?q=sanders")
        self.assertEqual(0, response.context_data["cl"].result_count)

        response = self.client.get(
            "/admin/autocomplete/?term=ainsley&app_label=busstops&model_name=operatorpaymentmethod"
            "&field_name=operator"
        )
        self.assertEqual(
            response.json(), {"results": [], "pagination": {"more": False}}
        )


class ServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.london_service = Service.objects.create(
            service_code="tfl_8-N41-_-y05",
            line_name="N41",
        )
        source = DataSource.objects.create(name="EA")
        cls.service = Service.objects.create(
            service_code="ea_21-1A-_-y08", source=source
        )
        Route.objects.create(
            code="swe_21-1A-_-y08-2.xml",
            service=cls.service,
            start_date="2012-05-01",
            source=source,
        )
        Route.objects.create(
            code="swe_21-1A-_-y08-1.xml",
            service=cls.service,
            start_date="2012-01-01",
            source=source,
        )
        cls.user = User.objects.create(
            username="josh", is_staff=True, is_superuser=True
        )

    def test_str(self):
        self.assertEqual(str(self.london_service), "N41")

        self.london_service.line_name = ""
        self.assertEqual(str(self.london_service), "tfl_8-N41-_-y05")
        self.london_service.line_name = "N41"

        service = Service(line_name="C", description="Happisburgh - Filey")
        self.assertEqual(str(service), "C - Happisburgh - Filey")
        self.assertEqual(service.get_line_name_and_brand(), "C")

        service.line_brand = "Coast Hopper"
        self.assertEqual(str(service), "Coast Hopper - Happisburgh - Filey")
        self.assertEqual(service.get_line_name_and_brand(), "C - Coast Hopper")

        service.line_brand = ""
        service.line_name = "Coast Hopper"
        service.description = "Coast Hopper"
        self.assertEqual(str(service), "Coast Hopper")

        service.line_name = "Coast Hopper"
        service.description = "Coast Hopper – Brighton - Filey"
        self.assertEqual(str(service), "Coast Hopper – Brighton - Filey")

    def test_get_a_mode(self):
        service = Service(mode="")

        self.assertEqual(service.get_a_mode(), "A ")

        service.mode = "Underground"
        self.assertEqual(service.get_a_mode(), "An Underground")

        service.mode = "bus"
        self.assertEqual(service.get_a_mode(), "A bus")

        operator = Operator(vehicle_mode="airline")
        self.assertEqual(operator.get_a_mode(), "An airline")

        operator = Operator(vehicle_mode="rail")
        self.assertEqual(operator.get_a_mode(), "A rail")

    def test_admin(self):
        self.client.force_login(self.user)

        response = self.client.get("/admin/busstops/service/")
        self.assertEqual(2, response.context_data["cl"].result_count)

        response = self.client.get("/admin/busstops/service/?q=21")
        self.assertEqual(1, response.context_data["cl"].result_count)

        response = self.client.get(
            "/admin/autocomplete/?term=21&app_label=busstops&model_name=servicecode&field_name=service"
        )
        self.assertEqual(
            {"results": [], "pagination": {"more": False}}, response.json()
        )


class StopPointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(id="R")
        admin_area = AdminArea.objects.create(id=1, atco_code=1, region=region)
        cls.cardiff_airport_locality = Locality.objects.create(
            name="Cardiff Airport", admin_area=admin_area
        )
        cls.cardiff_airport_stop = StopPoint.objects.create(
            common_name="Airport",
            locality=cls.cardiff_airport_locality,
            active=True,
        )
        cls.ballyblack_church = StopPoint.objects.create(
            atco_code="700000002498",
            active=True,
            common_name="Ballyblack Church",
            town="Ballyblack",
        )
        cls.user = User.objects.create(
            username="josh", is_staff=True, is_superuser=True
        )

    def test_get_qualified_name(self):
        self.assertEqual(
            "Ballyblack Church", self.ballyblack_church.get_qualified_name()
        )
        self.ballyblack_church.common_name = "Methodist Church"
        self.assertEqual(
            "Ballyblack Methodist Church", self.ballyblack_church.get_qualified_name()
        )

        self.assertEqual(
            "Cardiff Airport", self.cardiff_airport_stop.get_qualified_name()
        )
        self.cardiff_airport_stop.indicator = "Stop M"
        self.assertEqual(
            "Cardiff Airport (Stop M)", self.cardiff_airport_stop.get_qualified_name()
        )

        stop = StopPoint(common_name="Bus Station stand V", indicator="Stand V")
        self.assertEqual("Bus Station stand V", stop.get_qualified_name())

    def test_admin(self):
        self.client.force_login(self.user)

        response = self.client.get("/admin/busstops/stoppoint/")
        self.assertEqual(2, response.context_data["cl"].result_count)

        response = self.client.get("/admin/busstops/stoppoint/?q=cardiff+airport")
        self.assertEqual(1, response.context_data["cl"].result_count)

        response = self.client.get("/admin/busstops/stoppoint/?q=holland")
        self.assertEqual(0, response.context_data["cl"].result_count)

    def test_icon(self):
        stop = StopPoint(common_name="Bus Station", indicator="Stand B")

        self.assertEqual(stop.get_icon(), "B")

        stop.indicator = ""
        stop.common_name = "Castle Meadow CR"
        self.assertEqual(stop.get_icon(), "CR")

        stop.common_name = "YMCA"
        self.assertIsNone(stop.get_icon())

        stop.common_name = "P"
        self.assertEqual(stop.get_icon(), "P")

        stop.common_name = "Brecon Interchange Stop 12"
        self.assertEqual(stop.get_icon(), "12")
