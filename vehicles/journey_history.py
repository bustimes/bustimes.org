"""Shared encode + redis-write helpers for vehicle journey histories.

A journey's history is a single redis STRING per journey, grown with APPEND.
Concurrent writers (multiple importer processes for the same vehicle/journey)
are handled with WATCH/MULTI: each writer reads the existing polyline, decodes
the tail, encodes its new points as deltas from that tail, and atomically
APPENDs. On conflict, retry.
"""

import logging

from redis.exceptions import WatchError

from .time_aware_polyline import (
    decode_time_aware_polyline,
    extend_time_aware_polyline,
)

logger = logging.getLogger(__name__)

KEY_PREFIX = b"jh:"
KEY_TTL = 60 * 60 * 24  # 24h
MAX_RETRIES = 5


def history_key(journey_uuid_bytes: bytes) -> bytes:
    return KEY_PREFIX + journey_uuid_bytes


def append_journey_history(redis, uuid_bytes: bytes, points: list[list]) -> None:
    """Append `points` (each `[lng, lat, ts]`) to journey `uuid_bytes`'s polyline.

    Atomic w.r.t. concurrent writers via WATCH/MULTI.
    """
    if not points:
        return
    key = history_key(uuid_bytes)
    for _ in range(MAX_RETRIES):
        with redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                existing = pipe.get(key)
                if existing:
                    if isinstance(existing, bytes):
                        existing = existing.decode("ascii")
                    decoded = decode_time_aware_polyline(existing)
                    last_gpx_log = decoded[-1] if decoded else None
                else:
                    last_gpx_log = None
                fragment = extend_time_aware_polyline("", points, last_gpx_log)
                pipe.multi()
                pipe.append(key, fragment)
                pipe.expire(key, KEY_TTL)
                pipe.execute()
                return
            except WatchError:
                continue
    logger.warning(
        "history append for %s gave up after %d retries", uuid_bytes.hex(), MAX_RETRIES
    )


def read_journey_history(redis, uuid_bytes: bytes) -> list[list]:
    """Returns list of [lng, lat, ts] (matches the encoded order)."""
    raw = redis.get(history_key(uuid_bytes))
    if not raw:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode("ascii")
    return decode_time_aware_polyline(raw)
