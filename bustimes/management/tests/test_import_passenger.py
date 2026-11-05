from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from vcr import use_cassette

from busstops.models import DataSource, Operator, Region, Service

from ...models import Route, TimetableDataSource, Version
from ..commands.import_passenger import archive_old_versions


class ImportPassengerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sw = Region.objects.create(pk="SW", name="South West")
        Operator.objects.create(noc="BLUS", region=sw, name="Bluestar")
        Operator.objects.create(noc="UNIL", region=sw, name="Unilink")

    def test_import(self):
        fixtures_dir = Path(__file__).resolve().parent / "fixtures"
        TimetableDataSource.objects.create(
            active=True,
            url="https://data.discoverpassenger.com/operator/unilink",
            name="Unilink",
        )

        with (
            override_settings(DATA_DIR=fixtures_dir),
            use_cassette(
                str(fixtures_dir / "passenger.yaml"), decode_compressed_response=True
            ),
            patch("bustimes.management.commands.import_passenger.write_file"),
            self.assertRaises(FileNotFoundError),
            self.assertLogs("bustimes.management.commands.import_bod_timetables") as cm,
        ):
            call_command("import_passenger")

        self.assertEqual(
            cm.output,
            [
                "INFO:bustimes.management.commands.import_bod_timetables:Unilink",
                "INFO:bustimes.management.commands.import_bod_timetables:unilink_1648047602.zip",
            ],
        )

        self.assertFalse(Route.objects.all())

        source = DataSource.objects.get()
        route = Route(
            code="gocornwallbus_1653042367.zip/TXC Export 20220520-1013.xml#SER23"
        )
        # date from timestamp in code (1653042367)
        self.assertEqual(
            source.credit(route),
            """<a href="https://data.discoverpassenger.com/operator/unilink" rel="nofollow">Unilink</a>"""
            + """, <time datetime="2022-05-20">20 May 2022</time>""",
        )

    def test_archive_old_versions(self):
        source = TimetableDataSource.objects.create(name="Newport Bus")
        data_source = DataSource.objects.create(name="Newport Bus")
        service = Service.objects.create(line_name="1")

        def version(name, start_date, end_date):
            version = Version.objects.create(
                source=source, name=name, start_date=start_date, end_date=end_date
            )
            route = Route.objects.create(
                source=data_source, version=version, service=service, code=name
            )
            return version, route

        _, july = version("newportbus_1.zip", "2026-07-02", "2027-07-02")
        _, superseded = version("newportbus_2.zip", "2026-08-01", "2027-08-01")
        _, august = version("newportbus_3.zip", "2026-08-01", "2027-08-01")
        _, expired = version("newportbus_4.zip", "2026-09-01", "2026-09-30")
        current, current_route = version("newportbus_5.zip", "2026-11-02", None)

        short = Route.objects.create(
            source=data_source,
            version=july.version,
            service=service,
            code="short",
            end_date="2026-07-10",
        )

        archive_old_versions(source, data_source, [current])

        self.assertEqual(list(source.version_set.all()), [current])

        def end_date(route):
            route.refresh_from_db()
            self.assertIsNone(route.service_id)
            self.assertIsNone(route.version_id)
            return route.end_date

        self.assertEqual(end_date(july), date(2026, 7, 31))
        self.assertEqual(end_date(superseded), date(2026, 7, 31))
        self.assertEqual(end_date(august), date(2026, 8, 31))
        self.assertEqual(end_date(expired), date(2026, 9, 30))
        self.assertEqual(end_date(short), date(2026, 7, 10))

        current_route.refresh_from_db()
        self.assertEqual(current_route.service, service)
        self.assertIsNone(current_route.end_date)
