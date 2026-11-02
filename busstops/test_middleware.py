from http import HTTPStatus

from django.test import TestCase, modify_settings, override_settings
from multidb.pinning import this_thread_is_pinned


class MiddlewareTest(TestCase):
    @override_settings(ALLOWED_HOSTS=["bustimes.org"])
    def test_health_check(self):
        response = self.client.get("/")
        # host header check is not allowed
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)

        response = self.client.get("/up")
        # host header check is bypassed
        self.assertContains(response, "up!")

    def test_404_fallback(self):
        response = self.client.get("/static/css/style.p00p4nt5.css")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
        self.assertEqual(
            response.headers["cache-control"],
            "max-age=0, no-cache, no-store, must-revalidate, private",
        )

    def test_404_no_fallback(self):
        response = self.client.get("/static/css/poo.p00p4nt5.css")
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
        self.assertEqual(
            response.headers["cache-control"],
            "max-age=0, no-cache, no-store, must-revalidate, private",
        )

    @modify_settings(
        MIDDLEWARE={
            "append": "busstops.middleware.pin_db_middleware",
        }
    )
    def test_pin_db(self):
        self.assertFalse(this_thread_is_pinned())

        self.client.get("/admin/login/")
        self.assertTrue(this_thread_is_pinned())

        self.client.get("/")
        self.assertFalse(this_thread_is_pinned())

    def test_reporting_endpoints(self):
        response = self.client.get("/cookies")
        self.assertEqual(
            response.headers["Reporting-Endpoints"],
            'default="http://testserver/reports"',
        )
        self.assertContains(response, "localStorage.getItem(KEY)")

        response = self.client.get("/robots.txt")
        self.assertNotIn("Reporting-Endpoints", response.headers)
