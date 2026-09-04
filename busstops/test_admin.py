from django.contrib.admin import site
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from bustimes.models import Route, RouteLink

from .models import DataSource, Service, StopPoint


class BusStopsAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = DataSource.objects.create()

        cls.service_a = Service.objects.create(
            line_name="129", description="Frankby Cemetery - Liscard"
        )
        cls.service_b = Service.objects.create(
            line_name="129A", description="Frankby - Moreton - Liscard"
        )

        stop_a = StopPoint.objects.create(
            atco_code="2902", active=True, common_name="Sandy Corner"
        )
        stop_b = StopPoint.objects.create(
            atco_code="2903", active=True, common_name="Leafy Hollow"
        )
        RouteLink.objects.create(
            from_stop=stop_a,
            to_stop=stop_b,
            service=cls.service_a,
            geometry="LINESTRING(1.2 51.1,1.1 51.2)",
        )
        RouteLink.objects.create(
            from_stop=stop_a,
            to_stop=stop_b,
            service=cls.service_b,
            geometry="LINESTRING(1.3 51.1,1.1 51.3)",
        )
        Route.objects.create(
            source=cls.source,
            service_code="129",
            code="129",
            line_name="129",
            service=cls.service_a,
            revision_number=1,
        )
        Route.objects.create(
            source=cls.source,
            service_code="129",
            code="129A",
            line_name="129A",
            service=cls.service_b,
            revision_number=1,
        )

        cls.staff_user = User.objects.create(
            username="josh", is_staff=True, is_superuser=True, email="j@example.com"
        )

    def setUp(self):
        ContentType.objects.clear_cache()

    def make_url(self, site, model, page: str) -> str:
        return reverse(
            f"{site.name}:{model._meta.app_label}_{model._meta.model_name}_{page}"
        )

    def test_everything(self):
        self.client.force_login(self.staff_user)

        for model in site._registry:
            url = self.make_url(site, model, "changelist")
            response = self.client.get(url, {"q": "blah"})
            self.assertEqual(response.status_code, 200)

            url = self.make_url(site, model, "add")
            response = self.client.get(url, {"q": "blah"})
            self.assertEqual(response.status_code, 200)

    def test_merge_and_unmerge(self):
        self.client.force_login(self.staff_user)

        self.client.post(
            "/admin/busstops/service/",
            {
                "action": "merge",
                "_selected_action": [self.service_a.id, self.service_b.id],
            },
        )
        response = self.client.get("/admin/busstops/service/")
        self.assertEqual(
            next(iter(response.context["messages"])).message,
            "merged <QuerySet [<Service: 129A - Frankby - Moreton - Liscard>]> into 129 - Frankby Cemetery - Liscard",
        )

        # merged into 1:
        self.assertEqual(Service.objects.count(), 1)

        # unmerge back into 2:
        self.client.post(
            "/admin/busstops/service/",
            {
                "action": "unmerge",
                "_selected_action": [self.service_a.id],
            },
        )
        self.assertEqual(Service.objects.count(), 2)

    def test_split_service_filter(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            "/admin/busstops/service/?split=1",
        )
        self.assertContains(response, "Frankby Cemetery - Liscard")

    def test_has_current_route_filter(self):
        self.client.force_login(self.staff_user)

        url = "/admin/busstops/service/?has_current_route="

        response = self.client.get(f"{url}1")
        self.assertEqual(response.context_data["cl"].result_count, 2)

        # a newer revision of service_a's route, which has expired
        Route.objects.create(
            source=self.source,
            service_code="129",
            code="129 2",
            line_name="129",
            service=self.service_a,
            revision_number=2,
            start_date="2024-01-01",
            end_date="2024-06-01",
        )

        # service_a's other route has no end date, but is superseded
        response = self.client.get(f"{url}0")
        self.assertEqual(
            [service.line_name for service in response.context_data["cl"].result_list],
            ["129"],
        )

        response = self.client.get(f"{url}1")
        self.assertEqual(
            [service.line_name for service in response.context_data["cl"].result_list],
            ["129A"],
        )

    def test_data_source_admin(self):
        url = "/admin/busstops/datasource/"

        self.client.force_login(self.staff_user)
        response = self.client.get(url)
        self.assertContains(response, ">0<")  # services
        self.assertContains(response, ">2<")  # routes

        self.client.post(
            url,
            {
                "action": "delete_routes",
                "_selected_action": [self.source.id],
            },
        )
        response = self.client.get(url)

        with self.assertNumQueries(8):
            self.client.post(
                url,
                {
                    "action": "remove_datetimes",
                    "_selected_action": [self.source.id],
                },
            )

    def test_routelink_admin(self):
        url = "/admin/bustimes/routelink/"

        self.client.force_login(self.staff_user)

        response = self.client.get(url + "?dodgy=from_stop")
        self.assertEqual(response.context_data["cl"].result_count, 0)

        # move the stop away from the route link so it's "dodgy"
        StopPoint.objects.all().update(latlong="POINT(1.9 51.9)")

        response = self.client.get(url + "?dodgy=from_stop")
        self.assertEqual(response.context_data["cl"].result_count, 2)

        response = self.client.get(url + "?dodgy=to_stop")
        self.assertEqual(response.context_data["cl"].result_count, 2)

        url = RouteLink.objects.first().get_absolute_url()

        res = self.client.get(url)
        self.assertContains(res, "from Sandy Corner")
        self.assertContains(res, "to Leafy Hollow")
