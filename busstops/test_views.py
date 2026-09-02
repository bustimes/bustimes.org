from unittest.mock import patch

import time_machine
import vcr
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core import mail
from django.core.management import call_command
from django.shortcuts import render
from django.test import TestCase, override_settings

from accounts.models import User
from bustimes.models import Route, StopTime, Trip

from .models import (
    AdminArea,
    DataSource,
    District,
    Locality,
    Operator,
    PaymentMethod,
    Region,
    Service,
    StopPoint,
    StopUsage,
)


class ContactTests(TestCase):
    """Tests for the contact form and view"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="bob", email="bob@example.com")

    def test_contact_get(self):
        response = self.client.get("/contact")
        self.assertEqual(response.status_code, 200)

        # user logged in - set initial email address value
        self.client.force_login(self.user)
        response = self.client.get("/contact")
        self.assertContains(response, ' value="bob@example.com" ')

    def test_empty_contact_post(self):
        response = self.client.post("/contact")
        self.assertFalse(response.context["form"].is_valid())

    @patch("turnstile.fields.TurnstileField.validate", return_value=True)
    def test_contact_post(self, mock_validate):
        self.client.force_login(self.user)

        response = self.client.post(
            "/contact",
            {
                "name": 'Rufus "Red" Herring',
                "email": "rufus@example.com",
                "message": "Dear John,\r\n\r\nHow are you?\r\n\r\nAll the best,\r\nRufus",
                "referrer": "https://www.yahoo.com",
            },
        )

        self.assertContains(response, "<h1>Thank you</h1>", html=True)

        message = mail.outbox[0]
        self.assertEqual(message.subject, "Dear John,")
        self.assertEqual(
            message.from_email, '"Rufus "Red" Herring" <contactform@bustimes.org>'
        )
        self.assertEqual(message.to, ["contact@bustimes.org"])
        self.assertIn("https://www.yahoo.com", message.body)
        self.assertIn(f"/accounts/users/{self.user.id}/", message.body)


class ViewsTests(TestCase):
    """Boring tests for various views"""

    @classmethod
    @time_machine.travel("2023-02-21")
    def setUpTestData(cls):
        cls.north = Region.objects.create(pk="N", name="North")
        cls.norfolk = AdminArea.objects.create(
            id=91, atco_code=91, region=cls.north, name="Norfolk"
        )
        cls.north_norfolk = District.objects.create(
            id=91, admin_area=cls.norfolk, name="North Norfolk"
        )
        cls.melton_constable = Locality.objects.create(
            id="E0048689",
            admin_area=cls.norfolk,
            name="Melton Constable",
            latlong=Point(-0.14, 51.51),
        )
        cls.inactive_stop = StopPoint.objects.create(
            pk="2900M115",
            common_name="Bus Shelter",
            active=False,
            admin_area=cls.norfolk,
            locality=cls.melton_constable,
            locality_centre=False,
            indicator="adj",
            bearing="E",
            latlong=Point(1.041894987727773, 52.85610279717982),
        )
        cls.stop = StopPoint.objects.create(
            atco_code="2900M114",
            naptan_code="NFODGJTG",
            common_name="Bus Shelter",
            active=True,
            admin_area=cls.norfolk,
            locality=cls.melton_constable,
            locality_centre=False,
            indicator="opp",
            bearing="W",
            latlong=Point(1.041894987727773, 52.85610279717982),
        )
        cls.inactive_service = Service.objects.create(
            service_code="45A", line_name="45A", region=cls.north, current=False
        )
        StopUsage.objects.create(service=cls.inactive_service, stop=cls.stop, order=0)
        cls.inactive_service_with_alternative = Service.objects.create(
            service_code="45B",
            line_name="45B",
            description="Holt - Norwich",
            region=cls.north,
            current=False,
        )
        cls.service = Service.objects.create(
            service_code="ea_21-45-A-y08",
            line_name="45C",
            description="Holt - Norwich",
            region=cls.north,
        )
        source = DataSource.objects.create()
        route = Route.objects.create(
            service=cls.service, source=source, line_name="45C"
        )
        trip = Trip.objects.create(route=route, start="0", end="1")
        StopTime.objects.create(trip=trip, stop=cls.stop, arrival="2")
        StopUsage.objects.create(service=cls.service, stop=cls.stop, order=0)

        Service.objects.bulk_create(
            [
                Service(line_name=str(i), description="Sandwich - Deal", current=True)
                for i in range(1, 30)
            ]
        )

        cls.chariots = Operator.objects.create(
            noc="AINS",
            name="Ainsley's Chariots",
            vehicle_mode="airline",
            region_id="N",
            address="10 King Road\nIpswich",
            phone="0800 1111",
            email="ainsley@example.com",
            url="http://www.ouibus.com",
            twitter="dril\ncoldwarsteve",
        )
        cls.nuventure = Operator.objects.create(
            noc="VENT", name="Nu-Venture", vehicle_mode="bus", region_id="N"
        )

        oyster = PaymentMethod.objects.create(
            name="oyster card", url="http://example.com"
        )
        euros = PaymentMethod.objects.create(name="euros")

        cls.chariots.payment_methods.set([oyster, euros])
        cls.service.operator.add(cls.chariots)
        cls.inactive_service.operator.add(cls.chariots)

    def test_index(self):
        """Home page works and doesn't contain a breadcrumb"""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Home")

    def test_robots_txt(self):
        response = self.client.get("/robots.txt")
        self.assertContains(response, "\n\nUser-agent: *\nDisallow: /\n")

        with override_settings(ALLOWED_HOSTS=["bustimes.org"]):
            response = self.client.get("/robots.txt", headers={"host": "bustimes.org"})
        self.assertContains(response, "User-agent: *\nDisallow:")

    def test_not_found(self):
        """Not found responses have a 404 status code"""
        response = self.client.get("/fff")
        self.assertEqual(response.status_code, 404)

    def test_static(self):
        for route in ("/cookies", "/data"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)

    def test_region(self):
        response = self.client.get("/regions/N")
        self.assertContains(response, "North")
        self.assertContains(response, "<h1>North</h1>")

        self.assertContains(
            response, "Chariots"
        )  # An operator with a current service should be listed
        self.assertNotContains(
            response, "Nu-Venture"
        )  # An operator with no current services should not be listed

        self.assertNotContains(response, '<a href="/areas/91">Norfolk</a>')
        self.assertNotContains(response, '<a href="/districts/91">North Norfolk</a>')

        self.melton_constable.district = self.north_norfolk
        self.melton_constable.save()
        response = self.client.get("/regions/N")
        self.assertNotContains(
            response, '<a href="/areas/91">Norfolk</a>'
        )  # Only one area in this region - so...
        self.assertContains(
            response, '<a href="/districts/91">North Norfolk</a>'
        )  # ...list the districts in the area

    def test_lowercase_region(self):
        response = self.client.get("/regions/n")
        self.assertContains(
            response, '<link rel="canonical" href="https://bustimes.org/regions/N">'
        )
        self.assertEqual(response.status_code, 200)

    def test_search(self):
        response = self.client.get("/search?q=melton")
        self.assertContains(response, "1 place")
        self.assertContains(response, "<b>Melton</b> Constable")
        self.assertContains(response, "/localities/melton-constable")

        response = self.client.get("/search")
        self.assertNotContains(response, "found for")

        response = self.client.get("/search?q=+")
        self.assertNotContains(response, "found for")

        services = Service.objects.with_documents()
        for service in services:
            service.search_vector = service.document
        Service.objects.bulk_update(services, ["search_vector"])

        response = self.client.get("/search?q=sandwich+deal")
        self.assertContains(response, "<b>Sandwich</b> - <b>Deal</b>")
        self.assertContains(
            response,
            '<li><a rel="next nofollow" href="?q=sandwich+deal&amp;page=2#services">2</a></li>',
        )

        response = self.client.get("/search?q=sandwich+deal&page=2")
        # explicity link to page 1
        self.assertContains(
            response,
            '<li><a rel="prev" href="?q=sandwich+deal&amp;page=1#services">1</a></li>',
        )

    def test_api_search(self):
        response = self.client.get("/api/services/?search=holt").json()
        self.assertEqual(response["count"], 0)

        call_command("update_search_indexes")

        response = self.client.get("/api/services/?search=holt").json()
        self.assertEqual(response["count"], 1)

    def test_postcode(self):
        with vcr.use_cassette(
            str(settings.BASE_DIR / "fixtures" / "vcr" / "postcode.yaml"),
            decode_compressed_response=True,
        ):
            # postcode sufficiently near to fake locality
            response = self.client.get("/search?q=w1a 1aa")

            self.assertContains(response, "W1A 1AA")
            self.assertContains(
                response, """<a href="/map#16/51.5186/-0.1438">Map</a>"""
            )
            self.assertContains(response, "Melton Constable")
            self.assertContains(response, "/localities/melton-constable")
            self.assertNotContains(response, "results found for")

            # outcode
            with self.assertNumQueries(4):
                response = self.client.get("/search?q=nr1")
            self.assertContains(
                response, """<a href="/map#16/52.6265/1.3067">Map</a>"""
            )

            # postcode looks valid but doesn't exist
            with self.assertNumQueries(4):
                response = self.client.get("/search?q=w1a 1aj")
            self.assertNotContains(response, "Places near")

    def test_admin_area(self):
        """Admin area containing just one child should redirect to that child"""
        StopUsage.objects.create(service=self.service, stop=self.stop, order=0)
        response = self.client.get("/areas/91")
        self.assertRedirects(response, "/localities/melton-constable")

    def test_district(self):
        """Admin area containing just one child should redirect to that child"""
        response = self.client.get("/districts/91")
        self.assertEqual(response.status_code, 200)

    def test_locality(self):
        StopUsage.objects.create(service=self.service, stop=self.stop, order=0)
        response = self.client.get("/localities/e0048689")
        self.assertContains(response, "<h1>Melton Constable</h1>")
        self.assertContains(response, "/localities/melton-constable")

    def test_stops_api(self):
        response = self.client.get("/api/stops.json")
        self.assertEqual(
            response.json()["results"][0]["long_name"],
            "Melton Constable, adjacent to Bus Shelter",
        )

    def test_stops_json(self):
        # no params - bad request
        response = self.client.get("/stops.json")
        self.assertEqual(response.status_code, 400)

        # bounding box too big - bad request
        response = self.client.get(
            "/stops.json",
            {
                "ymax": "54.9",
                "xmax": "1.1",
                "ymin": "52.8",
                "xmin": "0",
            },
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.get(
            "/stops.json",
            {
                "ymax": "52.9",
                "xmax": "1.1",
                "ymin": "52.8",
                "xmin": "1.0",
            },
        )
        self.assertEqual("FeatureCollection", response.json()["type"])
        self.assertIn("features", response.json())

    def test_zoom_too_low(self):
        """zoom lower than 10"""

        response = self.client.get("/stops/9/255/255.pbf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/x-protobuf")
        self.assertEqual(response.content, b"")

    def test_empty_tile(self):
        """no stops"""

        response = self.client.get("/stops/14/0/0.pbf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/x-protobuf")
        self.assertEqual(response.content, b"")

    def test_tile_contains_active_stop(self):
        """A tile covering an active stop returns it (with current service only)"""

        response = self.client.get("/stops/14/8239/5347.pbf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/x-protobuf")
        # protobuf encodes strings as raw UTF-8 bytes
        self.assertIn(b"2900M114", response.content)
        self.assertIn(b"Melton Constable", response.content)
        # stop with inactive service must not appear
        self.assertNotIn(b"2900M115", response.content)

    def test_stop_view(self):
        response = self.client.get("/stops/2900m114")
        self.assertFalse(response.context_data["departures"])
        self.assertContains(response, "North")
        self.assertContains(response, "Norfolk")
        self.assertContains(response, "Melton Constable, opposite Bus Shelter")

    def test_stop_naptan_code_url(self):
        response = self.client.get("/stops/nfodgjtg")
        self.assertRedirects(response, "/stops/2900M114")

        response = self.client.get("/stops/nfodgjtgploop")
        self.assertEqual(response.status_code, 404)

    def test_inactive_stop(self):
        response = self.client.get("/stops/2900M115")
        self.assertContains(
            response,
            "<h1>Melton Constable, adjacent to Bus Shelter</h1>",
            status_code=404,
        )

    def test_operator_found(self):
        response = self.client.get("/operators/ains")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An airline operator")
        self.assertContains(response, "<h1>Ainsley&#x27;s Chariots</h1>")
        # postal address:
        # self.assertContains(response, "10 King Road<br />Ipswich", html=True)
        # obfuscated email address:
        # self.assertContains(
        #     response,
        #     "&#109;&#97;&#105;&#108;&#116;&#111;&#58;&#97;&#105;"
        #     + "&#110;&#115;&#108;&#101;&#121;&#64;&#101;&#120;&#97;&#109;"
        #     + "&#112;&#108;&#101;&#46;&#99;&#111;&#109;",
        # )
        self.assertContains(response, "http://www.ouibus.com")

    def test_operator_not_found(self):
        """An operator with no services, or that doesn't exist, should should return a 404 response"""
        with self.assertNumQueries(9):
            response = self.client.get("/operators/VENT")  # noc
            self.assertContains(response, "Nu-Venture", status_code=404)

        with self.assertNumQueries(9):
            response = self.client.get("/operators/nu-venture")  # slug
            self.assertContains(response, "Nu-Venture", status_code=404)

        with self.assertNumQueries(3):
            response = self.client.get("/operators/poop")  # doesn't exist
            self.assertEqual(response.status_code, 404)

        with self.assertNumQueries(1):
            response = self.client.get("/operators/POOP")
            self.assertEqual(response.status_code, 404)

    def test_service(self):
        response = self.client.get("/services/45c-holt-norwich")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ouibus")
        # payment methods:
        self.assertContains(response, "euros")
        self.assertContains(response, "Oyster card")
        self.assertContains(response, '"http://example.com"')

        self.assertFalse(response.streaming)

        with override_settings(TEST=False):
            response = self.client.get("/services/45c-holt-norwich")
            self.assertTrue(response.streaming)

    def test_national_express_service(self):
        self.chariots.name = "National Express"
        self.chariots.url = "http://www.nationalexpress.com"
        self.chariots.save()

        response = self.client.get(self.service.get_absolute_url())
        self.assertNotContains(response, "Timing points")
        self.assertContains(response, "Melton Constable, opp Bus Shelter")

        # check for affiliate links
        self.assertEqual(
            response.context_data["links"][0],
            {
                "text": "(ad) Buy tickets at National Express",
                "url": "https://nationalexpress.prf.hn/click/camref:1011ljPYw/pubref:45C",
            },
        )

        response = self.client.get(self.chariots.get_absolute_url())
        self.assertContains(response, ">Tickets (ad)<")
        self.assertContains(
            response, "https://nationalexpress.prf.hn/click/camref:1011ljPYw", 2
        )

    def test_service_redirect(self):
        """An inactive service should redirect to a current service with the same description"""
        with self.assertNumQueries(5):
            response = self.client.get("/services/45B")
        self.assertRedirects(response, "/services/45c-holt-norwich", status_code=301)

        response = self.client.get("/services/1-45-A-y08-9")
        self.assertEqual(response.status_code, 404)

    def test_not_found_redirect(self):
        """Redirect from url missing 'ea_' prefix"""
        response = self.client.get("/services/21-45-A-y08-9")
        self.assertRedirects(response, "/services/45c-holt-norwich")

    def test_service_not_found(self):
        """An inactive service with no replacement should redirect to its operator"""
        with self.assertNumQueries(6):
            response = self.client.get("/services/45A")
        self.assertRedirects(response, "/operators/ainsleys-chariots", status_code=302)

    def test_service_xml(self):
        """I can view the TransXChange XML for a service"""
        response = self.client.get("/services/foo/ea_21-45-A-y08.xml")
        self.assertEqual(response.status_code, 404)

    def test_service_map_data(self):
        # normal service (no trips, so no route links are fetched)
        with self.assertNumQueries(3):
            response = self.client.get(f"/services/{self.service.id}.json")
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 200)

    def test_modes(self):
        """A list of transport modes is turned into English"""
        self.assertContains(
            render(None, "modes.html", {"modes": ["bus"], "noun": "services"}),
            "Bus services",
        )
        self.assertContains(
            render(None, "modes.html", {"noun": "services"}), "Services"
        )
        self.assertContains(
            render(None, "modes.html", {"modes": ["bus", "coach"], "noun": "services"}),
            "Bus and coach services",
        )
        self.assertContains(
            render(
                None,
                "modes.html",
                {"modes": ["bus", "coach", "tram"], "noun": "services"},
            ),
            "Bus, coach and tram services",
        )
        self.assertContains(
            render(
                None,
                "modes.html",
                {"modes": ["bus", "coach", "tram", "cable car"], "noun": "operators"},
            ),
            "Bus, coach, tram and cable car operators",
        )

    def test_sitemap_index(self):
        with self.assertNumQueries(4):
            response = self.client.get("/sitemap.xml")
        self.assertContains(response, "https://testserver/sitemap-operators.xml")
        self.assertContains(response, "https://testserver/sitemap-services.xml")

    def test_sitemap_operators(self):
        with self.assertNumQueries(2):
            response = self.client.get("/sitemap-operators.xml")
        self.assertContains(
            response,
            "<url><loc>https://testserver/operators/ainsleys-chariots</loc><lastmod>2023-02-21</lastmod></url>",
        )

    def test_sitemap_services(self):
        with self.assertNumQueries(2):
            response = self.client.get("/sitemap-services.xml")
        self.assertContains(response, "https://testserver/services/45c-holt-norwich")

    def test_journey(self):
        """Journey planner"""
        with self.assertNumQueries(0):
            response = self.client.get("/journey")

        with self.assertNumQueries(1):
            response = self.client.get("/journey?from_q=melton")
        self.assertContains(response, "melton-constable")

        with self.assertNumQueries(1):
            response = self.client.get("/journey?to_q=melton")
        self.assertContains(response, "melton-constable")

        with self.assertNumQueries(2):
            response = self.client.get("/journey?from_q=melton&to_q=constable")

    def test_version(self):
        response = self.client.get("/version")
        self.assertTrue(response.content)

        with patch.dict("os.environ", {"COMMIT_HASH": "i've had a ploughman's"}):
            response = self.client.get("/version")
        self.assertEqual(
            response.content,
            b"<a href=\"https://github.com/jclgoodwin/bustimes.org/commit/i've had a ploughman's\">"
            b"i've had a ploughman's</a>",
        )

    def test_stop_qr_redirect(self):
        response = self.client.get("/STOP/2900ABC1")
        self.assertRedirects(response, "/stops/2900ABC1", 302, target_status_code=404)

    def test_trailing_slash(self):
        response = self.client.get("/map/")
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/mao/")
        self.assertEqual(response.status_code, 404)
