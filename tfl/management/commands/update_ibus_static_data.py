import io
import re
import zipfile
from itertools import islice
from xml.etree import ElementTree

import boto3
import pandas as pd
from botocore import UNSIGNED
from botocore.config import Config
from django.contrib.gis.geos import Point
from django.core.management import BaseCommand
from pandas.errors import ParserError

from ... import models

# https://techforum.tfl.gov.uk/t/data-drop-ibus1-static-data/6062

BUCKET = "ibus.data.tfl.gov.uk"
REGION = "eu-west-1"

BATCH_SIZE = 5000


def get_client():
    # the bucket is public/anonymous-readable, no credentials needed
    return boto3.client(
        "s3", region_name=REGION, config=Config(signature_version=UNSIGNED)
    )


def get_current_base_version(client):
    obj = client.get_object(Bucket=BUCKET, Key="Base_Version.xml")
    root = ElementTree.fromstring(obj["Body"].read())
    version = int(root.find("{*}Base_Version").text)
    valid_from = root.find("{*}Valid_From").text[:10]
    valid_to = root.find("{*}Valid_To").text[:10]
    return version, valid_from, valid_to


def load_zip(client, key):
    obj = client.get_object(Bucket=BUCKET, Key=key)
    return zipfile.ZipFile(io.BytesIO(obj["Body"].read()))


def clean(row):
    """pandas gives us NaN for missing/nil values - turn those into None"""
    return {key: (None if pd.isna(value) else value) for key, value in row.items()}


def read_rows(zf, member_prefix, tag, columns):
    """Read every member of a zip file whose name starts with member_prefix
    (there can be more than one, when TfL have had to split a big dataset
    into numbered chunks), yielding each row as a dict"""
    for name in sorted(zf.namelist()):
        if not name.startswith(member_prefix):
            continue
        try:
            with zf.open(name) as f:
                df = pd.read_xml(f, iterparse={tag: columns})
        except ParserError:
            continue  # e.g. an operator with no live services this period
        for row in df.to_dict("records"):
            yield clean(row)


def bulk_create_in_batches(model, instances):
    instances = iter(instances)
    while batch := list(islice(instances, BATCH_SIZE)):
        model.objects.bulk_create(batch)


