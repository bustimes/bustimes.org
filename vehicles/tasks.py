import functools
import json
import zipfile
from datetime import datetime, timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Count, Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task

from busstops.models import DataSource, Operator, Service

from .management.commands import import_bod_avl
from .models import (
    SiriSubscription,
    Vehicle,
    VehicleCode,
    VehicleJourney,
    VehicleRevision,
)
from .utils import archive_avl_data


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
            json.dumps(items),
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
            timezone.now() - now,
        )
    )
    stats = stats[-50:]
    cache.set(key, stats, 800)


@db_task()
def log_vehicle_journey(
    service: int, data: dict, time, destination, source_name, url, trip_id: int
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
    """
    count the number of current vehicle journeys
    (in total, with services, and with trips)
    and pending vehicle revisions
    for the /status graphs
    """

    now = timezone.now()
    half_hour_ago = now - timedelta(minutes=30)
    journeys = VehicleJourney.objects.filter(
        latest_vehicle__isnull=False, datetime__gte=half_hour_ago
    )

    stats = {
        "datetime": now,
        "pending_vehicle_edits": VehicleRevision.objects.filter(
            ~Q(disapproved=True), pending=True
        ).count(),
        "vehicle_journeys": journeys.count(),
        "service_vehicle_journeys": journeys.filter(service__isnull=False).count(),
        "trip_vehicle_journeys": journeys.filter(trip__isnull=False).count(),
    }

    history = cache.get("vehicle-tracking-stats", [])

    history = history[-3000:] + [stats]

    cache.set("vehicle-tracking-stats", history, None)


@db_periodic_task(crontab(minute=4, hour=10))
def timetable_source_stats():
    """
    update the other /status graph
    """

    now = timezone.now()

    sources = (
        DataSource.objects.annotate(
            count=Count(
                "route__service",
                filter=Q(route__service__current=True),
                distinct=True,
            ),
        )
        .filter(count__gt=0)
        .order_by("name")
    )

    stats = {"datetime": now, "sources": {}}
    for source in sources:
        name = source.name
        if "_" in name:
            name = source.name.split("_")[0]
        elif name.startswith("Stagecoach"):
            name = "Stagecoach"

        if name in stats["sources"]:
            stats["sources"][name] += source.count
        else:
            stats["sources"][name] = source.count

    history = cache.get("timetable-source-stats", [])
    history = history[-3000:]

    history.append(stats)

    cache.set("timetable-source-stats", history, None)


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
