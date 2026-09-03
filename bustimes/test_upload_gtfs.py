import io
import zipfile

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import User
from busstops.models import DataSource, Operator, Service

from .models import Route, RouteLink, StopTime, Trip

GTFS_FILES = {
    "agency.txt": """agency_id,agency_name,agency_url,agency_timezone
NABO,Town & District Transport Trust,https://www.tdtt.co.uk/,Europe/London
""",
    "stops.txt": """stop_id,stop_name,stop_lat,stop_lon
A,Showground,53.79,-2.41
B,Rishton,53.77,-2.42
C,Accrington,53.75,-2.36
""",
    # no agency_id column (optional if there's only one agency),
    # two routes with the same short name, and one with no short name
    "routes.txt": """route_id,route_short_name,route_long_name,route_type
route_1,2,Showground - Rishton,3
route_2,2,Rishton - Showground,3
route_3,,Showground - Accrington,3
""",
    "calendar.txt": """service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
svc_1,0,0,0,0,0,1,0,20260905,20260905
""",
    # no trip_headsign or direction_id columns
    "trips.txt": """route_id,service_id,trip_id
route_1,svc_1,trip_1
route_2,svc_1,trip_2
route_3,svc_1,trip_3
""",
    "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence
trip_1,10:50:00,10:50:00,A,1
trip_1,10:59:00,10:59:00,B,2
trip_2,11:10:00,11:10:00,B,1
trip_2,11:19:00,11:19:00,A,2
trip_3,12:00:00,12:00:00,A,1
trip_3,12:30:00,12:30:00,C,2
""",
}


SHAPES_FILES = {
    **GTFS_FILES,
    "trips.txt": """route_id,service_id,trip_id,shape_id
route_1,svc_1,trip_1,shape_1
route_2,svc_1,trip_2,shape_2
route_3,svc_1,trip_3,
""",
    "shapes.txt": """shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence
shape_1,53.79,-2.41,1
shape_1,53.78,-2.415,2
shape_1,53.77,-2.42,3
shape_2,53.77,-2.42,1
shape_2,53.78,-2.405,2
shape_2,53.79,-2.41,3
""",
}


def make_zip(files=GTFS_FILES):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return SimpleUploadedFile("gtfs.zip", buffer.getvalue(), "application/zip")


class UploadGTFSTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="uploader", email="u@example.com")
        cls.user.user_permissions.add(Permission.objects.get(codename="add_datasource"))

    def test_permission_required(self):
        response = self.client.get("/upload")
        self.assertEqual(response.status_code, 403)

    def test_upload(self):
        self.client.force_login(self.user)

        response = self.client.get("/upload")
        self.assertContains(response, "GTFS zip file")

        response = self.client.post(
            "/upload", {"source_name": "TDTT", "file": make_zip()}
        )
        source = DataSource.objects.get(name="TDTT")
        self.assertRedirects(response, source.get_absolute_url())

        operator = Operator.objects.get(noc="NABO")
        self.assertEqual(operator.name, "Town & District Transport Trust")

        # routes with the same short name share a service
        self.assertEqual(Route.objects.count(), 3)
        self.assertEqual(Service.objects.count(), 2)
        service = Service.objects.get(line_name="2")
        self.assertEqual(service.route_set.count(), 2)
        self.assertEqual(list(service.operator.all()), [operator])

        # no shapes.txt, so geometry is the bounding box of the stops
        self.assertEqual(service.geometry.geom_type, "Polygon")
        self.assertEqual(service.geometry.extent, (-2.42, 53.77, -2.41, 53.79))
        self.assertEqual(RouteLink.objects.count(), 0)

        self.assertEqual(Trip.objects.count(), 3)
        self.assertEqual(StopTime.objects.count(), 6)
        trip = Trip.objects.get(ticket_machine_code="trip_3")
        self.assertEqual(trip.headsign, "")
        self.assertFalse(trip.inbound)
        self.assertEqual(trip.destination.atco_code, "C")
        self.assertEqual(trip.operator, operator)

        # uploading again reuses services, routes and trips
        trip_ids = set(Trip.objects.values_list("id", flat=True))
        response = self.client.post(
            "/upload", {"source_name": "TDTT", "file": make_zip()}
        )
        self.assertRedirects(response, source.get_absolute_url())
        self.assertEqual(Service.objects.count(), 2)
        self.assertEqual(Route.objects.count(), 3)
        self.assertEqual(set(Trip.objects.values_list("id", flat=True)), trip_ids)
        self.assertEqual(StopTime.objects.count(), 6)

    def test_upload_with_shapes(self):
        self.client.force_login(self.user)

        response = self.client.post(
            "/upload", {"source_name": "TDTT", "file": make_zip(SHAPES_FILES)}
        )
        self.assertEqual(response.status_code, 302)

        # both routes' shapes, combined
        service = Service.objects.get(line_name="2")
        self.assertEqual(service.geometry.geom_type, "MultiLineString")
        self.assertEqual(len(service.geometry), 2)

        # no shape for route_3, so its service gets a bounding box
        service = Service.objects.get(line_name="")
        self.assertEqual(service.geometry.geom_type, "Polygon")

        self.assertEqual(
            sorted(RouteLink.objects.values_list("from_stop_id", "to_stop_id")),
            [("A", "B"), ("B", "A")],
        )
