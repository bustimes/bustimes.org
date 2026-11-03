"""Predictions for every London bus at once, from the "URA" Countdown feed -
rather than requesting each stop's or vehicle's arrivals from the TfL API.

Stale-while-revalidate: requests are served from Redis, and the first request
after the data is FRESH seconds old queues a huey task to refresh it.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime

import requests
from django.utils import timezone
from msgspec.json import decode, encode

from vehicles.utils import redis_client

logger = logging.getLogger(__name__)

URL = "https://countdown.api.tfl.gov.uk/interfaces/ura/instant_V1"
# (the feed returns fields in this order, whatever order they're asked for in)
FIELDS = (
    "StopPointName",
    "StopCode2",
    "LineName",
    "DestinationText",
    "TripID",
    "RegistrationNumber",
    "EstimatedTime",
)
PREDICTION, BASE_VERSION = 1, 3  # row types

VEHICLES_KEY = "tfl_countdown_vehicles"
STOPS_KEY = "tfl_countdown_stops"
FRESH_KEY = "tfl_countdown_fresh"
LOCK_KEY = "tfl_countdown_lock"

FRESH = 30  # seconds
EXPIRE = 300  # seconds - after which fall back to the TfL API


def parse(text) -> tuple[dict, dict]:
    rows = decode(f"[{','.join(text.splitlines())}]")

    base_version = None
    for row in rows:
        if row[0] == BASE_VERSION:
            base_version = row[1]
            break

    vehicles = defaultdict(list)
    stops = defaultdict(list)
    times = {}
    for row in rows:
        if row[0] != PREDICTION:
            continue
        name, atco_code, line_name, destination, trip_id, reg, timestamp = row[1:]
        if (expected_arrival := times.get(timestamp)) is None:
            expected_arrival = times[timestamp] = datetime.fromtimestamp(
                timestamp / 1000, UTC
            ).isoformat()
        # (only what api.views and departures.sources need)
        if reg:
            vehicles[reg].append(
                {
                    "naptanId": atco_code,
                    "stationName": name,
                    "tripId": str(trip_id),
                    "baseVersion": base_version,
                    "expectedArrival": expected_arrival,
                }
            )
        if atco_code:
            stops[atco_code].append(
                {
                    "lineName": line_name,
                    "destinationName": destination,
                    "vehicleId": reg,
                    "expectedArrival": expected_arrival,
                    "modeName": "bus",
                }
            )

    return vehicles, stops


def refresh():
    try:
        response = requests.get(
            URL,
            params={"ReturnList": ",".join(FIELDS)},
            headers={"User-Agent": "bustimes.org"},
            timeout=30,
        )
        response.raise_for_status()
        vehicles, stops = parse(response.text)
    except (requests.RequestException, ValueError):
        logger.warning("TfL Countdown error", exc_info=True)
        return  # (leave the lock to expire - back off for a minute)
    if vehicles:
        try:
            save(vehicles, stops)
        except Exception:
            logger.exception("error saving TfL Countdown predictions")


def save(vehicles, stops):
    pipeline = redis_client.pipeline()
    for key, items in ((VEHICLES_KEY, vehicles), (STOPS_KEY, stops)):
        temp_key = f"{key}_new"
        pipeline.delete(temp_key)
        pipeline.hset(
            temp_key, mapping={code: encode(value) for code, value in items.items()}
        )
        pipeline.expire(temp_key, EXPIRE)
        pipeline.rename(temp_key, key)
    pipeline.set(FRESH_KEY, 1, ex=FRESH)
    pipeline.delete(LOCK_KEY)
    pipeline.execute()


def maybe_refresh():
    if not redis_client.exists(FRESH_KEY) and redis_client.set(
        LOCK_KEY, 1, ex=60, nx=True
    ):
        from .tasks import refresh_countdown

        refresh_countdown()


def get_predictions(key, code) -> list | None:
    """None if there's no data (so use the TfL API instead)"""
    if not redis_client:
        return
    try:
        maybe_refresh()
        if (data := redis_client.hget(key, code)) is None:
            if redis_client.exists(key):
                return []
            return
    except Exception:
        logger.exception("error getting TfL Countdown predictions")
        return

    now = timezone.now()
    return [
        item
        for item in decode(data)
        # in case the data is a bit stale
        if datetime.fromisoformat(item["expectedArrival"]) > now
    ]


def get_vehicle_predictions(reg):
    return get_predictions(VEHICLES_KEY, reg)


def get_stop_predictions(atco_code):
    return get_predictions(STOPS_KEY, atco_code)
