import logging
from collections import defaultdict, namedtuple
from datetime import datetime, timedelta
from time import monotonic, sleep

import requests
import sentry_sdk
from asgiref.sync import async_to_sync
from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Now
from django.utils import timezone
from msgspec.json import decode, encode
from redis.exceptions import ConnectionError
from tenacity import before_sleep_log, retry, wait_exponential

from busstops.models import DataSource
from bustimes.models import Route, Trip

from .. import rtpi
from ..models import Vehicle, VehicleCode, VehicleJourney
from ..utils import VEHICLE_POSITIONS_CHANNEL, calculate_bearing, redis_client

logger = logging.getLogger(__name__)
fifteen_minutes = timedelta(minutes=15)
twelve_hours = timedelta(hours=12)


Status = namedtuple(
    "Status",
    (
        "fetched_at",
        "timestamp",
        "age",
        "total_items",
        "changed_items",
        "time_taken",
        "last_modified",
    ),
    defaults=(None,),
)

JOURNEY_UPDATE_FIELDS = (
    "code",
    "service_id",
    "trip_id",
    "datetime",
    "date",
    "route_name",
    "destination",
    "direction",
    "source_id",
)


def journey_values(journey):
    return tuple(getattr(journey, field) for field in JOURNEY_UPDATE_FIELDS)


def same_journey(journey, last_journey, now):
    if journey.datetime == last_journey.datetime:
        return True
    if (
        journey.trip_id
        and journey.trip_id == last_journey.trip_id
        and timezone.localdate(now) == timezone.localdate(last_journey.datetime)
    ):
        # OriginAimedDepartureTime corrected
        return True
    if journey.datetime and not last_journey.datetime.second:
        # seems safe to assume that last journey had a different departure time
        return False
    if (
        journey.service_id
        and last_journey.service_id
        and journey.service_id != last_journey.service_id
    ):
        return False
    return (
        journey.route_name,
        journey.code,
        journey.direction,
        # journey.trip_id,
        timezone.localdate(now),
    ) == (
        last_journey.route_name,
        last_journey.code,
        last_journey.direction,
        # last_journey.trip_id or journey.trip_id,
        timezone.localdate(last_journey.datetime),
    )


VEHICLE_CACHE_SECONDS = 300


