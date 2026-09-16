import math
from collections import defaultdict
from datetime import UTC, datetime
from time import time

import redis.asyncio
from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError

from .models import VehicleRevision, VehicleRevisionFeature

try:
    redis_client = caches["redis"]._cache.get_client()
except InvalidCacheBackendError:
    redis_client = None

if redis_client:
    async_redis_client = redis.asyncio.Redis.from_url(
        settings.REDIS_URL, max_connections=8
    )
else:
    async_redis_client = None

# channel that import_live_vehicles sends batches of updated vehicle locations to,
# for the distribute_vehicle_locations worker to fan out to websocket groups
VEHICLE_POSITIONS_CHANNEL = "vehicle_positions"

# Redis sorted set of vehicle_id -> number of websocket clients currently watching it,
# updated by VehicleLocationConsumer.connect/disconnect
VEHICLE_WATCHERS_KEY = "vehicle_watchers"

# Redis hashes counting the vehicle locations recorded each minute,
# for the "locations per second" graph on the /status page.
# Keys are like "location-counts:29285760" (Unix time in minutes),
# hash fields are data source names
LOCATION_COUNTS_PREFIX = "location-counts"
LOCATION_COUNTS_TTL = 60 * 60 * 24 * 8  # a week and a bit

# however long a time range is graphed, aim for about this many points
LOCATION_STATS_POINTS = 720


def location_counts_key(minute: int) -> str:
    return f"{LOCATION_COUNTS_PREFIX}:{minute}"


