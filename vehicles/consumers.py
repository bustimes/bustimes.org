import asyncio
import logging

from channels.consumer import AsyncConsumer
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .journey_history import append_journey_history
from .utils import redis_client

logger = logging.getLogger(__name__)


# firehose
class VehicleLocationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("vehicle_locations", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        print(close_code)
        await self.channel_layer.group_discard("vehicle_locations", self.channel_name)

    async def move_vehicles(self, event):
        await self.send_json({"items": event["items"]})


# background worker — currently unused (importers write inline via
# append_journey_history). Kept for when we want to debounce writes from
# multiple importer processes across a single coordinator.
class JourneyHistoryWriter(AsyncConsumer):
    FLUSH_INTERVAL = 30.0
    MAX_BUFFERED_POINTS = 60

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # uuid_bytes -> [[lng, lat, ts], ...]
        self._buffers: dict[bytes, list[list]] = {}
        self._flusher: asyncio.Task | None = None

    def _ensure_flusher(self):
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self):
        try:
            while self._buffers:
                await asyncio.sleep(self.FLUSH_INTERVAL)
                await self._flush_all()
        except asyncio.CancelledError:
            await self._flush_all()
            raise

    async def _flush_all(self):
        if not self._buffers:
            return
        buffers, self._buffers = self._buffers, {}
        for uuid_bytes, points in buffers.items():
            points.sort(key=lambda p: p[2])
            try:
                await asyncio.to_thread(
                    append_journey_history, redis_client, uuid_bytes, points
                )
            except Exception:
                logger.exception("history flush failed for %s", uuid_bytes.hex())

    async def append(self, event):
        uuid_bytes = event["uuid"]
        points = event["points"]  # list of [lng, lat, ts]
        buf = self._buffers.setdefault(uuid_bytes, [])
        buf.extend(points)
        if len(buf) >= self.MAX_BUFFERED_POINTS:
            self._buffers.pop(uuid_bytes)
            buf.sort(key=lambda p: p[2])
            try:
                await asyncio.to_thread(
                    append_journey_history, redis_client, uuid_bytes, buf
                )
            except Exception:
                logger.exception("history early-flush failed for %s", uuid_bytes.hex())
        self._ensure_flusher()