class ImportLiveVehiclesCommand(BaseCommand):
    url = ""
    vehicles = Vehicle.objects.select_related("latest_journey__trip")
    wait = 66
    status_key = None
    tzinfo = None

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("--immediate", action="store_true")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self.status = []
        self.to_save = []
        self.eager_progress = True
        self.journeys_to_create = {}
        self.journeys_to_update = []
        self.journey_snapshots = {}  # journey id: journey_values() as loaded
        self.vehicles_to_update = []
        self.identifiers = {}
        self.journeys_ids = {}
        self.journeys_ids_ids = {}
        self.vehicle_cache = {}  # identity: (vehicle, monotonic() when fetched)
        self.vehicle_cache_pruned_at = 0
        self.duplicate_vehicles = set()  # vehicles on 'two journeys at once'
        self.recent_positions = {}  # vehicle id or identity: hashes of last few coordinates
        self.last_locations = {}  # vehicle id: last location (as in Redis)
        self.max_datetime = None  # newest item timestamp in the current update

    @staticmethod
    def get_datetime():
        return

    def get_progress_journey(self, item, journey):
        """for journeys without a Trip - something with .date, .stop_times and .pairs"""
        return

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_sleep_log(logger, logging.ERROR),
    )
    def get_items(self):
        response = self.session.get(self.url, timeout=20)
        response.raise_for_status()
        return decode(response.content)

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
        identity=None,
    ):
        if dt := self.get_datetime(item):
            if dt.year == 1970:
                dt = None
            elif now and now < dt:
                logger.warning(
                    "%s: %s: %s is %s from now", timezone.now(), vehicle, dt, dt - now
                )
            elif not self.max_datetime or dt > self.max_datetime:
                self.max_datetime = dt

        location = None
        if vehicle is None:
            try:
                vehicle, _ = self.get_vehicle(item)
            except Vehicle.MultipleObjectsReturned:
                logger.exception("multiple vehicles found")
                return
            if not vehicle:
                return

        latest_datetime = None
        location_unchanged = False

        if latest is None:
            latest = redis_client.get(f"vehicle{vehicle.id}")
            if latest:
                latest = decode(latest)
        if (
            not latest
            and (fallback := self.last_locations.get(vehicle.id))
            and (dt or now or timezone.now())
            - datetime.fromisoformat(fallback["datetime"])
            < timedelta(hours=12)
        ):
            # the Redis key has expired (15 minutes after the position's timestamp) -
            # use the last location we saw, so a stuck GPS position
            # doesn't reappear with a new timestamp every 15 minutes
            latest = fallback
        if latest:
            latest_datetime = datetime.fromisoformat(latest["datetime"])
            latest_latlong = Point(*latest["coordinates"])

            if dt and latest_datetime >= dt:
                # timestamp isn't newer
                # but allow if the latest journey from another source has no service info
                if not (
                    vehicle.latest_journey
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
                        location_unchanged = location is not None
                    else:
                        return
                elif any(
                    hash(location.latlong.coords) in self.recent_positions.get(key, ())
                    for key in (vehicle.id, identity)
                ):
                    # back to a position it's already reported and moved on from -
                    # e.g. a stale feed (or a forgotten ticket machine) alongside a live one
                    # (per identity too, as a live feed soon pushes it out of the vehicle's)
                    return

        latest_journey = vehicle.latest_journey
        if keep_journey:
            journey = latest_journey
        else:
            if latest_journey and latest_journey.id:
                self.journey_snapshots.setdefault(
                    latest_journey.id, journey_values(latest_journey)
                )
            journey = self.get_journey(item, vehicle)

            if journey and journey.trip and journey.trip.garage_id != vehicle.garage_id:
                vehicle.garage_id = journey.trip.garage_id
                Vehicle.objects.filter(id=vehicle.id).update(
                    garage_id=vehicle.garage_id
                )

        if not journey:
            return
        journey.vehicle = vehicle

        if (
            latest
            and latest_journey
            and latest_journey.source_id != self.source.id
            and self.source.name != "Bus Open Data"
            and ((dt or now) - latest_datetime).total_seconds()
            < 300  # less than 5 minutes old
            and (latest_journey.service_id or not journey.service_id)
        ):
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
            if (
                latest_journey
                and latest_journey.id
                and same_journey(journey, latest_journey, location.datetime)
            ):
                journey.uuid = latest_journey.uuid
                journey.id = latest_journey.id
                if not journey.datetime:
                    journey.datetime = latest_journey.datetime
                if not journey.service_id:
                    journey.service_id = latest_journey.service_id
                    journey.trip_id = latest_journey.trip_id
                self.journeys_to_update.append(journey)
            elif journey.id:
                self.journeys_to_update.append(journey)
            else:
                if not journey.datetime:
                    journey.datetime = location.datetime
                key = (vehicle.id, journey.datetime)
                if key in self.journeys_to_create:
                    # ! unusually, the same journey in the feed twice
                    journey = self.journeys_to_create[key]
                elif latest_journey and (
                    existing := self.get_existing_journey(vehicle, journey)
                ):
                    # this journey already exists - e.g. the vehicle is on
                    # 'two journeys at once', or alternating between two in
                    # successive updates
                    journey.id = existing.id
                    journey.uuid = existing.uuid
                    self.journeys_to_update.append(journey)
                else:
                    self.journeys_to_create[key] = journey

            journey.source = self.source
            if not journey.date:
                journey.date = timezone.localdate(journey.datetime)
                if (
                    journey.trip
                    and journey.trip.start >= timedelta(days=1)
                    and timezone.localtime(journey.datetime).hour < 12
                ):
                    # if the driver signed in before midnight on the service date,
                    # the calendar date is already correct — don't roll back
                    journey.date -= timedelta(days=1)

            if (
                journey.service_id
                and VehicleJourney.service.is_cached(journey)
                and not journey.service.tracking
            ):
                journey.service.tracking = True
                journey.service.save(update_fields=["tracking"])

            if not (
                latest_journey
                and journey.id  # a new journey can always become the latest_journey
                and journey.datetime < latest_journey.datetime
                # unless latest_journey is implausibly far in the future
                and latest_journey.datetime - location.datetime < timedelta(hours=1)
            ):
                # a vehicle on 'two journeys at once', or alternating between two -
                # the older journey shouldn't become the latest_journey
                vehicle.latest_journey = journey
                if type(item) is dict:
                    vehicle.latest_journey_data = item
                self.vehicles_to_update.append(vehicle)

        location.id = vehicle.id
        location.journey = journey
        if not journey.trip_id:
            location.progress_journey = self.get_progress_journey(item, journey)

        if location.latlong:
            redis_json = location.get_redis_json(tz=self.tzinfo)
            if location_unchanged and self.same_as_latest(redis_json, latest):
                return  # nothing new
            self.last_locations[vehicle.id] = redis_json

            for key in (vehicle.id, identity) if identity else (vehicle.id,):
                self.recent_positions[key] = (
                    hash(location.latlong.coords),
                    *self.recent_positions.get(key, ())[:4],
                )
        self.to_save.append((location, vehicle))

        return location, vehicle

    @staticmethod
    def get_existing_journey(vehicle, journey):
        condition = Q(datetime=journey.datetime)
        if journey.code:
            # (Ticketer) the first item of a journey has no OriginAimedDepartureTime,
            # so that journey's datetime is when it was first seen
            condition |= Q(
                code=journey.code,
                route_name=journey.route_name,
                datetime__range=(
                    journey.datetime - timedelta(minutes=15),
                    journey.datetime + timedelta(minutes=15),
                ),
            )
        candidates = vehicle.vehiclejourney_set.filter(
            condition,
            date__gte=timezone.localdate(journey.datetime) - timedelta(days=1),
        )[:2]
        for existing in candidates:
            if existing.datetime == journey.datetime:
                return existing
        return candidates[0] if candidates else None

    @staticmethod
    def same_as_latest(redis_json, latest):
        # ignoring the things that save() might have added
        latest = {
            key: value
            for key, value in latest.items()
            if key not in ("progress", "trip_ids", "operator_id")
            and (key != "delay" or "delay" in redis_json)
        }
        return redis_json == latest

    def save(self):
        if not self.to_save:
            return

        journeys_to_update = [
            journey
            for journey in self.journeys_to_update
            if self.journey_snapshots.get(journey.id) != journey_values(journey)
        ]
        VehicleJourney.objects.bulk_update(journeys_to_update, JOURNEY_UPDATE_FIELDS)
        self.journeys_to_update = []
        self.journey_snapshots = {}

        VehicleJourney.objects.bulk_create(self.journeys_to_create.values())
        self.journeys_to_create = {}

        # update vehicle records if necessary
        if self.vehicles_to_update:
            for v in self.vehicles_to_update:
                v.latest_journey = v.latest_journey

            try:
                Vehicle.objects.bulk_update(
                    list({v.id: v for v in self.vehicles_to_update}.values()),
                    ["latest_journey", "latest_journey_data"],
                )
            except IntegrityError:
                logger.exception("error bulk updating vehicles")
            self.vehicles_to_update = []

        # update locations in Redis

        pipeline = redis_client.pipeline(transaction=False)

        geoadd = []
        sadd = defaultdict(list)
        items = []
        appendages = []

        for location, vehicle in self.to_save:
            if not location.latlong:
                continue

            appendages.append(
                (
                    location.journey.get_redis_key(),
                    int(location.datetime.timestamp()),
                    location.latlong.x,
                    location.latlong.y,
                )
            )

            # expire 15 minutes after the position's own timestamp
            expiry = 900
            if self.source.datetime:
                expiry -= int(
                    (self.source.datetime - location.datetime).total_seconds()
                )
                if expiry <= 0:
                    continue  # backlogged - record history, but keep off the live map

            # update live map

            geoadd += [location.latlong.x, location.latlong.y, vehicle.id]

            # yes it seems wasteful doing this repeatedly
            # (rather than just for new journeys),
            # but it's cheap, and we can't be sure the last time wasn't more than 15 minutes ago
            if location.journey.service_id:
                key = f"service{location.journey.service_id}vehicles"
                sadd[key].append(vehicle.id)
            if vehicle.operator_id:
                key = f"operator{vehicle.operator_id}vehicles"
                sadd[key].append(vehicle.id)
            try:
                if (
                    location.journey.trip
                    and location.journey.trip.operator_id
                    and location.journey.trip.operator_id != vehicle.operator_id
                ):
                    key = f"operator{location.journey.trip.operator_id}vehicles"
                    sadd[key].append(vehicle.id)
            except Trip.DoesNotExist:
                location.journey.trip = None

            redis_json = location.get_redis_json(tz=self.tzinfo)
            if "block" in redis_json and (
                operator_id := (
                    location.journey.trip and location.journey.trip.operator_id
                )
                or vehicle.operator_id
            ):
                # for matching other trips in the block (see departures.live)
                redis_json["operator_id"] = operator_id
            if self.eager_progress and "trip_id" in redis_json:
                # work out progress (and delay, if the source doesn't tell us)
                # from the trip's stop times, so pages don't have to on every request
                if (
                    VehicleJourney.service.is_cached(location.journey)
                    and location.journey.service
                ):
                    modified_at = location.journey.service.modified_at
                else:
                    # unlikely to have changed mid-journey
                    modified_at = rtpi.UNKNOWN
                try:
                    rtpi.add_progress_and_delay(
                        redis_json,
                        tzinfo=self.tzinfo,
                        modified_at=modified_at,
                    )
                except Exception:
                    logger.exception("error calculating progress and delay")
            elif (
                self.eager_progress
                and location.in_service
                and (progress_journey := location.progress_journey)
                and progress_journey.date == location.journey.date
            ):
                try:
                    rtpi.add_progress_and_delay(
                        redis_json,
                        stop_times=progress_journey.stop_times,
                        tzinfo=self.tzinfo,
                        pairs=progress_journey.pairs,
                    )
                except Exception:
                    logger.exception("error calculating progress and delay")
            items.append(redis_json)
            pipeline.set(
                f"vehicle{vehicle.id}",
                encode(redis_json),
                ex=min(expiry, 900),
            )
            # can't use 'mset' cos it doesn't let us specify an expiry

        if geoadd:
            pipeline.geoadd("vehicle_location_locations", geoadd)
        for key, value in sadd.items():
            pipeline.sadd(key, *value)

        try:
            pipeline.execute()
        except ConnectionError:
            logger.exception("error executing redis pipeline")

        channel_layer = get_channel_layer()
        if channel_layer is not None and appendages:
            try:
                async_to_sync(channel_layer.send)(
                    VEHICLE_POSITIONS_CHANNEL,
                    {"type": "move_vehicles", "items": appendages},
                )
            except ChannelFull as e:
                # distribute_vehicle_locations worker isn't keeping up (or is down) -
                # drop this batch rather than blocking the importer
                logger.error(e)

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

    @staticmethod
    def get_latest_locations(vehicle_ids) -> dict:
        if not vehicle_ids:
            return {}
        vehicle_ids = list(vehicle_ids)
        return {
            vehicle_id: decode(item)
            for vehicle_id, item in zip(
                vehicle_ids,
                redis_client.mget(
                    [f"vehicle{vehicle_id}" for vehicle_id in vehicle_ids]
                ),
            )
            if item
        }

    def handle_items(self, items, identities):
        if not items:
            return

        now = monotonic()
        if now - self.vehicle_cache_pruned_at > VEHICLE_CACHE_SECONDS:
            self.vehicle_cache = {
                identity: cached
                for identity, cached in self.vehicle_cache.items()
                if now - cached[1] < VEHICLE_CACHE_SECONDS
            }
            self.vehicle_cache_pruned_at = now

        journey_identities = [self.get_journey_identity(item) for item in items]

        # still on the same journey - no need to re-fetch the vehicle
        vehicles_by_identity = {}
        to_fetch = []
        for vehicle_identity, journey_identity in zip(identities, journey_identities):
            cached = self.vehicle_cache.get(vehicle_identity)
            if (
                cached
                and now - cached[1] < VEHICLE_CACHE_SECONDS
                and vehicle_identity not in self.duplicate_vehicles
                and self.journeys_ids_ids.get(vehicle_identity)
                == (journey_identity, cached[0].latest_journey_id)
            ):
                vehicles_by_identity[vehicle_identity] = cached[0]
            else:
                to_fetch.append(vehicle_identity)

        vehicle_locations = self.get_latest_locations(
            {vehicle.id for vehicle in vehicles_by_identity.values()}
        )
        # no location - deleted or merged (see signals.py), or just expired;
        # or another worker/identity wrote a different journey
        for vehicle_identity, vehicle in list(vehicles_by_identity.items()):
            latest = vehicle_locations.get(vehicle.id)
            if not latest or latest.get("journey_id") != vehicle.latest_journey_id:
                del vehicles_by_identity[vehicle_identity]
                to_fetch.append(vehicle_identity)

        if to_fetch:
            with sentry_sdk.start_span(name="get vehicle codes"):
                vehicle_codes = (
                    VehicleCode.objects.filter(
                        code__in=to_fetch, scheme=self.vehicle_code_scheme
                    )
                    .select_related("vehicle__latest_journey__trip")
                    .defer("vehicle__latest_journey_data", "vehicle__data")
                )
                fetched_ids = set()
                for code in vehicle_codes:
                    vehicles_by_identity[code.code] = code.vehicle
                    self.vehicle_cache[code.code] = (code.vehicle, now)
                    fetched_ids.add(code.vehicle_id)
            vehicle_locations |= self.get_latest_locations(
                fetched_ids - vehicle_locations.keys()
            )

        i = 1
        handled = {}
        for item, vehicle_identity, journey_identity in zip(
            items, identities, journey_identities
        ):
            concurrent = vehicle_identity in self.duplicate_vehicles

            if vehicle_identity in vehicles_by_identity:
                vehicle = vehicles_by_identity[vehicle_identity]
            else:
                vehicle, _created = self.get_vehicle(item)
                # print(vehicle_identity, vehicle, created)
                if vehicle:
                    VehicleCode.objects.create(
                        code=vehicle_identity,
                        scheme=self.vehicle_code_scheme,
                        vehicle=vehicle,
                    )
                    vehicles_by_identity[vehicle_identity] = vehicle
                    self.vehicle_cache[vehicle_identity] = (vehicle, now)

            keep_journey = False
            if not concurrent and vehicle_identity in self.journeys_ids_ids:
                journey_identity_id = self.journeys_ids_ids[vehicle_identity]
                if vehicle and journey_identity_id == (
                    journey_identity,
                    vehicle.latest_journey_id,
                ):
                    keep_journey = True  # can dumbly keep same latest_journey

            if vehicle:
                result = self.handle_item(
                    item,
                    self.source.datetime,
                    vehicle=vehicle,
                    latest=vehicle_locations.get(vehicle.id, False),
                    keep_journey=keep_journey,
                    identity=vehicle_identity,
                )

                if result:
                    location, vehicle = result
                    if location.latlong:
                        # in case the vehicle is in this batch again (under another identity)
                        vehicle_locations[vehicle.id] = {
                            "coordinates": location.latlong.coords,
                            "datetime": location.datetime.isoformat(),
                            "heading": location.heading,
                        }
                    # the journey this location was written to - not
                    # vehicle.latest_journey, which another source may have set
                    handled[vehicle_identity] = (journey_identity, location.journey)

            self.identifiers[vehicle_identity] = self.get_item_identity(item)

            if i % 500 == 0:
                self.save()
                self.record_journeys_ids_ids(handled)
                handled = {}
            i += 1

        self.save()
        self.record_journeys_ids_ids(handled)

    def record_journeys_ids_ids(self, handled):
        # a newly created journey only has an id after save()
        for vehicle_identity, (journey_identity, journey) in handled.items():
            self.journeys_ids_ids[vehicle_identity] = (journey_identity, journey.id)

    def get_changed_items(self, items=None):
        changed_items = []
        changed_journey_items = []
        changed_item_identities = []
        changed_journey_identities = []
        # (changed items and changed journey items are separate
        # so we can do the quick ones first)

        total_items = 0

        vehicle_identities = set()
        self.duplicate_vehicles = set()

        if items is None:
            items = self.get_items()

        for item in items or ():
            vehicle_identity = self.get_vehicle_identity(item)

            journey_identity = self.get_journey_identity(item)

            total_items += 1

            if vehicle_identity in vehicle_identities:
                # vehicle is on 'two journeys at once'
                self.duplicate_vehicles.add(vehicle_identity)
            else:
                vehicle_identities.add(vehicle_identity)

            if vehicle_identity not in self.identifiers:
                # on a cold start, skip vehicles not heard from in a while
                # (but keep handling known vehicles through an upstream backlog)
                dt = self.get_datetime(item)
                if (
                    dt
                    and dt.year != 1970
                    and self.source.datetime
                    and self.source.datetime - dt > timedelta(minutes=15)
                ):
                    self.identifiers[vehicle_identity] = self.get_item_identity(item)
                    self.journeys_ids[vehicle_identity] = journey_identity
                    continue
            elif (
                self.identifiers.get(vehicle_identity) == self.get_item_identity(item)
                and journey_identity == self.journeys_ids[vehicle_identity]
            ):
                continue
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

        # if not too many vehicles are tracking, try pre-calculating progress and delay
        self.eager_progress = len(changed_items) + len(changed_journey_items) <= 5000

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
            self.max_datetime = None

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
                except requests.exceptions.RequestException:
                    logger.exception("error getting changed items")
                    return self.wait

            handling_started = timezone.now()

            with sentry_sdk.start_span(name="handle quick items") as span:
                span.set_data("count", len(changed_items))
                self.handle_items(changed_items, changed_item_identities)
            with sentry_sdk.start_span(name="handle changed journey items") as span:
                span.set_data("count", len(changed_journey_items))
                self.handle_items(changed_journey_items, changed_journey_identities)

        if not total_items:
            # perhaps it's night time?
            return 120

        # just the handling, not the fetch
        time_taken = (timezone.now() - handling_started).total_seconds()

        if self.source_name:
            timestamp = self.source.datetime
            if timestamp is now and self.max_datetime:
                # the feed has no timestamp of its own
                timestamp = self.max_datetime

            self.status.append(
                Status(
                    now,
                    timestamp,
                    now - timestamp,
                    total_items,
                    len(changed_items) + len(changed_journey_items),
                    time_taken,
                )
            )
            self.status = self.status[-50:]
            cache.set(self.status_key, self.status, None)

            sentry_sdk.metrics.count(
                "vehicle_locations",
                self.status[-1].changed_items,
                attributes={"source": self.source_name},
            )

        took = (timezone.now() - now).total_seconds()
        if took < wait:
            return wait - took
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
