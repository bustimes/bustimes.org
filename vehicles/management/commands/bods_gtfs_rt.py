import io
import zipfile
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils.dateparse import parse_duration
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Operator
from bustimes.models import Trip

from ...models import Service, Vehicle, VehicleJourney
from .import_gtfsr_ie import Command as GTFSRCommand


class Command(GTFSRCommand):
    source_name = "BODS GTFS"
    vehicle_code_scheme = "BODS GTFS"
    url = "https://data.bus-data.dft.gov.uk/avl/download/gtfsrt"

    def do_source(self):
        self.tzinfo = ZoneInfo("Europe/London")
        self.source = DataSource.objects.get(name=self.source_name)
        return self

    def get_feed(self):
        response = self.get_response()
        print(response.headers)

        with (
            zipfile.ZipFile(io.BytesIO(response.content)) as archive,
            archive.open("gtfsrt.bin") as open_file,
        ):
            data = open_file.read()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(data)

        self.source.datetime = datetime.fromtimestamp(feed.header.timestamp, UTC)

        return feed

    def get_vehicle(self, item):
        if item.vehicle.trip.route_id:
            operator = Operator.objects.filter(
                service__route__code=item.vehicle.trip.route_id
            ).first()
        else:
            operator = None
        return Vehicle.objects.get_or_create(
            operator=operator,
            code=item.vehicle.vehicle.id.strip(),
        )

    def get_journey(self, item, vehicle):
        now = self.get_datetime(item).astimezone(self.tzinfo)
        journey = VehicleJourney(
            code=item.vehicle.trip.trip_id, datetime=now, date=now.date()
        )

        start_date = None
        if item.vehicle.trip.start_date:
            start_date = datetime.strptime(  # noqa: DTZ007 - tzinfo applied below
                f"{item.vehicle.trip.start_date} 12:00:00",
                "%Y%m%d %H:%M:%S",
            )
            journey.date = start_date.date()

            # start_time may be after midnight (e.g. "26:25:00")
            # at the end of the operational day
            # (the noon minus 12 hours trick copes with daylight saving time)
            start_time = parse_duration(item.vehicle.trip.start_time)
            if start_time is not None:
                journey.datetime = (
                    start_date.replace(tzinfo=self.tzinfo)
                    - timedelta(hours=12)
                    + start_time
                )

        journey.route_name = item.vehicle.trip.route_id

        if journey.code:
            try:
                trip = Trip.objects.get(
                    route__source=self.source, ticket_machine_code=journey.code
                )
            except Trip.DoesNotExist:
                pass
            else:
                journey.trip = trip

                if start_date:
                    journey.datetime = (
                        start_date.replace(tzinfo=self.tzinfo)
                        - timedelta(hours=12)
                        + trip.start
                    )
                    if journey.datetime - now > timedelta(hours=12):
                        # `start_date` is today but the trip's operational day is yesterday
                        journey.datetime -= timedelta(days=1)
                        journey.date -= timedelta(days=1)

                journey.service = trip.route.service

                journey.route_name = journey.service.line_name
                journey.destination = trip.headsign or ""

        if not journey.trip and item.vehicle.trip.route_id:
            try:
                journey.service = Service.objects.get(
                    route__code=item.vehicle.trip.route_id, source=self.source
                )
            except Service.DoesNotExist:
                pass
            else:
                journey.route_name = journey.service.line_name

        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey
