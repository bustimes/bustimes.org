import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from asgiref.sync import async_to_sync
from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.utils.dateparse import parse_duration
from google.protobuf import json_format

from busstops.models import DataSource
from bustimes.models import Trip

from ...models import Livery, VehicleJourney
from ...utils import VEHICLE_POSITIONS_CHANNEL, calculate_bearing
from .. import import_live_vehicles
from .import_gtfsr_ie import Command as GTFSRCommand

logger = logging.getLogger(__name__)


class Command(GTFSRCommand):
    source_name = "FlixBus"
    trip_id_field = "vehicle_journey_code"
    trip_select_related = ("route__service", "destination__locality")

    def do_source(self):
        self.tzinfo = ZoneInfo("Europe/London")
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.source.datetime = None
        self.url = "https://rt.flix.baguette.pirnet.si/rt.pb"
        self.livery = Livery.objects.filter(name="FlixBus").first()
        self.interval = None  # observed gap between feed timestamps
        self.source_timestamp = None
        self.trips = {}
        self.journeys = {}
        return self

    def get_trips(self):
        source = DataSource.objects.get(id=self.source.id)
        if self.trips and source.datetime == self.source_timestamp:
            return

        # timetables have updated, so re-fetch trips

        self.trips = {
            trip.vehicle_journey_code: trip
            for trip in Trip.objects.filter(route__source=source).select_related(
                *self.trip_select_related
            )
        }
        self.source_timestamp = source.datetime

    def get_items(self):
        previous_timestamp = self.source.datetime

        feed = self.get_feed()
        if feed is None:  # not modified
            return

        self.get_trips()

        new_timestamp = self.source.datetime

        if previous_timestamp and new_timestamp > previous_timestamp:
            self.interval = (new_timestamp - previous_timestamp).total_seconds()

        self.trip_updates = {}
        positions = []
        for item in feed.entity:
            if item.HasField("vehicle"):
                trip = item.vehicle.trip
            else:
                trip = item.trip_update.trip
            if trip.trip_id.startswith("UK") or trip.trip_id in self.trips:
                if item.HasField("vehicle"):
                    positions.append(item)
                else:
                    self.trip_updates[(trip.trip_id, trip.start_date)] = (
                        item.trip_update
                    )

        current = {self.get_vehicle_identity(item) for item in positions}
        # remove old journeys from cache
        self.journeys = {
            key: journey for key, journey in self.journeys.items() if key in current
        }

        yield from positions

        cache.set(
            "flixbus_trip_updates",
            {
                trip_id: json_format.MessageToDict(trip_update)
                for (trip_id, _), trip_update in self.trip_updates.items()
            },
            300,
        )

        cache.set("flixbus_feed", json_format.MessageToDict(feed))

    def update(self):
        now = datetime.now(UTC)

        try:
            (
                changed_items,
                changed_journey_items,
                changed_item_identities,
                changed_journey_identities,
                total_items,
            ) = import_live_vehicles.ImportLiveVehiclesCommand.get_changed_items(self)
        except requests.exceptions.RequestException:
            logger.exception("error getting changed items")
            return self.wait

        if changed_items or changed_journey_items:
            self.refresh_journey_vehicles()

            self.handle_items(changed_items, changed_item_identities)
            self.handle_items(changed_journey_items, changed_journey_identities)

        age = now - self.source.datetime

        self.status.append(
            import_live_vehicles.Status(
                now,
                self.source.datetime,
                age,
                total_items,
                len(changed_items) + len(changed_journey_items),
                (datetime.now(UTC) - now).total_seconds,
            )
        )
        self.status = self.status[-50:]
        import_live_vehicles.cache.set(self.status_key, self.status, None)

        if not self.interval:
            return self.wait

        wait = self.interval - age.total_seconds()
        return min(max(wait, 10), self.wait)

    @staticmethod
    def get_vehicle_identity(item):
        return (
            item.vehicle.trip.trip_id,
            item.vehicle.trip.start_date,
            item.vehicle.trip.start_time,
        )

    def refresh_journey_vehicles(self):
        # journeys may have gained vehicle ids from another source,
        if journeys := {
            journey.id: journey
            for journey in self.journeys.values()
            if not journey.vehicle_id
        }:
            for journey_id, vehicle_id in VehicleJourney.objects.filter(
                id__in=journeys, vehicle__isnull=False
            ).values_list("id", "vehicle_id"):
                journey = journeys[journey_id]
                journey.vehicle_id = vehicle_id
                import_live_vehicles.redis_client.delete(f"vehicle{-journey.id}")

    def get_journey(self, trip_code, start_date, start_time):
        date = datetime.strptime(start_date, "%Y%m%d").date()  # noqa: DTZ007

        if journey := (
            VehicleJourney.objects.filter(
                source=self.source, vehicle=None, code=trip_code, date=date
            )
            .select_related("service")
            .first()
        ):
            return journey

        journey = VehicleJourney(
            source=self.source,
            vehicle=None,
            code=trip_code,
            date=date,
            route_name=trip_code.split("-", 1)[0].removeprefix("UK"),
        )

        # (the noon minus 12 hours trick copes with daylight saving time)
        noon = datetime.strptime(f"{start_date} 12:00:00", "%Y%m%d %H:%M:%S").replace(
            tzinfo=self.tzinfo
        )

        if trip := self.trips.get(trip_code):
            journey.trip = trip
            journey.datetime = noon - timedelta(hours=12) + trip.start
            journey.service = trip.route.service
            journey.destination = str(trip.destination.locality or trip.destination)
        else:
            journey.datetime = noon - timedelta(hours=12) + parse_duration(start_time)

        if journey.datetime - self.source.datetime > timedelta(hours=12):
            # `start_date` is today but the trip's operational day is yesterday
            journey.datetime -= timedelta(days=1)
            journey.date -= timedelta(days=1)

        if journey.service:
            if not journey.service.tracking:
                journey.service.tracking = True
                journey.service.save(update_fields=["tracking"])

            # existing journey with a vehicle id (Scotland)
            if trip and (
                existing_journey := (
                    journey.service.vehiclejourney_set.filter(
                        trip=trip, date=journey.date, datetime=journey.datetime
                    ).first()
                )
            ):
                existing_journey.service = journey.service
                return existing_journey

        journey.save()

        return journey

    def handle_items(self, items, identities):
        for item, identity in zip(items, identities):
            self.handle_item(item)
            self.identifiers[identity] = self.get_item_identity(item)

    def handle_item(self, item):
        key = self.get_vehicle_identity(item)
        if not (journey := self.journeys.get(key)):
            journey = self.get_journey(*key)
            self.journeys[key] = journey

        updated_at = self.get_datetime(item)

        redis_client = import_live_vehicles.redis_client

        vehicle_or_journey_id = journey.vehicle_id or -journey.id

        latest = redis_client.get(f"vehicle{vehicle_or_journey_id}")
        if latest:
            latest = json.loads(latest)
            if datetime.fromisoformat(latest["datetime"]) >= updated_at:
                return

        location = self.create_vehicle_location(item)
        location.datetime = updated_at
        location.journey = journey
        location.id = vehicle_or_journey_id

        if latest and location.heading is None:
            latest_latlong = Point(*latest["coordinates"])
            if latest_latlong.equals_exact(location.latlong, 0.001):
                location.heading = latest["heading"]
            else:
                location.heading = calculate_bearing(latest_latlong, location.latlong)

        redis_json = location.get_redis_json(tz=self.tzinfo)
        if self.livery and not journey.vehicle_id:
            redis_json["vehicle"] = {
                "name": item.vehicle.vehicle.license_plate or "FlixBus",
                "livery": self.livery.id,
                "colour": self.livery.colour,
            }
        if journey.service_id and "service" in redis_json:
            redis_json["service"]["url"] = journey.service.get_absolute_url()

        pipeline = redis_client.pipeline(transaction=False)
        pipeline.geoadd(
            "vehicle_location_locations",
            [location.latlong.x, location.latlong.y, location.id],
        )
        if journey.service_id:
            pipeline.sadd(f"service{journey.service_id}vehicles", location.id)
        pipeline.sadd("operatorFLIXvehicles", location.id)
        pipeline.set(f"vehicle{location.id}", json.dumps(redis_json), ex=900)
        pipeline.execute()

        channel_layer = get_channel_layer()
        if channel_layer is not None:
            try:
                async_to_sync(channel_layer.send)(
                    VEHICLE_POSITIONS_CHANNEL,
                    {
                        "type": "move_vehicles",
                        "items": [
                            (
                                journey.get_redis_key(),
                                int(location.datetime.timestamp()),
                                location.latlong.x,
                                location.latlong.y,
                            )
                        ],
                    },
                )
            except ChannelFull as e:
                # distribute_vehicle_locations worker isn't keeping up (or is down) -
                # drop this update rather than blocking the importer
                logger.error(e)
