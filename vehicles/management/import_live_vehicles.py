import json
import logging
from collections import namedtuple
from datetime import timedelta, datetime
from time import sleep

import requests
import sentry_sdk
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Now
from django.utils import timezone
from redis.exceptions import ConnectionError
from tenacity import before_sleep_log, retry, wait_exponential

from busstops.models import DataSource
from bustimes.models import Route, Trip

from ..models import Vehicle, VehicleJourney, VehicleCode
from ..utils import calculate_bearing, redis_client

logger = logging.getLogger(__name__)
fifteen_minutes = timedelta(minutes=15)
twelve_hours = timedelta(hours=12)


Status = namedtuple(
    "Status",
    ("fetched_at", "timestamp", "age", "total_items", "changed_items", "time_taken"),
)


def same_journey(journey, last_journey, now):
    if journey.datetime == last_journey.datetime:
        return True
    if journey.datetime and not last_journey.datetime.second:
        # seems safe to assume that last journey had a different departure time
        return False
    return (
        journey.service_id,
        journey.route_name,
        journey.code,
        journey.direction,
        # journey.trip_id,
        timezone.localdate(now),
    ) == (
        last_journey.service_id,
        last_journey.route_name,
        last_journey.code,
        last_journey.direction,
        # last_journey.trip_id or journey.trip_id,
        timezone.localdate(last_journey.datetime),
    )


