"""Tests for importing Northern Ireland data"""

from pathlib import Path
from unittest.mock import patch

import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import DataSource


class ImportNornIronTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        DataSource.objects.bulk_create(
            [
                DataSource(
                    name="ULB",
                    url="https://www.opendatani.gov.uk/dataset/"
                    "ulsterbus-and-goldline-timetable-data-from-08-11-2023",
                    datetime="2020-07-01T10:35:39.433122Z",
                ),
                DataSource(
                    name="MET",
                    url="https://www.opendatani.gov.uk/dataset/"
                    "metro-timetable-data-valid-from-18-june-until-31-august-2016",
                    datetime="2020-06-01T10:35:39Z",
                ),
            ]
        )

    @patch("bustimes.management.commands.import_ni.handle_file")
    @patch("bustimes.management.commands.import_ni.download")
    @patch("bustimes.management.commands.import_ni.pprint.pprint")
    def test_import_ni(self, download, handle_archive, pprint):
        vcr_path = Path(__file__).resolve().parent / "fixtures" / "import_ni.yaml"
        with vcr.use_cassette(str(vcr_path), decode_compressed_response=True):
            with self.assertNumQueries(17):
                call_command("import_ni")

            handle_archive.assert_called()
            download.assert_called()
            pprint.assert_called()
