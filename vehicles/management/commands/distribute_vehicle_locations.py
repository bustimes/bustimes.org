import asyncio
import functools
import logging

from channels.layers import get_channel_layer
from django.core.management.base import BaseCommand
from redis.exceptions import ConnectionError

from ...models import VehicleLocation
from ...time_aware_polyline import (
    decode_time_aware_polyline,
    encode_time_aware_polyline,
    extend_time_aware_polyline,
)
from ...utils import VEHICLE_POSITIONS_CHANNEL, async_redis_client

logger = logging.getLogger(__name__)


class PolylineWrapper:
    def __init__(self):
        self.polyline = ""
        self.pending = ""  # newly extended bytes not yet flushed to Redis
        self.last_lat = 0
        self.last_lng = 0
        self.last_time = 0

    def extend(self, lat, lng, time):
        extension = extend_time_aware_polyline(
            "",
            ((lat, lng, time),),
            (self.last_lat, self.last_lng, self.last_time),
        )
        if extension == "???":
            return  # no change
        self.polyline += extension
        self.pending += extension
        self.last_lat = lat
        self.last_lng = lng
        self.last_time = time

    def set_polyline(self, polyline):
        if isinstance(polyline, bytes):
            polyline = polyline.decode()
        self.polyline = polyline
        if decoded := decode_time_aware_polyline(polyline):
            self.last_lat, self.last_lng, self.last_time = decoded[-1]


class Command(BaseCommand):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_polyline = functools.lru_cache(maxsize=50_000)(
            lambda uuid: PolylineWrapper()
        )

    async def handle_items(self, items):
        polylines = {uuid: self.get_polyline(uuid) for (uuid, _, _, _) in items}

        pipeline = async_redis_client.pipeline()

        unknowns = [
            (uuid, polyline)
            for uuid, polyline in polylines.items()
            if not polyline.polyline
        ]

        for uuid, _ in unknowns:
            pipeline.type(uuid)

        types = await pipeline.execute()

        list_uuids = []
        string_uuids = []

        list_pipe = async_redis_client.pipeline()

        for pair, type in zip(unknowns, types):
            if type == b"list":
                list_uuids.append(pair)
                list_pipe.lrange(pair[0], 0, -1)
            elif type == b"string":
                string_uuids.append(pair[0])

        lists = await list_pipe.execute()
        strings = await async_redis_client.mget(string_uuids)

        migrate_pipe = async_redis_client.pipeline()
        migrating = False

        for (uuid, wrapper), raw_locations in zip(list_uuids, lists):
            if not raw_locations:
                continue
            decoded = sorted(
                (VehicleLocation.decode_appendage(loc) for loc in raw_locations),
                key=lambda loc: loc["id"],
            )
            polyline = encode_time_aware_polyline(
                [
                    [loc["coordinates"][0], loc["coordinates"][1], loc["id"]]
                    for loc in decoded
                ]
            )
            wrapper.set_polyline(polyline)
            # the key currently holds the old 'list' type, so it needs a
            # full SET (not APPEND) to convert it and store the migrated
            # history; subsequent updates can be appended incrementally
            migrate_pipe.set(uuid, polyline)
            migrating = True

        if migrating:
            await migrate_pipe.execute()

        for uuid, string in zip(string_uuids, strings):
            polylines[uuid].set_polyline(string)

        for uuid, time, x, y in items:
            polylines[uuid].extend(x, y, time)

        append_pipe = async_redis_client.pipeline()
        appending = False

        for uuid, wrapper in polylines.items():
            if wrapper.pending:
                append_pipe.append(uuid, wrapper.pending)
                wrapper.pending = ""
                appending = True

        if appending:
            await append_pipe.execute()

    async def run(self):
        channel_layer = get_channel_layer()

        while True:
            try:
                message = await channel_layer.receive(VEHICLE_POSITIONS_CHANNEL)
                await self.handle_items(message["items"])
            except ConnectionError:
                logger.exception("error distributing vehicle locations")
                raise

    def handle(self, *args, **options):
        asyncio.run(self.run())
