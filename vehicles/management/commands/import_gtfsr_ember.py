from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db.models import Q
from google.protobuf import json_format

from busstops.models import DataSource

from ...models import Vehicle, VehicleJourney
from .import_gtfsr_ie import Command as GTFSRCommand


class Command(GTFSRCommand):
    source_name = "Ember"
    vehicle_code_scheme = "Ember"
    wait = 17

    def do_source(self):
        self.tzinfo = ZoneInfo("Europe/London")
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.url = "https://api.ember.to/v1/gtfs/realtime/"
        return self

    def get_items(self):
        feed = self.get_feed()
        if feed is None:  # not modified
            return

        trip_updates = {
            entity["tripUpdate"]["trip"]["tripId"]: entity["tripUpdate"]
            for entity in json_format.MessageToDict(feed).get("entity", ())
            if "tripUpdate" in entity and "tripId" in entity["tripUpdate"]["trip"]
        }
        cache.set("ember_trip_updates", trip_updates, 300)

        # the feed contains both vehicle positions and alerts (and possibly other entities)
        for item in feed.entity:
            if item.HasField("vehicle"):
                yield item

    def get_vehicle(self, item):
        vehicle_code = item.vehicle.vehicle.id
        reg = vehicle_code.replace(" ", "")

        return Vehicle.objects.filter(Q(code=vehicle_code) | Q(code=reg)).get_or_create(
            operator_id="EMBR",
            defaults={"code": vehicle_code, "reg": reg},
        )

    def get_journey(self, item, vehicle):
        journey = VehicleJourney(code=item.vehicle.trip.trip_id)

        start_date = datetime.strptime(  # noqa: DTZ007 - only .date() is used
            f"{item.vehicle.trip.start_date} 12:00:00",
            "%Y%m%d %H:%M:%S",
        )
        journey.date = start_date.date()

        if trip := self.trips.get(journey.code):
            journey.trip = trip

            journey.datetime = (
                start_date.replace(tzinfo=self.tzinfo)
                - timedelta(hours=12)
                + trip.start
            )
            now = self.get_datetime(item)
            if journey.datetime - now > timedelta(hours=12):
                # `start_date` is today but the trip's operational day is yesterday
                journey.datetime -= timedelta(days=1)
                journey.date -= timedelta(days=1)

            journey.service = trip.route.service

            journey.route_name = journey.service.line_name
            journey.destination = trip.headsign or ""

        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey
