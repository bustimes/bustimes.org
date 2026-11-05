from django.contrib.admin.models import LogEntry
from django.test import TestCase

from accounts.models import User
from busstops.models import AdminArea, DataSource, Region

from .models import Consequence, Situation


class SituationAdminTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            username="josh", is_staff=True, is_superuser=True, email="j@example.com"
        )
        region = Region.objects.create(id="SW", name="South West")
        cornwall = AdminArea.objects.create(
            id=73, atco_code="080", name="Cornwall", region=region
        )
        AdminArea.objects.create(id=76, atco_code="110", name="Devon", region=region)
        source = DataSource.objects.create(name="Bus Open Data")
        cls.storm, cls.test_message = (
            Situation.objects.create(
                source=source, summary=summary, publication_window=("2026-10-22", None)
            )
            for summary in ("Storm Austen", "Test Message")
        )
        consequence = Consequence.objects.create(situation=cls.storm)
        consequence.admin_areas.add(cornwall)

    def test_situation_admin(self):
        self.client.force_login(self.user)
        url = "/admin/disruptions/situation/"

        response = self.client.get(f"{url}?consequence__admin_areas__id__exact=73")
        self.assertContains(response, "Storm Austen")
        self.assertNotContains(response, "Test Message")
        # only areas with situations
        self.assertContains(response, "Cornwall")
        self.assertNotContains(response, "Devon")

        # hide one while the list is filtered to current ones
        response = self.client.post(
            f"{url}?current__exact=1",
            {"action": "current_false", "_selected_action": [self.test_message.id]},
        )
        self.test_message.refresh_from_db()
        self.assertFalse(self.test_message.current)
        entry = LogEntry.objects.get(object_id=self.test_message.id)
        self.assertEqual(entry.get_change_message(), "Changed current.")

        self.client.post(
            url,
            {"action": "current_true", "_selected_action": [self.test_message.id]},
        )
        self.test_message.refresh_from_db()
        self.assertTrue(self.test_message.current)
        self.assertEqual(LogEntry.objects.count(), 2)
