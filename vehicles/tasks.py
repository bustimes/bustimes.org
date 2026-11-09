import functools
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import batched

import sentry_sdk
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Count, Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task
from msgspec.json import encode

from busstops.models import DataSource, Operator, Service

from .management.commands import import_bod_avl
from .models import (
    SiriSubscription,
    Vehicle,
    VehicleCode,
    VehicleJourney,
    VehicleLocationHistory,
)
from .utils import archive_avl_data, redis_client


@functools.cache
def get_bod_avl_command(source: DataSource):
    command = import_bod_avl.Command()
    command.source = source
    command.source_name = source.name
    return command


SIRI_CHANNEL = "bod_avl.siri"


def queue_siri_post(uuid, data: dict):
    """for the import_bod_avl --siri worker, or else huey"""
    if channel_layer := get_channel_layer("bod_avl"):
        async_to_sync(channel_layer.send)(
            SIRI_CHANNEL, {"uuid": str(uuid), "data": data}
        )
    else:
        handle_siri_post(uuid, data)


@db_task()
def handle_siri_post(uuid, data: dict):
    now = timezone.now()

    data = data["Siri"]

    subscription = SiriSubscription.objects.select_related("source").get(uuid=uuid)

    if "HeartbeatNotification" in data:
        timestamp = datetime.fromisoformat(
            data["HeartbeatNotification"]["RequestTimestamp"]
        )
        total_items = None
        changed_items = changed_journey_items = ()
    else:
        data = data["ServiceDelivery"]

        command = get_bod_avl_command(subscription.source)

        items = data["VehicleMonitoringDelivery"]["VehicleActivity"]

        timestamp = datetime.fromisoformat(data["ResponseTimestamp"])
        command.source.datetime = timestamp

        (
            changed_items,
            changed_journey_items,
            changed_item_identities,
            changed_journey_identities,
            total_items,
        ) = command.get_changed_items(items)

        command.handle_items(changed_items, changed_item_identities)
        command.handle_items(changed_journey_items, changed_journey_identities)

        archive_avl_data(
            command.source,
            encode(items),
            timestamp.strftime("%Y-%m-%d_%H%M%S.json"),
        )

    # stats for last 50 updates:
    key = subscription.get_status_key()
    stats = cache.get(key, [])
    stats.append(
        import_bod_avl.Status(
            now,
            timestamp,
            now - timestamp,
            total_items,
            len(changed_items) + len(changed_journey_items),
            (timezone.now() - now).total_seconds(),
        )
    )
    stats = stats[-50:]
    cache.set(key, stats, 800)


