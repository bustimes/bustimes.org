import csv
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.gis.geos import LineString, Point
from django.core.management import call_command
from django.test import TestCase

from busstops.models import DataSource, Service, StopPoint

from ...models import Route, RouteLink, StopTime, Trip
from ...test_osm_routing import lon_lat, make_osm


class CompareRouteLinksTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = DataSource.objects.create(name="BODS")
        cls.service = Service.objects.create(line_name="25")
        route = Route.objects.create(line_name="25", service=cls.service, source=source)
        StopPoint.objects.bulk_create(
            [
                StopPoint(atco_code="a", active=True, latlong=Point(lon_lat(100, 10))),
                StopPoint(atco_code="b", active=True, latlong=Point(lon_lat(600, 10))),
                StopPoint(atco_code="c", active=True, latlong=Point(lon_lat(900, 10))),
                StopPoint(atco_code="d", active=True),  # no location
            ]
        )
        trip = Trip.objects.create(route=route, start="09:00:00", end="09:10:00")
        StopTime.objects.bulk_create(
            [
                StopTime(trip=trip, stop_id=stop_id, sequence=i)
                for i, stop_id in enumerate("abcd")
            ]
        )
        RouteLink.objects.bulk_create(
            [
                # along the road - should match
                RouteLink(
                    service=cls.service,
                    from_stop_id="a",
                    to_stop_id="b",
                    geometry=LineString(
                        lon_lat(100, 0), lon_lat(500, 0), lon_lat(600, 0)
                    ),
                ),
                # via the next road down - shouldn't match
                RouteLink(
                    service=cls.service,
                    from_stop_id="b",
                    to_stop_id="c",
                    geometry=LineString(
                        lon_lat(600, 0),
                        lon_lat(600, -300),
                        lon_lat(900, -300),
                        lon_lat(900, 0),
                    ),
                ),
                RouteLink(
                    service=cls.service,
                    from_stop_id="c",
                    to_stop_id="d",
                    geometry=LineString(lon_lat(900, 0), lon_lat(1000, 0)),
                ),
            ]
        )

    def test_compare_route_links(self):
        with TemporaryDirectory() as directory:
            osm_path = Path(directory) / "osm.json"
            osm_path.write_text(json.dumps(make_osm()))
            csv_path = Path(directory) / "results.csv"

            stdout = StringIO()
            call_command(
                "compare_route_links",
                str(self.service.id),
                "--osm",
                str(osm_path),
                "--csv",
                str(csv_path),
                stdout=stdout,
            )

            with csv_path.open() as f:
                rows = list(csv.DictReader(f))

        output = stdout.getvalue()
        self.assertIn("2 links, 1/2 within 25 m", output)
        self.assertIn("Overall: 1/2 (50%) links within 25 m", output)
        self.assertIn("/routelinks/", output)  # the worst link

        self.assertEqual(len(rows), 2)
        rows = {(row["from_stop"], row["to_stop"]): row for row in rows}
        self.assertLess(float(rows["a", "b"]["hausdorff"]), 1)
        self.assertGreater(float(rows["b", "c"]["hausdorff"]), 250)
