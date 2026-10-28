import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache
from django.utils.dateparse import parse_duration
from google.protobuf import json_format
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource
from bustimes.gtfs_feeds import FEEDS

from ...models import Operator, Service, Vehicle, VehicleJourney
from .import_gtfsr_ie import Command as GTFSRCommand

logger = logging.getLogger(__name__)


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def parse_feed(data: bytes):
    """Like FeedMessage.ParseFromString, but copes with the occasional entity
    whose declared length is slightly too short for its contents
    (which makes normal parsers reject the whole feed as corrupt - Skopje)
    by trying slightly longer lengths until the entity parses
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(data)
        return feed
    except DecodeError:
        feed.Clear()
    pos = 0
    end = len(data)
    while pos < end:
        tag, pos = read_varint(data, pos)
        if tag & 7 != 2:  # all FeedMessage fields are length-delimited
            logger.warning("unparseable feed from offset %s", pos)
            break
        length, pos = read_varint(data, pos)
        if tag >> 3 == 1:  # header
            feed.header.ParseFromString(data[pos : pos + length])
        elif tag >> 3 == 2:  # entity
            for fudge in range(4):
                entity = gtfs_realtime_pb2.FeedEntity()
                try:
                    entity.ParseFromString(data[pos : pos + length + fudge])
                except DecodeError:
                    continue
                length += fudge
                feed.entity.append(entity)
                break
            else:
                logger.warning("skipped unparseable entity at offset %s", pos)
        pos += length
    return feed


class Command(GTFSRCommand):
    @staticmethod
    def add_arguments(parser):
        GTFSRCommand.add_arguments(parser)
        parser.add_argument(
            "feed",
            choices=[
                name for name, feed in FEEDS.items() if feed.vehicle_positions_url
            ],
        )

    def handle(self, feed, *args, **options):
        self.set_feed(feed)
        super().handle(*args, **options)

    def set_feed(self, feed_name):
        self.feed_name = feed_name
        self.feed = FEEDS[feed_name]
        self.source_name = self.vehicle_code_scheme = self.feed.source_name
        self.stop_id_prefix = self.feed.stop_id_prefix

    def do_source(self):
        self.tzinfo = ZoneInfo(self.feed.timezone)
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.url = self.feed.vehicle_positions_url
        self.session.headers["User-Agent"] = "bustimes.org"
        self.operator = Operator.objects.get(noc=self.feed.noc)
        return self

    def get_items(self):
        # trip updates in a separate feed
        if self.feed.trip_updates_url and self.feed.trip_updates_url != self.url:
            try:
                response = requests.get(
                    self.feed.trip_updates_url,
                    headers={"User-Agent": "bustimes.org"},
                    timeout=10,
                )
                response.raise_for_status()
            except requests.RequestException as e:
                logger.warning(e)
            else:
                self.cache_trip_updates(parse_feed(response.content))

        response = self.get_response()
        if response.status_code == 304:  # not modified
            return
        feed = parse_feed(response.content)
        if feed.header.timestamp:
            self.source.datetime = datetime.fromtimestamp(feed.header.timestamp, UTC)

        # trip updates in the same feed as vehicle positions
        if self.feed.trip_updates_url == self.url:
            self.cache_trip_updates(feed)

        # (Cap Cotentin sometimes lists vehicles with a trip but no position)
        return [
            item
            for item in feed.entity
            if item.HasField("vehicle") and item.vehicle.HasField("position")
        ]

    def cache_trip_updates(self, feed):
        trip_updates = {
            entity.trip_update.trip.trip_id: json_format.MessageToDict(
                entity.trip_update
            )
            for entity in feed.entity
            if entity.HasField("trip_update") and entity.trip_update.trip.trip_id
        }
        cache.set(f"{self.feed_name}_trip_updates", trip_updates, 300)

    def get_vehicle(self, item):
        vehicle_code = item.vehicle.vehicle.id.strip()
        return Vehicle.objects.get_or_create(
            {
                "fleet_code": item.vehicle.vehicle.label.strip() or vehicle_code,
                "reg": item.vehicle.vehicle.license_plate.replace(" ", ""),
            },
            operator=self.operator,
            code=vehicle_code,
        )

    def get_journey(self, item, vehicle):
        now = self.get_datetime(item)
        if now.year == 1970:  # no timestamp
            now = self.source.datetime
        now = now.astimezone(self.tzinfo)
        journey = VehicleJourney(code=item.vehicle.trip.trip_id, date=now.date())

        start_date = None
        if item.vehicle.trip.start_date:
            start_date = datetime.strptime(
                f"{item.vehicle.trip.start_date} 12:00:00",
                "%Y%m%d %H:%M:%S",
            )
            journey.date = start_date.date()

            # start_time may be after midnight (e.g. "26:25:00")
            # at the end of the operational day
            # (the noon minus 12 hours trick copes with daylight saving time)
            # (parse_duration("") is timedelta(0), not None)
            if (
                item.vehicle.trip.start_time
                and (start_time := parse_duration(item.vehicle.trip.start_time))
                is not None
            ):
                journey.datetime = (
                    start_date.replace(tzinfo=self.tzinfo)
                    - timedelta(hours=12)
                    + start_time
                )

        journey.route_name = item.vehicle.trip.route_id

        if journey.code and (trip := self.trips.get(journey.code)):
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