@db_task()
def log_vehicle_journey(
    *,
    data: dict,
    source_name: str,
    url: str,
    service: int | None = None,
    time=None,
    date=None,
    destination: str = "",
    trip_id: int | None = None,
):
    operator_ref = data.get("OperatorRef")
    if operator_ref == "SWB":  # Stagecoach
        return

    if not time:
        time = data.get("OriginAimedDepartureTime")
    if not time:
        return

    vehicle = data["VehicleRef"]

    if operator_ref:
        vehicle = vehicle.removeprefix(f"{operator_ref}-")

    for prefix in ("WCM", "SHU", "MCG_Fleet", "112", "SCLK"):
        vehicle = vehicle.removeprefix(f"{prefix}-")

    if not vehicle or vehicle == "-":
        return

    operator = None
    if operator_ref:
        operator = Operator.objects.filter(noc=operator_ref).first()

    if not operator:
        try:
            operator = Operator.objects.get(service=service)
        except (Operator.DoesNotExist, Operator.MultipleObjectsReturned):
            return

    if operator.name.startswith("Stagecoach "):
        return

    vehicle_code_code = f"{operator_ref}:{vehicle}"
    vehicle_code = VehicleCode.objects.filter(
        scheme=source_name, code=vehicle_code_code
    ).first()

    data_source, _ = DataSource.objects.get_or_create({"url": url}, name=source_name)

    if vehicle_code:
        vehicle = vehicle_code.vehicle
    else:
        # get or create vehicle
        defaults = {"source": data_source, "operator": operator, "code": vehicle}

        operator_query = Q(operator=operator)
        if operator.group_id:
            operator_query |= Q(operator__group=operator.group_id)
        vehicles = Vehicle.objects.filter(
            operator_query | Q(source=data_source)
        ).select_related("latest_journey")

        if vehicle.isdigit():
            defaults["fleet_number"] = vehicle
            vehicles = vehicles.filter(
                Q(code=vehicle)
                | Q(code__endswith=f"-{vehicle}")
                | Q(code__startswith=f"{vehicle}_-_")
            )
        else:
            vehicles = vehicles.filter(code__iexact=vehicle)

        try:
            vehicle, _ = vehicles.get_or_create(defaults)
        except Vehicle.MultipleObjectsReturned:
            vehicle = vehicles.filter(operator=operator).first()

        VehicleCode.objects.create(
            scheme=source_name, code=vehicle_code_code, vehicle=vehicle
        )

    if isinstance(time, str):
        time = datetime.fromisoformat(time)

    if last_journey := vehicle.latest_journey:
        last_time = last_journey.datetime
        if last_time == time or (
            last_journey.source_id != data_source.id
            and time - last_time < timedelta(hours=2)
        ):
            return

    if (
        "FramedVehicleJourneyRef" in data
        and "DatedVehicleJourneyRef" in data["FramedVehicleJourneyRef"]
    ):
        journey_ref = data["FramedVehicleJourneyRef"]["DatedVehicleJourneyRef"]
    else:
        journey_ref = None

    destination = destination or ""
    route_name = data.get("LineName") or data.get("LineRef")

    if not date:
        date = timezone.localdate(time)

    journeys = vehicle.vehiclejourney_set.filter(date=date)

    existing = VehicleJourney.objects.filter(
        Q(vehicle=vehicle) | Q(vehicle=None, service=service), date=date, datetime=time
    ).first()

    if (
        existing
        and existing.vehicle_id
        or journey_ref
        and journeys.filter(route_name=route_name, code=journey_ref).exists()
    ):
        return

    journey = VehicleJourney(
        vehicle=vehicle,
        service_id=service,
        route_name=route_name,
        code=journey_ref,
        datetime=time,
        source=data_source,
        destination=destination,
        trip_id=trip_id,
    )

    if operator_ref == "FLIX" and not service:
        journey.service = Service.objects.filter(
            line_name=f"UK{route_name}", operator=operator_ref, current=True
        ).first()

    match data.get("DirectionRef", "").lower():
        case "i" | "inbound":
            journey.direction = "inbound"
        case "o" | "outbound":
            journey.direction = "outbound"

    if existing:  # FlixBus journey with no vehicle id yet
        journey.id = existing.id
        journey.uuid = existing.uuid

    if not trip_id:
        journey.trip = journey.get_trip(
            departure_time=time, destination_ref=data.get("DestinationRef")
        )
    if not journey.date:
        journey.date = date

    try:
        journey.save()
    except IntegrityError:
        return

    if not vehicle.latest_journey or vehicle.latest_journey.datetime < journey.datetime:
        if journey.trip and journey.trip.garage_id != vehicle.garage_id:
            vehicle.garage_id = journey.trip.garage_id
        vehicle.latest_journey = journey
        vehicle.latest_journey_data = data
        vehicle.save(update_fields=["garage", "latest_journey", "latest_journey_data"])


@db_periodic_task(crontab(minute="*/5"))
def stats():
    journeys = VehicleJourney.objects.filter(
        latest_vehicle__isnull=False,
        datetime__gte=timezone.now() - timedelta(minutes=30),
    ).aggregate(
        vehicle_journeys=Count("id"),
        service_vehicle_journeys=Count("id", filter=Q(service__isnull=False)),
        trip_vehicle_journeys=Count("id", filter=Q(trip__isnull=False)),
    )
    for key, value in journeys.items():
        sentry_sdk.metrics.gauge(key, value)


@db_periodic_task(crontab(minute=10, hour=1))
def compress_avl_archive():
    """
    move files named things like
    2024-03-15_060942.json
    into
    2024-03-15.zip
    """

    today_str = timezone.now().date().isoformat()

    for path in settings.AVL_ARCHIVE_DIR.iterdir():
        if not path.name.isdigit():
            continue

        date_str = None
        archive = None

        for file_path in sorted(path.iterdir()):
            if file_path.suffix != ".json":
                continue
            if file_path.name.startswith(today_str):
                break
            if not date_str or not file_path.name.startswith(date_str):
                date_str = file_path.name[:10]
                archive = zipfile.ZipFile(
                    path / f"{date_str}.zip", "a", compression=zipfile.ZIP_DEFLATED
                )
            archive.write(file_path, file_path.name)
            file_path.unlink()


def archive_journey_locations(date):
    """move a day's journey polylines from Redis to the database"""
    if not redis_client:
        return
    journeys = VehicleJourney.objects.filter(date=date, vehicle__isnull=False)
    vehicle_ids = journeys.order_by("vehicle_id").values_list("vehicle_id", flat=True)
    for chunk in batched(vehicle_ids.distinct(), 100):
        chunk_journeys = list(
            journeys.filter(vehicle__in=chunk).values_list("id", "vehicle_id", "uuid")
        )
        keys = [uuid.bytes for _, _, uuid in chunk_journeys]
        polylines = redis_client.mget(keys)
        rows = defaultdict(dict)
        for (journey_id, vehicle_id, _), polyline in zip(chunk_journeys, polylines):
            if polyline:
                rows[vehicle_id, date][str(journey_id)] = polyline.decode()
        if rows:
            VehicleLocationHistory.merge(rows)
        redis_client.unlink(*keys)


@db_periodic_task(crontab(minute=30, hour=3))
def archive_old_journey_locations():
    archive_journey_locations(timezone.localdate() - timedelta(days=2))
