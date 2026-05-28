from http import HTTPStatus
from unittest.mock import patch

import fakeredis
import time_machine
from datetime import datetime
from django.contrib.gis.geos import Point
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from accounts.models import User
from busstops.models import DataSource, Operator, OperatorGroup, Region, Service

from .models import (
    Livery,
    Vehicle,
    VehicleFeature,
    VehicleJourney,
    VehicleLocation,
    VehicleRevision,
    VehicleRevisionFeature,
    VehicleType,
)


@patch(
    "vehicles.views.redis_client",
    fakeredis.FakeStrictRedis(),
)
class VehiclesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.datetime = "2020-10-19 23:47+00:00"

        source = DataSource.objects.create(name="HP", datetime=cls.datetime)

        ea = Region.objects.create(id="EA", name="East Anglia")

        cls.wifi = VehicleFeature.objects.create(name="Wi-Fi")
        cls.usb = VehicleFeature.objects.create(name="USB")

        group = OperatorGroup.objects.create(
            name="Madrigal Electromotive", slug="madrigal-electromotive"
        )
        cls.bova = Operator.objects.create(
            region=ea,
            name="Bova and Over",
            noc="BOVA",
            slug="bova-and-over",
            group=group,
        )
        cls.lynx = Operator.objects.create(
            region=ea, name="Lynx", noc="LYNX", slug="lynx", group=group
        )
        cls.chicken = Operator.objects.create(
            region=ea, name="Chicken Bus", noc="CLUCK", slug="chicken"
        )

        tempo = VehicleType.objects.create(name="Optare Tempo", fuel="diesel")
        spectra = VehicleType.objects.create(
            name="Optare Spectra",
            fuel="diesel",
            style="double decker",
        )

        service = Service.objects.create(
            service_code="49",
            slug="spixworth-hunworth-happisburgh",
            region=ea,
            tracking=True,
            description="Spixworth - Hunworth - Happisburgh",
        )
        service.operator.add(cls.lynx)
        service.operator.add(cls.bova)

        cls.vehicle_1 = Vehicle.objects.create(
            code="2",
            fleet_number=1,
            reg="FD54JYA",
            vehicle_type=tempo,
            colours="#FF0000",
            notes="Trent Barton",
            operator=cls.lynx,
            branding="",
        )
        cls.livery = Livery.objects.create(
            name="black with lemon piping",
            colours="#FF0000 #0000FF",
            colour="#FF0000",
            published=True,
        )
        cls.vehicle_2 = Vehicle.objects.create(
            code="50",
            fleet_number=50,
            reg="UWW2X",
            livery=cls.livery,
            vehicle_type=spectra,
            operator=cls.lynx,
        )

        cls.vehicle_3 = Vehicle.objects.create(
            code="10", branding="Coastliner", colours="#c0c0c0"
        )

        cls.journey = VehicleJourney.objects.create(
            vehicle=cls.vehicle_1,
            datetime=cls.datetime,
            date="2020-10-20",
            source=source,
            service=service,
            route_name="2",
        )
        VehicleJourney.objects.create(
            vehicle=cls.vehicle_1,
            datetime="2020-10-16 12:00:00+00:00",
            date="2020-10-16",
            source=source,
            service=service,
            route_name="2",
        )
        VehicleJourney.objects.create(
            vehicle=cls.vehicle_1,
            datetime="2020-10-20 12:00:00+00:00",
            date="2020-10-20",
            source=source,
            service=service,
            route_name="2",
        )

        cls.vehicle_1.latest_journey = cls.journey
        cls.vehicle_1.save()

        cls.vehicle_1.features.set([cls.wifi])

        cls.staff_user = User.objects.create(
            username="josh",
            is_staff=True,
            is_superuser=True,
            email="j@example.com",
            date_joined="2020-10-19 23:47+00:00",
        )
        cls.trusted_user = User.objects.create(
            username="norma", trusted=True, email="n@example.com", score=2
        )
        cls.trusted_user.user_permissions.add(
            Permission.objects.get(codename="add_vehiclerevision"),
            Permission.objects.get(codename="change_vehiclerevision"),
        )
        cls.user = User.objects.create(
            username="ken",
            trusted=None,
            email="ken@example.com",
            date_joined="2020-10-19 23:47+00:00",
        )
        cls.untrusted_user = User.objects.create(
            username="clem", trusted=False, email="c@example.com"
        )

    def test_untrusted_user(self):
        self.client.force_login(self.untrusted_user)

        with self.assertNumQueries(2):
            response = self.client.get(self.vehicle_1.get_edit_url())
        self.assertEqual(response.status_code, 403)

    def test_parent(self):
        with self.assertNumQueries(5):
            response = self.client.get("/groups/Madrigal Electromotive/vehicles")
        self.assertContains(response, "Lynx")
        self.assertContains(response, "Madrigal Electromotive")
        self.assertContains(response, "Optare")

        with self.assertNumQueries(2):
            response = self.client.get("/groups/Shatton Group/vehicles")
        self.assertEqual(404, response.status_code)

    def test_fleet_lists(self):
        # operator has no vehicles
        with self.assertNumQueries(2):
            response = self.client.get("/operators/bova-and-over/vehicles")
            self.assertEqual(404, response.status_code)
            self.assertFalse(str(response.context["exception"]))

        # operator doesn't exist
        with self.assertNumQueries(2):
            response = self.client.get("/operators/shatton-east/vehicles")
            self.assertEqual(404, response.status_code)

        # some new vehicles for testing age-based ordering
        Vehicle.objects.bulk_create(
            [
                Vehicle(reg="SA60TWP", code="SA60TWP", operator=self.lynx),
                Vehicle(reg="BB74BUS", code="BB74BUS", operator=self.lynx),
                Vehicle(reg="YX24ANV", code="YX24ANV", operator=self.lynx),
                Vehicle(reg="K292KEX", code="K292KEX", operator=self.lynx),
                Vehicle(reg="YN14ANV", code="YN14ANV", operator=self.lynx),
                Vehicle(reg="T125OAH", code="T125OAH", operator=self.lynx),
                Vehicle(fleet_code="DE69", code="DE69", operator=self.lynx),
                Vehicle(fleet_code="G 2434", code="G_2434", operator=self.lynx),
                Vehicle(fleet_code="J 1221", code="J_121", operator=self.lynx),
            ]
        )

        vehicle = Vehicle.objects.get(code="DE69")
        self.assertEqual(vehicle.get_next().code, "G_2434")
        self.assertEqual(vehicle.get_previous().code, "50")

        # last seen today - should only show time, should link to map
        with (
            time_machine.travel("2020-10-20 12:00+01:00"),
            self.assertNumQueries(3),
            override_settings(ALLOW_VEHICLE_NOTES_OPERATORS=("LYNX",)),
        ):
            response = self.client.get("/operators/lynx/vehicles")

        vehicles = response.context["vehicles"]
        self.assertEqual(vehicles[0].reg, "UWW2X")
        self.assertEqual(str(vehicles[1]), "J 1221")
        self.assertEqual(str(vehicles[2]), "G 2434")
        self.assertEqual(str(vehicles[3]), "DE69")
        self.assertEqual(vehicles[4].reg, "K292KEX")  # age order
        self.assertEqual(vehicles[5].reg, "T125OAH")
        self.assertEqual(vehicles[6].reg, "SA60TWP")
        self.assertEqual(vehicles[7].reg, "YN14ANV")
        self.assertEqual(vehicles[8].reg, "YX24ANV")
        self.assertEqual(vehicles[9].reg, "BB74BUS")
        self.assertEqual(vehicles[10].reg, "FD54JYA")  # notes order

        self.assertNotContains(response, "20 Oct")
        self.assertContains(response, "00:47")
        self.assertContains(response, "/operators/lynx/map")
        self.assertContains(response, "/vehicles/edits?operator=LYNX")
        self.assertContains(response, "/operators/lynx/map")

        with self.assertNumQueries(7):
            response = self.client.get("/operators/lynx")
        self.assertContains(response, "/operators/lynx/vehicles")
        self.assertNotContains(response, "/operators/lynx/map")

        # last seen yesterday - should show date
        with time_machine.travel("2020-10-21 00:10+01:00"), self.assertNumQueries(3):
            response = self.client.get("/operators/lynx/vehicles")
        self.assertContains(response, "20 Oct")
        self.assertNotContains(response, "/operators/lynx/map")

    def test_vehicle_views(self):
        with self.assertNumQueries(7):
            response = self.client.get(self.vehicle_1.get_absolute_url() + "?date=poop")
        self.assertContains(response, "Optare Tempo")
        self.assertContains(response, "Trent Barton")
        self.assertContains(response, "#FF0000")

        self.assertContains(response, ">00:47<")
        self.assertContains(response, ">13:00<")

        with self.assertNumQueries(6):
            response = self.client.get(self.vehicle_2.get_absolute_url())
        self.assertEqual(200, response.status_code)

        # can't connect to redis - no drama
        with (
            override_settings(REDIS_URL="redis://localhose:69"),
            self.assertNumQueries(4),
        ):
            response = self.client.get(
                f"/vehicles/{self.vehicle_1.id}/journeys/{self.journey.id}.json"
            )
        self.assertEqual(
            {
                "code": "",
                "current": True,
                "datetime": "2020-10-20T00:47:00+01:00",
                "destination": "",
                "direction": "",
                "next": {
                    "datetime": "2020-10-20T13:00:00+01:00",
                    "id": self.journey.id + 2,
                },
                # "previous": {
                #     "datetime": "2020-10-16T13:00:00+01:00",
                #     "id": self.journey.id + 1,
                # },
                "route_name": "2",
                "service_id": self.journey.service_id,
                "trip_id": None,
                "vehicle_id": self.journey.vehicle_id,
                "stops": [],
            },
            response.json(),
        )

        self.journey.refresh_from_db()
        self.assertEqual(str(self.journey), "19 Oct 20 23:47 2  ")
        self.assertEqual(
            self.journey.get_absolute_url(),
            f"/vehicles/{self.vehicle_1.id}?date=2020-10-20#journey-{self.journey.id}",
        )

        response = self.client.get(f"/api/vehiclejourneys/{self.journey.id}.json")
        self.assertEqual(response.json()["vehicle"]["reg"], "FD54JYA")

    def test_location_json(self):
        location = VehicleLocation(latlong=Point(0, 51))
        location.id = 1
        location.journey = self.journey
        location.datetime = datetime.fromisoformat(self.datetime)

        self.assertEqual(str(location), "19 Oct 2020 23:47:00")

        self.assertEqual(location.get_redis_json()["coordinates"], (0.0, 51.0))

        location.occupancy = "Seats available"
        self.assertEqual(location.get_redis_json()["seats"], "Seats available")

        location.wheelchair_occupancy = 0
        location.wheelchair_capacity = 0
        self.assertNotIn("wheelchair", location.get_redis_json())

        location.wheelchair_capacity = 1
        self.assertEqual(location.get_redis_json()["wheelchair"], "free")

    def test_vehicle_json(self):
        vehicle = Vehicle.objects.get(id=self.vehicle_2.id)
        vehicle.feature_names = "foo, bar"

        self.assertEqual(vehicle.get_json()["features"], "Double decker<br>foo, bar")

        vehicle = Vehicle.objects.get(id=self.vehicle_1.id)
        vehicle.feature_names = ""

        self.assertEqual(vehicle.get_json()["css"], "#FF0000")

        vehicle.colours = "#000000 #FFFFFF #FFFFFF"
        self.assertEqual(
            vehicle.get_json()["colour"], "#FFFFFF"
        )  # most frequent colour in list of colours

    def test_vehicle_admin(self):
        self.client.force_login(self.staff_user)

        # test copy type, livery actions
        self.client.post(
            "/admin/vehicles/vehicle/",
            {
                "action": "copy_type",
                "_selected_action": [self.vehicle_1.id, self.vehicle_2.id],
            },
        )
        self.client.post(
            "/admin/vehicles/vehicle/",
            {
                "action": "copy_livery",
                "_selected_action": [self.vehicle_1.id, self.vehicle_2.id],
            },
        )
        self.client.post(
            "/admin/vehicles/vehicle/",
            {
                "action": "spare_ticket_machine",
                "_selected_action": [self.vehicle_1.id, self.vehicle_2.id],
            },
        )
        response = self.client.get("/admin/vehicles/vehicle/")
        self.assertContains(response, "Copied Optare Spectra to 2 vehicles.")
        self.assertContains(response, "Copied black with lemon piping to 2 vehicles.")

        # spare ticket machine - some fields not editable
        response = self.client.get(self.vehicle_1.get_edit_url())
        self.assertNotContains(response, "id_reg")
        self.assertNotContains(response, "livery")

        response = self.client.get("/operators/lynx/vehicles")
        self.assertContains(response, "<td>Spare ticket machine</td>")

        # test make livery
        self.client.post(
            "/admin/vehicles/vehicle/",
            {"action": "make_livery", "_selected_action": [self.vehicle_1.id]},
        )
        response = self.client.get("/admin/vehicles/vehicle/")
        self.assertContains(response, "Select a vehicle with colours and branding.")
        self.client.post(
            "/admin/vehicles/vehicle/",
            {"action": "make_livery", "_selected_action": [self.vehicle_3.id]},
        )
        response = self.client.get("/admin/vehicles/vehicle/")
        self.assertContains(response, "Updated 1 vehicles.")

        # test merging 2 vehicles:

        duplicate_1 = Vehicle.objects.create(reg="SA60TWP", code="60", withdrawn=True)
        duplicate_2 = Vehicle.objects.create(reg="SA60TWP", code="SA60TWP")
        duplicate_3 = Vehicle.objects.create(code="TEST")

        self.assertEqual(Vehicle.objects.count(), 6)

        response = self.client.get("/admin/vehicles/vehicle/?duplicate=reg")
        self.assertContains(response, '2 results (<a href="?">6 total</a>')

        response = self.client.get("/admin/vehicles/vehicle/?duplicate=code")
        self.assertContains(response, '0 results (<a href="?">6 total</a>')

        response = self.client.get("/admin/vehicles/vehicle/?duplicate=fleet_code")
        self.assertContains(response, '0 results (<a href="?">6 total</a>')

        response = self.client.get("/admin/vehicles/vehicle/?duplicate=operator")
        self.assertContains(response, '0 results (<a href="?">6 total</a>')

        # search (fleet_code has a nondeterministic collation, so LIKE
        # would fail without special handling)
        Vehicle.objects.filter(id=self.vehicle_1.id).update(fleet_code="DE69")
        response = self.client.get("/admin/vehicles/vehicle/?q=DE69")
        self.assertContains(response, '1 result (<a href="?">6 total</a>')

        self.client.post(
            "/admin/vehicles/vehicle/",
            {
                "action": "deduplicate",
                "_selected_action": [duplicate_1.id, duplicate_2.id, duplicate_3.id],
            },
        )
        self.assertEqual(Vehicle.objects.count(), 5)
        merged = Vehicle.objects.get(reg="SA60TWP")
        self.assertEqual(merged.slug, "none-sa60twp")
        self.assertEqual(merged.code, "SA60TWP")
        self.assertFalse(merged.withdrawn)
        self.assertEqual(merged.id, duplicate_1.id)
        self.assertEqual("none-60", merged.vehiclecode_set.get().code)

        self.client.post(
            "/admin/vehicles/vehicle/",
            {
                "action": "lock",
                "_selected_action": [merged.id],
            },
        )
        self.client.post(
            "/admin/vehicles/vehicle/",
            {
                "action": "unlock",
                "_selected_action": [merged.id],
            },
        )

    def test_vehiclejourneys_admin(self):
        self.client.force_login(self.staff_user)

        # list nothing journeys if no filters have been selected
        response = self.client.get("/admin/vehicles/vehiclejourney/")
        self.assertContains(response, "0 vehicle journeys")

    def test_livery_admin(self):
        self.client.force_login(self.staff_user)

        response = self.client.get("/admin/vehicles/livery/")
        self.assertContains(
            response, '<td class="field-name">black with lemon piping</td>'
        )
        self.assertContains(response, '<td class="field-vehicles">1</td>')

        livery_2 = Livery.objects.create()

        response = self.client.post(
            "/admin/vehicles/livery/",
            {
                "action": "merge",
                "_selected_action": [
                    self.livery.id,
                    livery_2.id,
                ],
            },
            follow=True,
        )
        self.assertEqual(
            list(response.context["messages"])[0].message,
            "You can only merge liveries that are the same",
        )

        livery_2.colours = self.livery.colours
        livery_2.left_css = self.livery.left_css
        livery_2.right_css = self.livery.right_css
        livery_2.save()

        response = self.client.post(
            "/admin/vehicles/livery/",
            {
                "action": "merge",
                "_selected_action": [
                    self.livery.id,
                    livery_2.id,
                ],
            },
            follow=True,
        )
        self.assertEqual(list(response.context["messages"])[0].message, "Merged")

    def test_vehicle_type_admin(self):
        self.client.force_login(self.staff_user)

        response = self.client.get("/admin/vehicles/vehicletype/")
        self.assertContains(response, "Optare Spectra")
        self.assertContains(response, '<td class="field-vehicles">1</td>', 2)

        self.client.post(
            "/admin/vehicles/vehicletype/",
            {
                "action": "merge",
                "_selected_action": [
                    self.vehicle_1.vehicle_type_id,
                    self.vehicle_2.vehicle_type_id,
                ],
            },
        )
        response = self.client.get("/admin/vehicles/vehicletype/")
        self.assertContains(response, '<td class="field-vehicles">2</td>', 1)
        self.assertContains(response, '<td class="field-vehicles">0</td>', 1)

    def test_journey_admin(self):
        self.client.force_login(self.staff_user)

        response = self.client.get("/admin/vehicles/vehiclejourney/?trip__isnull=1")
        self.assertContains(response, "0 of 3 selected")

    def test_search(self):
        response = self.client.get("/search?q=fd54jya")
        self.assertContains(response, "1 vehicle")

        response = self.client.get("/search?q=11111")
        self.assertNotContains(response, "vehicle")

    def test_liveries_css(self):
        response = self.client.get("/liveries.44.css")

        self.assertEqual(
            response.text,
            f""".livery-{self.livery.id}{{color:#fff;background:linear-gradient(90deg,red 50%,#00f 50%);\
&.right{{background:linear-gradient(270deg,red 50%,#00f 50%)}}}}\n""",
        )

    def test_vehicle_edit_1(self):
        response = self.client.get("/vehicles/edits?status=pending")
        self.assertContains(response, "pending is not one of the available choices")

        url = self.vehicle_1.get_edit_url()

        with self.assertNumQueries(0):
            response = self.client.get(url)
        self.assertRedirects(response, f"/accounts/login/?next={url}", 302)

        with self.assertNumQueries(0):
            response = self.client.get(response.url)
        self.assertContains(response, "Log in")

        self.client.force_login(self.staff_user)

        with self.assertNumQueries(11):
            response = self.client.get(url)
        self.assertNotContains(response, "already")

        initial = {
            "fleet_number": "1",
            "reg": "FD54JYA",
            "vehicle_type": self.vehicle_1.vehicle_type_id,
            "other_vehicle_type": str(self.vehicle_1.vehicle_type),
            "features": self.wifi.id,
            "operator": self.lynx.noc,
            "other_colour": "#FF0000",
            "notes": "Trent Barton",
        }

        # edit nothing
        with self.assertNumQueries(14):
            response = self.client.post(url, initial)
        self.assertFalse(response.context["form"].has_changed())
        self.assertContains(response, "You haven&#x27;t changed anything")
        self.assertNotContains(response, "already")

        # edit nothing but summary
        initial["summary"] = (
            "Poo poo pants\r\r\n"
            "https://www.flickr.com/pho"
            "tos/goodwinjoshua/51046126023/in/photolist-2n3qgFa-2n2eJqm-2mL2ptW-2k"
            "LLJR6-2hXgjnC-2hTkN9R-2gRxwqk-2g3ut3U-29p2ZiJ-ZrgH1M-WjEYtY-SFzez8-Sh"
            "KDfn-Pc9Xam-MvcHsg-2mvhSdj-FW3FiA-z9Xy5u-v8vKmD-taSCD6-uJFzob-orkudc-"
            "mjXUYS-i2nbH2-hyrrxD-fabgxp-fbM7Gf-eR4fGA-eHtfHb-eAreVh-ekmQ1E-e8sxcb"
            "-aWWgKX-aotzn6-aiadaL-adWEKk/ blah"
        )

        with self.assertNumQueries(14):
            response = self.client.post(url, initial)
        self.assertContains(response, "You haven&#x27;t changed anything")
        self.assertNotContains(response, "already")
        self.assertNotContains(response, "already")

        # edit fleet number
        initial["fleet_number"] = "2"
        initial["previous_reg"] = "bean"
        with self.assertNumQueries(14):
            response = self.client.post(url, initial)
        self.assertIsNone(response.context["form"])
        self.assertContains(response, "<strong>fleet number</strong>")
        revision = response.context["revision"]
        self.assertEqual(str(revision), "fleet number: 1 → 2, previous reg:  → BEAN")
        self.assertEqual(
            revision.message,
            """Poo poo pants

https://www.flickr.com/photos/goodwinjoshua/51046126023/ blah""",
        )

        # should not create an edit
        with self.assertRaises(ValueError):
            initial["colours"] = "#FFFF00"
            response = self.client.post(url, initial)

        self.assertEqual(1, VehicleRevision.objects.count())

        response = self.client.get("/admin/accounts/user/")
        self.assertContains(
            response,
            '<td class="field-revisions">'
            f'<a href="/admin/vehicles/vehiclerevision/?user={self.staff_user.id}&">1</a></td>'
            '<td class="field-disapproved">'
            f'<a href="/admin/vehicles/vehiclerevision/?user={self.staff_user.id}&disapproved=True">0</a></td>'
            '<td class="field-pending">'
            f'<a href="/admin/vehicles/vehiclerevision/?user={self.staff_user.id}&pending=True">1</a></td>',
        )

        response = self.client.get("/admin/vehicles/vehiclerevision/")
        self.assertContains(response, "1 vehicle revision")
        self.assertContains(response, ": josh<")

        response = self.client.get(
            "/admin/vehicles/vehiclerevision/?change=changes__reg"
        )
        self.assertContains(response, "0 vehicle revisions")

        with self.assertNumQueries(5):
            response = self.client.get("/vehicles/edits?status=pending")
        self.assertContains(response, "<strong>previous reg</strong>", html=True)
        self.assertContains(response, "BEAN")

        del initial["colours"]

        # apply edit
        self.client.force_login(self.trusted_user)
        response = self.client.post(f"/vehicles/revisions/{revision.id}/apply")
        revision.refresh_from_db()
        self.assertFalse(revision.pending)
        self.assertFalse(revision.disapproved)

        response = self.client.get(revision.vehicle.get_absolute_url())
        self.assertContains(response, "B EAN")
        self.assertContains(response, "Wi-Fi")
        self.assertEqual(revision.vehicle.fleet_number, 2)

        self.client.force_login(self.staff_user)

        # staff user can edit branding and notes
        initial["branding"] = "Crag Hopper"
        initial["notes"] = "West Coast Motors"
        with self.assertNumQueries(14):
            response = self.client.post(url, initial)
        self.assertContains(response, "<strong>notes</strong>")
        self.assertContains(response, "from Trent Barton")
        self.assertContains(response, "to West Coast Motors")
        self.assertContains(response, "to Crag Hopper")
        revision = response.context["revision"]

        del initial["previous_reg"]

        self.client.force_login(self.trusted_user)

        with self.assertNumQueries(3):
            response = self.client.get("/vehicles/edits?status=disapproved")
        self.assertEqual(len(response.context["revisions"]), 0)

        self.client.force_login(self.trusted_user)

        # add and remove a feature, change type
        initial["features"] = self.usb.id
        initial["vehicle_type"] = self.vehicle_2.vehicle_type_id
        with self.assertNumQueries(25):
            response = self.client.post(url, initial)
        revision = response.context["revision"]
        self.assertFalse(revision.pending)

        features = VehicleRevisionFeature.objects.all()
        self.assertEqual(str(features[0]), "<del>Wi-Fi</del>")
        self.assertEqual(str(features[1]), "<ins>USB</ins>")

        # colour, spare ticket machine
        initial["colours"] = self.livery.id
        initial["spare_ticket_machine"] = True
        with self.assertNumQueries(19):
            response = self.client.post(url, initial)
            revision = response.context["revision"]
            self.assertEqual(revision.to_livery, self.livery)
            self.assertFalse(revision.pending)

        response = self.client.get(revision.vehicle.get_absolute_url())
        self.assertNotContains(response, "B EAN")
        self.assertNotContains(response, "Wi-Fi")

    def test_vehicle_edit_2(self):
        self.client.force_login(self.staff_user)

        url = self.vehicle_2.get_edit_url()

        initial = {
            "fleet_number": "50",
            "reg": "UWW2X",
            "operator": self.vehicle_2.operator_id,
            "vehicle_type": self.vehicle_2.vehicle_type_id,
            "other_vehicle_type": str(self.vehicle_2.vehicle_type),
            "colours": self.livery.id,
            "notes": "",
            "summary": "I saw it with my eyes",
        }

        with self.assertNumQueries(15):
            response = self.client.post(url, initial)
        self.assertNotContains(response, "already")
        self.assertContains(response, "You haven&#x27;t changed anything")

        self.assertEqual(0, VehicleRevision.objects.count())

        self.assertNotContains(response, "/operators/bova-and-over")

        initial["notes"] = "Ex Ipswich Buses"
        initial["name"] = "Luther Blisset"
        initial["branding"] = "Coastliner"
        initial["previous_reg"] = "k292  jvf"
        initial["reg"] = ""
        with self.assertNumQueries(14):
            response = self.client.post(url, initial)
        self.assertIsNone(response.context["form"])

        self.assertContains(response, "Your changes")

        response = self.client.get("/vehicles/edits?status=pending")
        self.assertContains(response, "Luther Blisset")

        with self.assertNumQueries(14):
            response = self.client.get(url)
        self.assertContains(response, "already")

    def test_vehicle_edit_colour(self):
        self.client.force_login(self.staff_user)
        url = self.vehicle_2.get_edit_url()

        initial = {
            "fleet_number": "50",
            "reg": "UWW2X",
            "vehicle_type": self.vehicle_2.vehicle_type_id,
            "other_vehicle_type": "Optare Spectra",
            "operator": self.vehicle_2.operator_id,
            "colours": self.livery.id,
            "other_colour": "",
            "notes": "",
            "summary": "I smelt it with my nose",
        }

        with self.assertNumQueries(15):
            response = self.client.post(url, initial)
            self.assertContains(response, "You haven&#x27;t changed anything")

        initial["other_colour"] = "Bath is my favourite spa town, and so is Harrogate"
        with self.assertNumQueries(15):
            response = self.client.post(url, initial)
            self.assertEqual(
                response.context["form"].errors,
                {
                    "other_colour": [
                        "An HTML5 simple color must be a Unicode string seven characters long."
                    ]
                },
            )

    def test_remove_fleet_number(self):
        self.client.force_login(self.staff_user)

        url = self.vehicle_1.get_edit_url()

        # create a revision
        with self.assertNumQueries(16):
            response = self.client.post(
                url,
                {
                    "fleet_number": "",
                    "vehicle_type": self.vehicle_1.vehicle_type_id,
                    "reg": "",
                    "operator": self.vehicle_1.operator_id,
                    "notes": "Trent Barton",
                    "summary": "I am the CEO of the company",
                },
            )
        self.assertContains(response, "Thank you")
        self.assertContains(response, "Your changes")

        revision = response.context["revision"]
        self.assertEqual(
            str(revision), "colours: #FF0000 → , fleet number: 1 → , reg: FD54JYA → "
        )
        self.assertTrue(revision.pending)
        self.assertIsNone(revision.approved_at)

        with self.assertNumQueries(6):
            response = self.client.get("/vehicles/edits?status=pending")
        self.assertEqual(len(response.context["revisions"]), 1)

        with self.assertNumQueries(3):
            response = self.client.get("/vehicles/edits")  # approved
        self.assertEqual(len(response.context["revisions"]), 0)

        with self.assertNumQueries(7):
            response = self.client.get("/vehicles/edits?operator=LYNX&status=pending")
        self.assertEqual(len(response.context["revisions"]), 1)

        with self.assertNumQueries(7):
            response = self.client.get(
                "/vehicles/edits?operator=lynx&status=pending"
            )  # test lowercase opcode
        self.assertEqual(len(response.context["revisions"]), 1)

        self.client.force_login(self.staff_user)

        revision = VehicleRevision.objects.last()
        self.assertEqual(
            revision.changes,
            {"reg": "-FD54JYA\n+", "colours": "-#FF0000\n+", "fleet number": "-1\n+"},
        )

        # test user view
        response = self.client.get(self.staff_user.get_absolute_url())
        self.assertContains(response, f"?user={self.staff_user.id}&")
        self.assertContains(response, "0 disapproved")

    def test_operator_user_permission(self):
        self.client.force_login(self.staff_user)

        self.user.operators.add(self.vehicle_1.operator_id)

        # another user has permission
        response = self.client.get(self.vehicle_1.get_edit_url())
        self.assertContains(
            response, "Editing Lynx vehicles is restricted", status_code=403
        )

        # give logged-in user permission
        self.staff_user.operators.add(self.vehicle_1.operator_id)

        response = self.client.get(self.vehicle_1.get_edit_url())
        self.assertNotContains(response, "Editing Lynx vehicles is restricted")

    def test_vehicle_edit_3(self):
        self.client.force_login(self.user)

        url = self.vehicle_3.get_edit_url()

        # first read the rules and tick the box
        with self.assertNumQueries(5):
            response = self.client.get(url)
        self.assertContains(response, "read the rules")

        with self.assertNumQueries(5):
            response = self.client.get(url)
        with self.assertNumQueries(10):
            response = self.client.post(self.vehicle_3.get_edit_url(), {"rules": True})

        self.assertNotContains(response, "notes")
        self.assertNotContains(response, "read the rules")

        with self.assertNumQueries(10):
            # new user - can create a pending revision
            response = self.client.post(
                self.vehicle_3.get_edit_url(),
                {
                    "reg": "D19 FOX",
                    "previous_reg": "QC FBPE",
                    "withdrawn": True,
                    "summary": ".",
                },
            )
        self.assertContains(response, "Your changes")
        self.assertContains(response, "<strong>removed from list</strong>")
        revision = response.context["revision"]

        with self.assertNumQueries(14):
            response = self.client.post(
                self.vehicle_2.get_edit_url(),
                {
                    "reg": self.vehicle_2.reg,
                    "vehicle_type": self.vehicle_2.vehicle_type_id,
                    "colours": "",
                    "prevous_reg": "SPIDERS",  # doesn't match regex
                    "summary": "I sold the manager this reg",
                },
            )
            self.assertContains(response, "Your changes")

        self.client.force_login(self.trusted_user)

        # apply
        self.client.post(f"/vehicles/revisions/{revision.id}/apply")
        revision.vehicle.refresh_from_db()
        self.assertTrue(revision.vehicle.withdrawn)

        # withdrawn so can't edit
        with self.assertNumQueries(3):
            response = self.client.get(self.vehicle_3.get_edit_url())
            self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)

        revision.vehicle.withdrawn = False
        revision.vehicle.save(update_fields=["withdrawn"])

        with self.assertNumQueries(12):
            # trusted user - can edit reg
            response = self.client.post(
                self.vehicle_3.get_edit_url(),
                {
                    "reg": "DA04 DDA",
                    "branding": "",
                    "previous_reg": "K292  JVF,P44CEX",  # has to match regex
                    "summary": "dunno",
                },
            )
        self.assertEqual(
            str(response.context["revision"]),
            "previous reg:  → K292JVF,P44CEX, reg: D19FOX → DA04DDA",
        )
        self.assertContains(response, "<strong>reg</strong>")
        self.assertContains(response, "to DA04DDA")
        self.assertContains(response, "<strong>previous reg</strong>")
        self.assertContains(response, "to K292JVF,P44CEX")

        # test previous reg display
        response = self.client.get(self.vehicle_3.get_absolute_url())
        self.assertContains(response, ">K292 JVF, P44 CEX<")

        with self.assertNumQueries(17):
            # trusted user - can edit colour
            response = self.client.post(
                self.vehicle_2.get_edit_url(),
                {
                    "reg": self.vehicle_2.reg,
                    "vehicle_type": self.vehicle_2.vehicle_type_id,
                    "other_vehicle_type": str(self.vehicle_2.vehicle_type),
                    "operator": self.vehicle_2.operator_id,
                    "colours": "",
                    "summary": "I sold the manager a tin of paint",
                },
            )
        self.assertContains(response, "<strong>livery</strong>")
        # self.assertContains(
        #     response,
        #     '<span class="livery" style="background:linear-gradient(90deg,red 50%,#00f 50%)"></span>',
        # )
        self.assertContains(response, "<strong>livery</strong>")

        revision = VehicleRevision.objects.first()
        self.assertEqual(
            list(revision.revert()),
            [
                f"vehicle {revision.vehicle_id} colours not reverted",
                f"vehicle {revision.vehicle_id} branding not reverted",
                f"vehicle {revision.vehicle_id} previous reg not reverted",
            ],
        )
        revision = VehicleRevision.objects.first()
        self.assertEqual(
            list(revision.revert()),
            [
                f"vehicle {revision.vehicle_id} colours not reverted",
                f"vehicle {revision.vehicle_id} branding not reverted",
                f"vehicle {revision.vehicle_id} previous reg not reverted",
            ],
        )

    def test_vehicle_code_uniqueness(self):
        vehicle_1 = Vehicle.objects.create(code="11111", operator_id="BOVA")
        Vehicle.objects.create(
            code="11111", operator_id="LYNX"
        )  # same code, different operator

        self.client.force_login(self.trusted_user)

        response = self.client.post(
            vehicle_1.get_edit_url(), {"operator": "LYNX", "summary": "BUSES Magazine"}
        )
        self.assertContains(
            response,
            "<li>Lynx already has a vehicle with the code 11111</li>",
            html=True,
        )

    def test_big_map(self):
        with self.assertNumQueries(1):
            response = self.client.get("/map")
            self.assertContains(response, "latitude: 54,")

        with self.assertNumQueries(1):
            response = self.client.get("/maps")
            self.assertRedirects(response, "/map", 301)

        # 🇮🇪
        response = self.client.get("/map", headers={"CF-IPCountry": "IE"})
        self.assertContains(response, "latitude: 53.45,")

        response = self.client.get("/map/old")
        self.assertNotContains(response, "/bigmap.")
        self.assertContains(response, "/bigmap-classic.")

    def test_vehicles(self):
        with self.assertNumQueries(3):
            self.client.get("/vehicles")

    def test_service_vehicle_history(self):
        with self.assertNumQueries(5):
            response = self.client.get(
                "/services/spixworth-hunworth-happisburgh/vehicles?date=poop"
            )
        with self.assertNumQueries(4):
            response = self.client.get(
                "/services/spixworth-hunworth-happisburgh/vehicles?date=2020-10-20"
            )
        self.assertContains(response, "Vehicles")
        self.assertContains(response, "/vehicles/")
        self.assertContains(
            response,
            '<input type="date" name="date" aria-label="Date" value="2020-10-20">',
            # '<option selected value="2020-10-20">Tuesday 20 October 2020</option>'
        )
        self.assertContains(response, "1 - FD54 JYA")

    def test_api(self):
        with self.assertNumQueries(2):
            response = self.client.get("/api/vehicles/?limit=2")
        self.maxDiff = None
        self.assertEqual(
            response.json(),
            {
                "count": 3,
                "next": "http://testserver/api/vehicles/?limit=2&offset=2",
                "previous": None,
                "results": [
                    {
                        "id": self.vehicle_1.id,
                        "slug": "lynx-2",
                        "fleet_number": 1,
                        "fleet_code": "1",
                        "reg": "FD54JYA",
                        "previous_reg": "",
                        "vehicle_type": {
                            "id": self.vehicle_1.vehicle_type_id,
                            "name": "Optare Tempo",
                            "double_decker": False,
                            "coach": False,
                            "electric": False,
                            "style": "",
                            "fuel": "diesel",
                        },
                        "livery": {
                            "id": None,
                            "name": None,
                            "left": "#FF0000",
                            "right": "#FF0000",
                        },
                        "branding": "",
                        "operator": {
                            "id": "LYNX",
                            "slug": "lynx",
                            "name": "Lynx",
                        },
                        "garage": None,
                        "name": "",
                        "notes": "Trent Barton",
                        "withdrawn": False,
                        "special_features": ["Wi-Fi"],
                    },
                    {
                        "id": self.vehicle_2.id,
                        "slug": "lynx-50",
                        "fleet_number": 50,
                        "fleet_code": "50",
                        "reg": "UWW2X",
                        "previous_reg": "",
                        "vehicle_type": {
                            "id": self.vehicle_2.vehicle_type_id,
                            "name": "Optare Spectra",
                            "double_decker": True,
                            "coach": False,
                            "electric": False,
                            "style": "double decker",
                            "fuel": "diesel",
                        },
                        "livery": {
                            "id": self.vehicle_2.livery_id,
                            "name": "black with lemon piping",
                            "left": "linear-gradient(90deg,red 50%,#00f 50%)",
                            # "left": "linear-gradient(90deg,#FF0000 50%,#0000FF 50%)",
                            "right": "linear-gradient(270deg,red 50%,#00f 50%)",
                            # "right": "linear-gradient(270deg,#FF0000 50%,#0000FF 50%)",
                        },
                        "branding": "",
                        "operator": {
                            "id": "LYNX",
                            "slug": "lynx",
                            "name": "Lynx",
                        },
                        "garage": None,
                        "name": "",
                        "notes": "",
                        "withdrawn": False,
                        "special_features": None,
                    },
                ],
            },
        )

        with self.assertNumQueries(1):
            response = self.client.get("/api/vehicles/?reg=sa60twp")
        self.assertEqual(0, response.json()["count"])

        with self.assertNumQueries(2):
            response = self.client.get("/api/vehicles/?search=fd54jya")
        self.assertEqual(1, response.json()["count"])

    def test_vehicles_json(self):
        with self.assertNumQueries(0):
            response = self.client.get(
                "/vehicles.json?xmax=984.375&xmin=694.688&ymax=87.043&ymin=-89.261"
            )
        # Longitude 984.375 is out of range [-180, 180]
        self.assertEqual(response.status_code, 400)

        with self.assertNumQueries(0):
            response = self.client.get("/vehicles.json?xmax=x&xmin=x&ymax=x&ymin=x")
        # String input unrecognized as WKT EWKT, and HEXEWKB.
        self.assertEqual(response.status_code, 400)

        with self.assertNumQueries(0):
            response = self.client.get("/vehicles.json?id=1,2")
        self.assertEqual(response.json(), [])
        self.assertEqual(response.headers["ETag"], '"d751713988987e9331980363e24189ce"')
