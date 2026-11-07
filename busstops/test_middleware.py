from http import HTTPStatus

from django.test import TestCase, modify_settings, override_settings
from multidb.pinning import this_thread_is_pinned

from accounts.models import User


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

    def test_cdn_cache_control(self):
        cdn = "public, max-age=600, stale-while-revalidate=3600, stale-if-error=86400"

        for path in ("/", "/map"):
            with self.subTest(path=path):
                # anonymous user - cacheable, varies by country
                response = self.client.get(path)
                self.assertEqual(response.headers["CDN-Cache-Control"], cdn)
                self.assertIn("CF-IPCountry", response.headers["Vary"])
                self.assertNotIn("private", response.headers.get("Cache-Control", ""))

                # other (e.g. ad tech) cookies don't matter
                self.client.cookies["_ga"] = "GA1.1.123"
                response = self.client.get(path)
                self.assertEqual(response.headers["CDN-Cache-Control"], cdn)

                # a stale/invalid session cookie - not cacheable, just in case
                self.client.cookies["sessionid"] = "nonsense"
                response = self.client.get(path)
                self.assertEqual(
                    response.headers["CDN-Cache-Control"], "private, no-store"
                )
                self.assertIn("private", response.headers["Cache-Control"])
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.client.cookies.clear()

        # logged in user - never cacheable
        user = User.objects.create(username="josh")
        self.client.force_login(user)
        for path in ("/", "/map"):
            with self.subTest(path=path, logged_in=True):
                response = self.client.get(path)
                self.assertEqual(
                    response.headers["CDN-Cache-Control"], "private, no-store"
                )
                self.assertIn("private", response.headers["Cache-Control"])
                self.assertNotIn("public", response.headers["Cache-Control"])

    def test_cdn_cache_control_sets_cookie(self):
        """a response that sets a cookie must never be cached"""
        from django.http import HttpResponse
        from django.test import RequestFactory

        from .middleware import AnonymousCdnCacheMiddleware, cdn_cache_control

        @cdn_cache_control(max_age=60)
        def view(request):
            response = HttpResponse("hello")
            response.set_cookie("csrftoken", "abc")
            return response

        request = RequestFactory().get("/")
        response = AnonymousCdnCacheMiddleware(view)(request)
        self.assertEqual(response.headers["CDN-Cache-Control"], "private, no-store")

        # undecorated views are left alone
        request = RequestFactory().get("/")
        response = AnonymousCdnCacheMiddleware(lambda r: HttpResponse())(request)
        self.assertNotIn("CDN-Cache-Control", response.headers)

    def test_cdn_cache_control_safety_net(self):
        """a view that sets its own public CDN-Cache-Control header, and touches
        the session, is overruled for requests with a session cookie"""
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.http import HttpResponse
        from django.test import RequestFactory

        from .middleware import AnonymousCdnCacheMiddleware

        def view(request):
            request.session.get("_auth_user_id")  # like rendering {{ user }}
            response = HttpResponse("user 1")
            response["CDN-Cache-Control"] = "public, max-age=3600"
            response["Cache-Control"] = "public, max-age=60"
            return response

        middleware = AnonymousCdnCacheMiddleware(SessionMiddleware(view))

        response = middleware(RequestFactory().get("/"))
        self.assertEqual(response.headers["CDN-Cache-Control"], "public, max-age=3600")

        request = RequestFactory().get("/", headers={"Cookie": "sessionid=abc"})
        response = middleware(request)
        self.assertEqual(response.headers["CDN-Cache-Control"], "private, no-store")
        self.assertNotIn("public", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])
