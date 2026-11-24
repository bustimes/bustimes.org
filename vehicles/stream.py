import asyncio
import logging

import redis.asyncio
from django.conf import settings
from msgspec import json, msgpack

logger = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self, client=None):
        self.client = client
        self.pubsub = None
        self.task = None
        self.queues: dict[str, set[asyncio.Queue]] = {}
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()

    async def add(self, channels, queue):
        async with self.lock:
            if self.pubsub is None:
                if self.client is None:
                    self.client = redis.asyncio.Redis.from_url(
                        settings.REDIS_URL, health_check_interval=30
                    )
                self.pubsub = self.client.pubsub()
                self.task = asyncio.create_task(self.listen())
            if new := [channel for channel in channels if channel not in self.queues]:
                await self.pubsub.subscribe(*new)
                self.wake.set()
            for channel in channels:
                self.queues.setdefault(channel, set()).add(queue)

    async def discard(self, channels, queue):
        async with self.lock:
            gone = []
            for channel in channels:
                if queues := self.queues.get(channel):
                    queues.discard(queue)
                    if not queues:
                        del self.queues[channel]
                        gone.append(channel)
            if gone:
                await self.pubsub.unsubscribe(*gone)

    async def listen(self):
        while True:
            if not self.pubsub.subscribed:
                self.wake.clear()
                await self.wake.wait()
            try:
                message = await self.pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=30
                )
            except Exception:
                logger.exception("error receiving from Redis")
                await asyncio.sleep(1)
                continue
            try:
                self.broadcast(message)
            except Exception:
                logger.exception("error broadcasting message")

    def broadcast(self, message):
        if not message or message["type"] != "message":
            return
        if queues := self.queues.get(message["channel"].decode()):
            data = message["data"]
            if not data.startswith(b"["):
                # old channels_redis msgpack format - remove once the importers are redeployed
                data = json.encode(msgpack.decode(data)["items"])
            event = b"data: " + data + b"\n\n"
            for queue in queues:
                queue.put_nowait(event)


broadcaster = Broadcaster()