class ImportLiveVehiclesCommand(BaseCommand):
    url = ""
    vehicles = Vehicle.objects.select_related("latest_journey__trip")
    wait = 66
    history = True
    status = []
    status_key = None

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("--immediate", action="store_true")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self.to_save = []
        self.journeys_to_create = {}
        self.journeys_to_update = []
        self.vehicles_to_update = []
        self.identifiers = {}
        self.journeys_ids = {}
        self.journeys_ids_ids = {}

    @staticmethod
    def get_datetime(self):
        return

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_sleep_log(logger, logging.ERROR),
    )
    def get_items(self):
        response = self.session.get(self.url, timeout=20)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_service(queryset, latlong):
        for filtered_queryset in (
            queryset,
            queryset.filter(
                Exists(
                    Route.objects.filter(
                        Q(end_date__gte=Now()) | Q(end_date=None),
                        Q(start_date__lte=Now()) | Q(start_date=None),
                        service=OuterRef("id"),
                    )
                )
            ),
            queryset.filter(geometry__bboverlaps=latlong.buffer(0.1)),
            queryset.filter(geometry__bboverlaps=latlong.buffer(0.05)),
            queryset.filter(geometry__bboverlaps=latlong),
        ):
            try:
                return filtered_queryset.get()
            except queryset.model.MultipleObjectsReturned:
                continue

    def handle_item(
        self,
        item,
        now=None,
        vehicle: Vehicle | None = None,
        latest: dict | None = None,
        keep_journey=False,
    ):
        if dt := self.get_datetime(item):
            if dt.year == 1970:
                dt = None
            elif now and now < dt:
                logger.warning(
                    "%s: %s: %s is %s from now", timezone.now(), vehicle, dt, dt - now
                )

        location = None
        if vehicle is None:
            try:
                vehicle, _ = self.get_vehicle(item)
            except Vehicle.MultipleObjectsReturned as e:
                logger.exception(e)
                return
            if not vehicle:
                return

        latest_datetime = None

        if latest is None:
            latest = redis_client.get(f"vehicle{vehicle.id}")
            if latest:
                latest = json.loads(latest)
        if latest:
            latest_datetime = datetime.fromisoformat(latest["datetime"])
            latest_latlong = Point(*latest["coordinates"])

            if dt and latest_datetime >= dt:
                # timestamp isn't newer
                # but allow if the latest journey from another source has no service info
                if not (
                    vehicle.latest_journey_id
                    and vehicle.latest_journey.source_id != self.source.id
                    and not vehicle.latest_journey.service_id
                ):
                    return
            else:
                location = self.create_vehicle_location(item)
                if not location or location.latlong.equals_exact(latest_latlong):
                    if dt:
                        # location hasn't changed
                        # - so assume the data is old
                        # – if the vehicle was really stationary the location would "drift" a bit
                        dt = latest_datetime
                    else:
                        return

        latest_journey = vehicle.latest_journey
        if keep_journey:
            journey = latest_journey
        else:
            journey = self.get_journey(item, vehicle)

            if (
                journey
                and journey.trip
                and journey.trip.garage_id
                and journey.trip.garage_id != vehicle.garage_id
            ):
                vehicle.garage_id = journey.trip.garage_id
                vehicle.save(update_fields=["garage"])

        if not journey:
            return
        journey.vehicle = vehicle

        if (
            latest
            and latest_journey
            and latest_journey.source_id != self.source.id
            and self.source.name != "Bus Open Data"
        ):
            if ((dt or now) - latest_datetime).total_seconds() < 300:
                # less than 5 minutes old
                if latest_journey.service_id or not journey.service_id:
                    return  # defer to other source

        if not location:
            location = self.create_vehicle_location(item)
            if not location:
                return

        if (
            not (location.latlong.x or location.latlong.y)  # (0, 0) - null island
            or (location.latlong.x == 1 and location.latlong.y == 1)
            or not (
                -180 <= location.latlong.x <= 180
                and -85.05112878 <= location.latlong.y <= 85.05112878
            )
        ):
            location.latlong = None

        if location.heading == -1:
            location.heading = None

        location.datetime = dt
        if not location.datetime:
            location.datetime = now

        if latest and location.latlong and location.heading is None:
            if latest_latlong.equals_exact(location.latlong, 0.001):
                location.heading = latest["heading"]
            else:
                location.heading = calculate_bearing(latest_latlong, location.latlong)

        if keep_journey:
            pass
        else:
            if latest_journey and same_journey(
                journey, latest_journey, location.datetime
            ):
                journey.uuid = latest_journey.uuid
                journey.id = latest_journey.id
                self.journeys_to_update.append(journey)
            elif journey.id:
                self.journeys_to_update.append(journey)
            else:
                key = (vehicle.id, journey.datetime)
                if key in self.journeys_to_create:
                    # ! unusually, the same journey is twice in the feed
                    journey = self.journeys_to_create[key]
                else:
                    self.journeys_to_create[key] = journey

            journey.source = self.source
            if not journey.datetime:
                journey.datetime = location.datetime
            if not journey.date:
                journey.date = timezone.localdate(journey.datetime)
                if journey.trip and journey.trip.start >= timedelta(days=1):
                    # if the driver signed in before midnight on the service date,
                    # the calendar date is already correct — don't roll back
                    if timezone.localtime(journey.datetime).hour < 12:
                        journey.date -= timedelta(days=1)

            if journey.service_id and VehicleJourney.service.is_cached(journey):
                if not journey.service.tracking:
                    journey.service.tracking = True
                    journey.service.save(update_fields=["tracking"])

            vehicle.latest_journey = journey
            if type(item) is dict:
                vehicle.latest_journey_data = item
            self.vehicles_to_update.append(vehicle)

        location.id = vehicle.id
        location.journey = journey

        self.to_save.append((location, vehicle))

        return location, vehicle

    def save(self):
        if not self.to_save:
            return

        update_fields = (
            "code",
            "service",
            "trip",
            "route_name",
            "destination",
            "direction",
            "source",
        )

        VehicleJourney.objects.bulk_update(self.journeys_to_update, update_fields)
        self.journeys_to_update = []

        VehicleJourney.objects.bulk_create(self.journeys_to_create.values())
        self.journeys_to_create = {}

        # update vehicle records if necessary
        if self.vehicles_to_update:
            for v in self.vehicles_to_update:
                v.latest_journey = v.latest_journey

            try:
                Vehicle.objects.bulk_update(
                    self.vehicles_to_update,
                    ["latest_journey", "latest_journey_data"],
                )
            except IntegrityError as e:
                logger.exception(e)
            self.vehicles_to_update = []

        # update locations in Redis

        pipeline = redis_client.pipeline(transaction=False)

        geoadd = []
        sadd = {}
        items = []

        for location, vehicle in self.to_save:
            if not location.latlong or (
                self.source.datetime
                and (self.source.datetime - location.datetime).total_seconds() > 600
            ):
                continue

            # update live map

            geoadd += [location.latlong.x, location.latlong.y, vehicle.id]

            if location.journey.service_id:
                key = f"service{location.journey.service_id}vehicles"
                if key in sadd:
                    sadd[key].append(vehicle.id)
                else:
                    sadd[key] = [vehicle.id]
            if vehicle.operator_id:
                key = f"operator{vehicle.operator_id}vehicles"
                if key in sadd:
                    sadd[key].append(vehicle.id)
                else:
                    sadd[key] = [vehicle.id]
            try:
                if (
                    location.journey.trip
                    and location.journey.trip.operator_id
                    and location.journey.trip.operator_id != vehicle.operator_id
                ):
                    key = f"operator{location.journey.trip.operator_id}vehicles"
                    if key in sadd:
                        sadd[key].append(vehicle.id)
                    else:
                        sadd[key] = [vehicle.id]
            except Trip.DoesNotExist:
                location.journey.trip = None

            redis_json = json.dumps(location.get_redis_json(), cls=DjangoJSONEncoder)
            pipeline.set(f"vehicle{vehicle.id}", redis_json, ex=900)
            # can't use 'mset' cos it doesn't let us specify an expiry (900 secs = 15 min)

            items.append(json.loads(redis_json))

        if geoadd:
            pipeline.geoadd("vehicle_location_locations", geoadd)
        for key in sadd:
            pipeline.sadd(key, *sadd[key])

        if self.history:
            # add locations to journey history

            for location, vehicle in self.to_save:
                if location.latlong:
                    pipeline.rpush(*location.get_appendage())

        try:
            pipeline.execute()
        except ConnectionError as e:
            logger.exception(e)

        channel_layer = get_channel_layer()
        if channel_layer is not None and items:
            group_send = async_to_sync(channel_layer.group_send)
            group_send(
                "vehicle_locations",
                {
                    "type": "move_vehicles",
                    "items": items,
                },
            )

        self.to_save = []

    def do_source(self):
        if self.url:
            self.source, _ = DataSource.objects.get_or_create(
                {"name": self.source_name}, url=self.url
            )
        else:
            self.source = DataSource.objects.get(name=self.source_name)
            self.url = self.source.url
        return self

    def handle_items(self, items, identities):
        with sentry_sdk.start_span(name="get vehicle codes"):
            vehicle_codes = (
                VehicleCode.objects.filter(
                    code__in=identities, scheme=self.vehicle_code_scheme
                )
                .select_related("vehicle__latest_journey__trip")
                .defer("vehicle__latest_journey_data", "vehicle__data")
            )

            vehicles_by_identity = {code.code: code.vehicle for code in vehicle_codes}

        vehicle_locations = redis_client.mget(
            [f"vehicle{vc.vehicle_id}" for vc in vehicle_codes]
        )
        vehicle_locations = {
            vehicle_code.vehicle_id: json.loads(item)
            for vehicle_code, item in zip(vehicle_codes, vehicle_locations)
            if item
        }

        i = 1
        for item, vehicle_identity in zip(items, identities):
            journey_identity = self.journeys_ids[vehicle_identity]

            if vehicle_identity in vehicles_by_identity:
                vehicle = vehicles_by_identity[vehicle_identity]
            else:
                vehicle, created = self.get_vehicle(item)
                # print(vehicle_identity, vehicle, created)
                if vehicle:
                    VehicleCode.objects.create(
                        code=vehicle_identity,
                        scheme=self.vehicle_code_scheme,
                        vehicle=vehicle,
                    )

            keep_journey = False
            if vehicle_identity in self.journeys_ids_ids:
                journey_identity_id = self.journeys_ids_ids[vehicle_identity]
                if journey_identity_id == (journey_identity, vehicle.latest_journey_id):
                    keep_journey = True  # can dumbly keep same latest_journey

            if vehicle:
                result = self.handle_item(
                    item,
                    self.source.datetime,
                    vehicle=vehicle,
                    latest=vehicle_locations.get(vehicle.id, False),
                    keep_journey=keep_journey,
                )

                if result:
                    location, vehicle = result

                self.journeys_ids_ids[vehicle_identity] = (
                    journey_identity,
                    vehicle.latest_journey_id,
                )

            self.identifiers[vehicle_identity] = self.get_item_identity(item)

            if i % 500 == 0:
                self.save()
            i += 1

        self.save()

    def get_changed_items(self, items=None):
        changed_items = []
        changed_journey_items = []
        changed_item_identities = []
        changed_journey_identities = []
        # (changed items and changed journey items are separate
        # so we can do the quick ones first)

        total_items = 0

        for i, item in enumerate(items or self.get_items() or ()):
            vehicle_identity = self.get_vehicle_identity(item)

            journey_identity = self.get_journey_identity(item)

            total_items += 1

            if self.identifiers.get(vehicle_identity) == self.get_item_identity(item):
                if journey_identity == self.journeys_ids[vehicle_identity]:
                    continue
                print(self.journeys_ids[vehicle_identity], item)
            if (
                vehicle_identity not in self.journeys_ids
                or journey_identity != self.journeys_ids[vehicle_identity]
            ):
                changed_journey_items.append(item)
                changed_journey_identities.append(vehicle_identity)
            else:
                changed_items.append(item)
                changed_item_identities.append(vehicle_identity)

            self.journeys_ids[vehicle_identity] = journey_identity

        return (
            changed_items,
            changed_journey_items,
            changed_item_identities,
            changed_journey_identities,
            total_items,
        )

    def update(self) -> int:
        with sentry_sdk.start_transaction(name=f"{self.source.name} update"):
            now = timezone.localtime()
            self.source.datetime = now

            wait = self.wait

            with sentry_sdk.start_span(name="get changed items"):
                try:
                    (
                        changed_items,
                        changed_journey_items,
                        changed_item_identities,
                        changed_journey_identities,
                        total_items,
                    ) = self.get_changed_items()
                except requests.exceptions.RequestException as e:
                    logger.exception(e)
                    return self.wait

            with sentry_sdk.start_span(name="handle quick items") as span:
                span.set_data("count", len(changed_items))
                self.handle_items(changed_items, changed_item_identities)
            with sentry_sdk.start_span(name="handle changed journey items") as span:
                span.set_data("count", len(changed_journey_items))
                self.handle_items(changed_journey_items, changed_journey_identities)

        if not total_items:
            # perhaps it's night time?
            return 120

        time_taken = (timezone.now() - now).total_seconds()

        if self.source_name:
            self.status.append(
                Status(
                    self.source.datetime,
                    None,
                    None,
                    total_items,
                    len(changed_items) + len(changed_journey_items),
                    time_taken,
                )
            )
            self.status = self.status[-50:]
            cache.set(self.status_key, self.status, 800)

        if time_taken < wait:
            return wait - time_taken
        return 0  # took longer than minimum wait

    def handle(self, immediate=False, *args, **options):
        if self.source_name:
            self.status_key = f"{self.source_name.replace(' ', '_')}_status"
            self.status = cache.get(self.status_key, [])

        if not immediate:
            sleep(self.wait)
        self.do_source()
        while True:
            wait = self.update()
            sleep(wait)
