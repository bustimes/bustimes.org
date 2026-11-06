import asyncio
import functools
import logging

from channels.layers import get_channel_layer
from django.core.management.base import BaseCommand
from redis.exceptions import ConnectionError
from tqdm import tqdm

from ...time_aware_polyline import (
    decode_time_aware_polyline,
    extend_time_aware_polyline,
)
from ...utils import VEHICLE_POSITIONS_CHANNEL, async_redis_client

logger = logging.getLogger(__name__)

EXPIRY = 3 * 24 * 60 * 60  # archive_old_journey_locations moves them to the database


class PolylineWrapper:
    def __init__(self):
        self.polyline = ""
        self.pending = ""  # newly extended bytes not yet flushed to Redis
        self.last_lat = 0
        self.last_lng = 0
        self.last_time = 0

    def extend(self, lat, lng, time):
        if time < self.last_time:
            # e.g. a vehicle in the feed twice (under two VehicleRefs) with different lags
            return
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

        if unknowns := [
            uuid for uuid, polyline in polylines.items() if not polyline.polyline
        ]:
            strings = await async_redis_client.mget(unknowns)
            for uuid, string in zip(unknowns, strings):
                if string:
                    polylines[uuid].set_polyline(string)

        for uuid, time, x, y in items:
            polylines[uuid].extend(x, y, time)

        append_pipe = async_redis_client.pipeline()
        appending = False

        for uuid, wrapper in polylines.items():
            if wrapper.pending:
                append_pipe.append(uuid, wrapper.pending)
                append_pipe.expire(uuid, EXPIRY)
                wrapper.pending = ""
                appending = True

        if appending:
            await append_pipe.execute()

    async def run(self):
        channel_layer = get_channel_layer()
        progress = tqdm(unit=" locations", disable=None)

        while channel_layer:
            try:
                message = await channel_layer.receive(VEHICLE_POSITIONS_CHANNEL)
                await self.handle_items(message["items"])
                progress.update(len(message["items"]))
            except ConnectionError:
                logger.exception("error distributing vehicle locations")
                raise

    def handle(self, *args, **options):
        asyncio.run(self.run())
