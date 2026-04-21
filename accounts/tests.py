from http import HTTPStatus
from unittest.mock import patch

from django.core import mail
from django.test import TransactionTestCase, override_settings, Client

from .models import User, Invitation


class RegistrationTest(TransactionTestCase):
    def test_blank_email(self):
        with self.assertNumQueries(0):
            response = self.client.post("/accounts/register/")
        self.assertContains(response, "This field is required")

    @override_settings(
        # use an old, insecure password hasher, because it's fast
        PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    )
    @patch("turnstile.fields.TurnstileField.validate", return_value=True)
    def test_registration(self, mocked_validate):
        dummy_uuid = "b4fcbc02-1920-4d0d-b07b-756db0cb2cd0"

        # invite code not created yet - no registration form
        response = self.client.get(f"/accounts/register/?invite_code={dummy_uuid}")
        self.assertNotContains(response, "Email address")
        self.assertNotContains(response, dummy_uuid)

        # no invite code
        with self.assertNumQueries(1):
            response = self.client.post(
                "/accounts/register/",
                {
                    "email": "rufus@herring.pizza",
                    "turnstile": "foo",
                    "invite_code": dummy_uuid,
                },
            )
        self.assertContains(response, "is not valid or has expired")

        invitation = Invitation.objects.create(
            uuid=dummy_uuid, expires_at="3000-01-01 00:00:00+00:00"
        )
        self.assertEqual(
            invitation.get_absolute_url(),
            f"/accounts/register/?invite_code={dummy_uuid}",
        )

        response = self.client.get(f"/accounts/register/?invite_code={dummy_uuid}")
        self.assertContains(response, "Email address")
        self.assertContains(response, dummy_uuid)

        # create new account successfully:
        with self.assertNumQueries(3):
            response = self.client.post(
                "/accounts/register/",
                {
                    "email": "rufus@herring.pizza",
                    "turnstile": "foo",
                    "invite_code": dummy_uuid,
                },
                headers={"CF-Connecting-IP": "1.2.3.4"},
            )
        self.assertContains(response, "Check your email (rufus@herring.pizza")
        self.assertEqual("bustimes.org account", mail.outbox[0].subject)
        self.assertIn("a bustimes.org account", mail.outbox[0].body)

        user = User.objects.get(email="rufus@herring.pizza")
        user.is_active = False
        user.save()

        with self.assertNumQueries(3):
            # reactivate existing account
            response = self.client.post(
                "/accounts/register/",
                {
                    "email": "RUFUS@HeRRInG.piZZa",
                    "turnstile": "foo",
                    "invite_code": dummy_uuid,
                },
            )

        user.refresh_from_db()
        self.assertEqual(user.username, "rufus@herring.pizza")
        self.assertEqual(user.email, "rufus@herring.pizza")
        self.assertIs(True, user.is_active)
        self.assertEqual(str(user), str(user.id))

        with self.assertNumQueries(3):
            response = self.client.post(
                "/accounts/register/",
                {
                    "email": "ROY@HotMail.com",
                    "turnstile": "foo",
                    "invite_code": dummy_uuid,
                },
            )

        user = User.objects.get(email__iexact="ROY@HotMail.com")
        self.assertEqual(user.username, "ROY@HotMail.com")
        self.assertEqual(user.email, "ROY@hotmail.com")

        self.assertContains(response, "Check your email (ROY@hotmail.com")
        self.assertEqual(3, len(mail.outbox))

        # username (email address) should be case insensitive
        dummy_password = "swim green twenty eggs"
        user.set_password(dummy_password)
        user.save()
        data = {"username": "roY@hoTmail.com", "password": dummy_password}
        with self.assertNumQueries(9):
            response = self.client.post("/accounts/login/", data)
            self.assertEqual(302, response.status_code)

        # test CSDRF middeware
        csrf_client = Client(enforce_csrf_checks=True)
        with self.assertNumQueries(0), self.assertLogs():
            response = csrf_client.post("/accounts/login/", data)
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

        csrf_client.force_login(user)
        with self.assertNumQueries(2), self.assertLogs():
            response = csrf_client.post("/accounts/login/", data)
        self.assertEqual(302, response.status_code)

    def test_update_user(self):
        super_user = User.objects.create(
            username="josh", is_staff=True, is_superuser=True, email="j@example.com"
        )
        other_user = User.objects.create(
            username="ken@example.com",
            trusted=None,
            email="ken@example.com",
        )

        # super user sees change link:

        self.client.force_login(super_user)

        response = self.client.get(other_user.get_absolute_url())

        self.assertContains(response, f'"/vehicles/edits?user={other_user.id}"')
        self.assertContains(response, f'"/vehicles/edits?user={other_user.id}&amp;')

        self.assertContains(response, "/change/")

        # set permissions
        with self.assertNumQueries(6):
            response = self.client.post(other_user.get_absolute_url())

        # trust/distrust in admin
        response = self.client.post(
            "/admin/accounts/user/",
            {
                "action": "trust",
                "_selected_action": [other_user.id],
            },
        )
        other_user.refresh_from_db()
        self.assertTrue(other_user.trusted)

        response = self.client.post(
            "/admin/accounts/user/",
            {
                "action": "distrust",
                "_selected_action": [other_user.id],
            },
        )
        other_user.refresh_from_db()
        self.assertFalse(other_user.trusted)

        self.client.force_login(other_user)

        # normal user can't see email addresses
        response = self.client.get(super_user.get_absolute_url())
        self.assertNotContains(response, "ken@example.com")

        # set username:

        response = self.client.post(
            other_user.get_absolute_url(), {"name": "kenton_schweppes"}
        )
        other_user.refresh_from_db()
        self.assertEqual(other_user.username, "kenton_schweppes")

        # try setting a looong username:
        response = self.client.post(
            other_user.get_absolute_url(),
            {"name": "Hubert Blaine Wolfeschlegelsteinhausenbergerdorff Sr."},
        )
        self.assertContains(
            response, ">Ensure this value has at most 50 characters (it has 53).</"
        )

        # try copying someone else's username
        response = self.client.post(other_user.get_absolute_url(), {"name": "josh"})
        self.assertContains(response, ">Username taken<")

        self.assertContains(response, "That's you!")

        self.client.post(other_user.get_absolute_url(), {"name": ""})
        other_user.refresh_from_db()
        self.assertEqual(other_user.username, "ken@example.com")

        # user can delete own account:

        with self.assertNumQueries(6):
            self.client.post(other_user.get_absolute_url(), {"confirm_delete": False})
            # confirm delete not ticked
        other_user.refresh_from_db()
        self.assertTrue(other_user.is_active)

        with self.assertNumQueries(7):
            self.client.post(other_user.get_absolute_url(), {"confirm_delete": "on"})
        other_user.refresh_from_db()
        self.assertFalse(other_user.is_active)

    def test_password_reset(self):
        with self.assertNumQueries(0):
            response = self.client.get("/accounts/password_reset/")
        self.assertContains(response, "Reset your password")
        self.assertContains(response, "Email address")

        with self.assertNumQueries(1):
            response = self.client.post(
                "/accounts/password_reset/",
                {
                    "email": "rufus@herring.pizza",
                },
            )
        self.assertEqual(response.url, "/accounts/password_reset/done/")

        with self.assertNumQueries(0):
            response = self.client.get(response.url)
        self.assertContains(
            response,
            "<p>We’ve emailed you instructions for setting your password, if an account exists with the email you "
            "entered. You should receive them shortly.</p>",
        )
        self.assertEqual([], mail.outbox)