def count_locations(pipeline, source_name: str, count: int):
    """Add a "locations this minute" counter to a Redis pipeline
    (which the caller is responsible for executing)
    """

    key = location_counts_key(int(time()) // 60)
    pipeline.hincrby(key, source_name, count)
    pipeline.expire(key, LOCATION_COUNTS_TTL)


def get_location_stats(hours: int = 24) -> list:
    """Vehicle locations per second per data source over the last so many hours,
    in buckets of a minute or more, for the graph on the /status page
    """

    if not redis_client:
        return []

    minutes = hours * 60
    bucket = math.ceil(minutes / LOCATION_STATS_POINTS)  # minutes per point

    # the current minute is still in progress, so end with the previous one,
    # and line buckets up so that each one covers the same number of minutes
    end = int(time()) // 60 // bucket * bucket
    start = end - math.ceil(minutes / bucket) * bucket

    pipeline = redis_client.pipeline(transaction=False)
    for minute in range(start, end):
        pipeline.hgetall(location_counts_key(minute))
    results = pipeline.execute()

    stats = []
    for i in range(0, len(results), bucket):
        counts = defaultdict(int)
        for result in results[i : i + bucket]:
            for name, count in result.items():
                counts[name.decode()] += int(count)

        stats.append(
            {
                "datetime": datetime.fromtimestamp((start + i) * 60, UTC),
                "sources": {
                    name: round(count / (bucket * 60), 2)
                    for name, count in sorted(counts.items())
                },
            }
        )

    return stats


def filename_from_content_disposition(response) -> str:
    # really not fully RFC 6266 compliant
    return response.headers["Content-Disposition"].split("filename", 1)[1][2:-1]


def archive_avl_data(source, data: bytes | str, filename: str):
    if path := settings.AVL_ARCHIVE_DIR:
        path = path / str(source.id)
        if not path.exists():
            path.mkdir(parents=True)
        path /= filename
        if type(data) is str:
            path.write_text(data)
        else:
            path.write_bytes(data)


def calculate_bearing(a, b):
    a_lat = math.radians(a.y)
    a_lon = math.radians(a.x)
    b_lat = math.radians(b.y)
    b_lon = math.radians(b.x)

    y = math.sin(b_lon - a_lon) * math.cos(b_lat)
    x = math.cos(a_lat) * math.sin(b_lat) - math.sin(a_lat) * math.cos(
        b_lat
    ) * math.cos(b_lon - b_lon)

    bearing_radians = math.atan2(y, x)
    bearing_degrees = math.degrees(bearing_radians)

    if bearing_degrees < 0:
        bearing_degrees += 360

    return round(bearing_degrees)


def get_revision(vehicle, data):
    revision = VehicleRevision(vehicle=vehicle, changes={})
    features = []

    # create a VehicleRevision record

    if "spare_ticket_machine" in data:
        data["notes"] = (
            "Spare ticket machine" if data.pop("spare_ticket_machine") else ""
        )

    if "withdrawn" in data:
        from_value = "Yes" if revision.vehicle.withdrawn else "No"
        to_value = "Yes" if data.pop("withdrawn") else "No"
        revision.changes["withdrawn"] = f"-{from_value}\n+{to_value}"

    if "vehicle_type" in data:
        vehicle_type = data.pop("vehicle_type")
        revision.from_type = revision.vehicle.vehicle_type
        revision.to_type = vehicle_type

    # operator has its own ForeignKey fields:
    if "operator" in data:
        revision.from_operator = revision.vehicle.operator
        revision.to_operator = data.pop("operator")

    if "colours" in data:
        livery = data.pop("colours")
        if revision.vehicle.livery_id != (livery and livery.id):
            revision.from_livery = revision.vehicle.livery
            revision.to_livery = livery
            if revision.vehicle.colours:
                revision.changes["colours"] = f"-{revision.vehicle.colours}\n+"

    if "other_colour" in data:
        to_colour = data.pop("other_colour")
        revision.from_livery = revision.vehicle.livery
        if revision.vehicle.colours != to_colour:
            revision.changes["colours"] = f"-{revision.vehicle.colours}\n+{to_colour}"

    if "features" in data:
        for feature in revision.vehicle.features.all():
            if feature not in data["features"]:
                features.append(
                    VehicleRevisionFeature(
                        revision=revision, feature=feature, add=False
                    )
                )
        for feature in data.pop("features"):
            if feature not in revision.vehicle.features.all():
                features.append(
                    VehicleRevisionFeature(revision=revision, feature=feature, add=True)
                )

    if "summary" in data:
        revision.message = data.pop("summary")

    if "fleet_number" in data:
        revision.changes["fleet number"] = (
            f"-{vehicle.fleet_code or vehicle.fleet_number or ''}\n+{data.pop('fleet_number') or ''}"
        )

    if "previous_reg" in data:
        revision.changes["previous reg"] = f"-\n+{data.pop('previous_reg')}"

    for field in ("reg", "notes", "branding", "name"):
        if field in data:
            from_value = getattr(vehicle, field)
            to_value = data.pop(field)
            revision.changes[field] = f"-{from_value}\n+{to_value}"

    assert not data

    return revision, features


def apply_revision(revision, features=None):
    changed_fields = []
    vehicle = revision.vehicle

    if revision.from_type_id != revision.to_type_id:
        vehicle.vehicle_type_id = revision.to_type_id
        changed_fields.append("vehicle_type")

    for field in ("operator", "livery"):
        from_value = getattr(revision, f"from_{field}_id")
        to_value = getattr(revision, f"to_{field}_id")
        if from_value != to_value:
            setattr(vehicle, f"{field}_id", to_value)
            changed_fields.append(field)

    for field in revision.changes:
        value = revision.changes[field]
        from_value, to_value = value.split("\n")
        assert to_value[0] == "+"
        to_value = to_value[1:]

        if field in ("reg", "notes", "branding", "name", "colours"):
            setattr(vehicle, field, to_value)
            changed_fields.append(field)

        elif field == "previous reg":
            if not vehicle.data:
                vehicle.data = {}
            vehicle.data["Previous reg"] = to_value
            changed_fields.append("data")

        elif field == "fleet number":
            vehicle.fleet_code = to_value
            if "/" in to_value:
                to_value = to_value.split("/", 1)[1]
            if to_value.isdigit():
                vehicle.fleet_number = int(to_value)
            else:
                vehicle.fleet_number = None
            changed_fields.append("fleet_number")
            changed_fields.append("fleet_code")

        elif field == "withdrawn":
            if to_value == "Yes":
                vehicle.withdrawn = True
            else:
                assert to_value == "No"
                vehicle.withdrawn = False
            changed_fields.append("withdrawn")

        else:
            assert False

    vehicle.save(update_fields=changed_fields)

    if features is None:
        features = revision.vehiclerevisionfeature_set.all()

    for feature in features:
        if feature.add:
            vehicle.features.add(feature.feature_id)
        else:
            vehicle.features.remove(feature.feature_id)