class Command(BaseCommand):
    help = "Imports TfL iBus static schedule data from S3"

    def handle(self, **options):
        client = get_client()
        version, valid_from, valid_to = get_current_base_version(client)

        if models.BaseVersion.objects.filter(pk=version).exists():
            self.stdout.write(f"{version} already imported")
            return

        base_version = models.BaseVersion.objects.create(
            version=version, valid_from=valid_from, valid_to=valid_to
        )
        prefix = f"Base_Version_{version}/"

        self.load_operators(client, base_version, prefix)
        self.load_lines(client, base_version, prefix)
        self.load_destinations(client, base_version, prefix)
        self.load_garages(client, base_version, prefix)
        self.load_vehicles(client, base_version, prefix)
        self.load_stops(client, base_version, prefix)

        operator_codes, pattern_keys = self.discover_keys(client, prefix)

        for operator_code in operator_codes:
            self.stdout.write(f"schedule {operator_code}")
            self.load_schedule(client, base_version, prefix, operator_code)

        for i, key in enumerate(pattern_keys):
            self.stdout.write(f"pattern {i + 1}/{len(pattern_keys)}: {key}")
            self.load_pattern(client, base_version, key)

        self.prune_old_versions(version)

    def discover_keys(self, client, prefix):
        """Find which operators have schedule data, and which per-line
        Pattern_data zips exist, in this base version"""
        paginator = client.get_paginator("list_objects_v2")
        schedule_re = re.compile(
            rf"^{re.escape(prefix)}([A-Z0-9]+)/schedule_\1_\d+\.zip$"
        )
        operator_codes = set()
        pattern_keys = []
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key[len(prefix) :]
                if name.startswith("Pattern_data_") and name.endswith(".zip"):
                    pattern_keys.append(key)
                elif match := schedule_re.match(key):
                    operator_codes.add(match.group(1))
        return sorted(operator_codes), pattern_keys

    def load_operators(self, client, base_version, prefix):
        zf = load_zip(client, f"{prefix}Operator_{base_version.version}.zip")
        rows = read_rows(
            zf,
            "Operator_",
            "Operator",
            ["aOperator_Code", "Operator_Name", "Operator_Agency"],
        )
        bulk_create_in_batches(
            models.Operator,
            (
                models.Operator(
                    base_version=base_version,
                    code=row["aOperator_Code"],
                    name=row["Operator_Name"] or "",
                    agency=row["Operator_Agency"] or "",
                )
                for row in rows
            ),
        )

    def load_lines(self, client, base_version, prefix):
        zf = load_zip(client, f"{prefix}Line_{base_version.version}.zip")
        rows = read_rows(
            zf,
            "Line_",
            "Line",
            ["aContract_Line_No", "Service_Line_No", "Logical_Line_No"],
        )
        bulk_create_in_batches(
            models.Line,
            (
                models.Line(
                    base_version=base_version,
                    contract_line_no=row["aContract_Line_No"],
                    service_line_no=row["Service_Line_No"],
                    logical_line_no=row["Logical_Line_No"],
                )
                for row in rows
            ),
        )

    def load_destinations(self, client, base_version, prefix):
        zf = load_zip(client, f"{prefix}Destination_{base_version.version}.zip")
        rows = read_rows(
            zf,
            "Destination_",
            "Destination",
            ["aDestination_Idx", "Long_Destination_Name", "Short_Destination_Name"],
        )
        bulk_create_in_batches(
            models.Destination,
            (
                models.Destination(
                    base_version=base_version,
                    idx=row["aDestination_Idx"],
                    long_name=row["Long_Destination_Name"] or "",
                    short_name=row["Short_Destination_Name"] or "",
                )
                for row in rows
            ),
        )

    def load_garages(self, client, base_version, prefix):
        zf = load_zip(client, f"{prefix}Garage_{base_version.version}.zip")
        rows = read_rows(
            zf,
            "Garage_",
            "Garage",
            ["aGarage_No", "aOperator_Code", "Garage_Code", "Garage_Name"],
        )
        bulk_create_in_batches(
            models.Garage,
            (
                models.Garage(
                    base_version=base_version,
                    number=row["aGarage_No"],
                    operator_code=row["aOperator_Code"],
                    code=row["Garage_Code"] or "",
                    name=row["Garage_Name"] or "",
                )
                for row in rows
            ),
        )

    def load_vehicles(self, client, base_version, prefix):
        zf = load_zip(client, f"{prefix}Vehicle_{base_version.version}.zip")
        rows = read_rows(
            zf,
            "Vehicle_",
            "Vehicle",
            ["aVehicleId", "Registration_Number", "Bonnet_No", "Operator_Agency"],
        )
        bulk_create_in_batches(
            models.Vehicle,
            (
                models.Vehicle(
                    base_version=base_version,
                    vehicle_id=row["aVehicleId"],
                    registration_number=row["Registration_Number"] or "",
                    bonnet_no=row["Bonnet_No"] or "",
                    operator_agency=row["Operator_Agency"] or "",
                )
                for row in rows
            ),
        )

    def load_stops(self, client, base_version, prefix):
        zf = load_zip(client, f"{prefix}Stop_Point_{base_version.version}.zip")
        rows = read_rows(
            zf,
            "Stop_Point_",
            "Stop_Point",
            [
                "aStop_Point_Idx",
                "Stop_Code_LBSL",
                "Stop_Name",
                "Location_Longitude",
                "Location_Latitude",
                "Point_Letter",
                "NaPTAN_Code",
                "SMS_Code",
                "Stop_Area",
                "Borough_Code",
                "Heading",
                "Stop_Type",
                "Street_Name",
                "Post_Code",
                "Towards",
            ],
        )

        def build(row):
            longitude = row["Location_Longitude"]
            latitude = row["Location_Latitude"]
            latlong = Point(longitude, latitude) if longitude and latitude else None
            return models.Stop(
                base_version=base_version,
                idx=row["aStop_Point_Idx"],
                naptan_code=row["NaPTAN_Code"] or "",
                stop_code_lbsl=row["Stop_Code_LBSL"] or "",
                name=row["Stop_Name"] or "",
                latlong=latlong,
                point_letter=row["Point_Letter"] or "",
                sms_code=row["SMS_Code"] or "",
                stop_area=row["Stop_Area"] or "",
                borough_code=row["Borough_Code"] or "",
                heading=row["Heading"],
                stop_type=row["Stop_Type"] or "",
                street_name=row["Street_Name"] or "",
                post_code=row["Post_Code"] or "",
                towards=row["Towards"] or "",
            )

        bulk_create_in_batches(models.Stop, (build(row) for row in rows))

    def load_schedule(self, client, base_version, prefix, operator_code):
        version = base_version.version
        key = f"{prefix}{operator_code}/schedule_{operator_code}_{version}.zip"
        zf = load_zip(client, key)

        blocks = read_rows(
            zf,
            f"Block_{operator_code}_{version}",
            "Block",
            ["aBlock_Idx", "aOperator_Code", "Block_No", "Running_No"],
        )
        bulk_create_in_batches(
            models.Block,
            (
                models.Block(
                    base_version=base_version,
                    idx=row["aBlock_Idx"],
                    operator_code=row["aOperator_Code"],
                    block_no=row["Block_No"],
                    running_no=row["Running_No"],
                )
                for row in blocks
            ),
        )

        block_calendar_days = read_rows(
            zf,
            f"Block_CalendarDay_{operator_code}_{version}",
            "Block_CalendarDay",
            ["aBlock_Idx", "aCalendar_Day", "Block_Runs_On_Day"],
        )
        bulk_create_in_batches(
            models.BlockCalendarDay,
            (
                models.BlockCalendarDay(
                    base_version=base_version,
                    block_idx=row["aBlock_Idx"],
                    calendar_day=row["aCalendar_Day"],
                    runs=bool(row["Block_Runs_On_Day"]),
                )
                for row in block_calendar_days
            ),
        )

        journeys = read_rows(
            zf,
            f"Journey_{operator_code}_{version}",
            "Journey",
            [
                "aJourney_Idx",
                "aPattern_Idx",
                "aBlock_Idx",
                "Trip_No_LBSL",
                "Type",
                "Start_Time",
            ],
        )
        bulk_create_in_batches(
            models.Journey,
            (
                models.Journey(
                    base_version=base_version,
                    idx=row["aJourney_Idx"],
                    pattern_idx=row["aPattern_Idx"],
                    block_idx=row["aBlock_Idx"],
                    trip_no_lbsl=row["Trip_No_LBSL"],
                    type=row["Type"],
                    start_time=row["Start_Time"],
                )
                for row in journeys
            ),
        )

        drive_times = read_rows(
            zf,
            f"Journey_Drive_Time_{operator_code}_{version}",
            "Journey_Drive_Time",
            [
                "aJourney_Idx",
                "aStop_In_Pattern_From_Idx",
                "aStop_In_Pattern_To_Idx",
                "Drive_Time",
            ],
        )
        bulk_create_in_batches(
            models.JourneyDriveTime,
            (
                models.JourneyDriveTime(
                    base_version=base_version,
                    journey_idx=row["aJourney_Idx"],
                    stop_in_pattern_from_idx=row["aStop_In_Pattern_From_Idx"],
                    stop_in_pattern_to_idx=row["aStop_In_Pattern_To_Idx"],
                    drive_time=row["Drive_Time"],
                )
                for row in drive_times
            ),
        )

        wait_times = read_rows(
            zf,
            f"Journey_Wait_Time_{operator_code}_{version}",
            "Journey_Wait_Time",
            ["aJourney_Idx", "aStop_In_Pattern_Idx", "Wait_Time"],
        )
        bulk_create_in_batches(
            models.JourneyWaitTime,
            (
                models.JourneyWaitTime(
                    base_version=base_version,
                    journey_idx=row["aJourney_Idx"],
                    stop_in_pattern_idx=row["aStop_In_Pattern_Idx"],
                    wait_time=row["Wait_Time"],
                )
                for row in wait_times
            ),
        )

    def load_pattern(self, client, base_version, key):
        zf = load_zip(client, key)

        patterns = read_rows(
            zf,
            "Pattern_",
            "Pattern",
            ["aPattern_Idx", "aContract_Line_No", "Direction", "Type"],
        )
        bulk_create_in_batches(
            models.Pattern,
            (
                models.Pattern(
                    base_version=base_version,
                    idx=row["aPattern_Idx"],
                    contract_line_no=row["aContract_Line_No"],
                    direction=row["Direction"],
                    type=row["Type"],
                )
                for row in patterns
            ),
        )

        stops_in_pattern = read_rows(
            zf,
            "Stop_In_Pattern_",
            "Stop_In_Pattern",
            [
                "aStop_In_Pattern_Idx",
                "aPattern_Idx",
                "aDestination_Idx",
                "aStop_Point_Idx",
                "Sequence_No",
                "Timing_Point_Code",
            ],
        )
        bulk_create_in_batches(
            models.StopInPattern,
            (
                models.StopInPattern(
                    base_version=base_version,
                    idx=row["aStop_In_Pattern_Idx"],
                    pattern_idx=row["aPattern_Idx"],
                    destination_idx=row["aDestination_Idx"],
                    stop_idx=row["aStop_Point_Idx"],
                    sequence_no=row["Sequence_No"],
                    timing_point_code=row["Timing_Point_Code"] or "",
                )
                for row in stops_in_pattern
            ),
        )

    def prune_old_versions(self, keep_version):
        models.BaseVersion.objects.exclude(pk=keep_version).delete()
