import asyncio
from unittest import mock

import fakeredis
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve
from msgspec import msgpack

from .stream import Broadcaster


class VehiclesStreamTest(SimpleTestCase):
    async def test_services(self):
        redis = fakeredis.FakeAsyncRedis(version=7)
        broadcaster = Broadcaster(redis)

        with mock.patch("vehicles.views.broadcaster", broadcaster):
            response = await self.async_client.get("/stream/services/1,2,2")
            self.assertEqual(response.headers["Content-Type"], "text/event-stream")
            stream = response.streaming_content
            self.assertRegex(await anext(stream), rb"^retry: \d+\n\n$")
            self.assertCountEqual(
                await redis.pubsub_channels(),
                [b"asgi__group__service1", b"asgi__group__service2"],
            )

            await redis.publish(
                "asgi__group__service3", b'[{"id":4,"coordinates":[1,52]}]'
            )
            await redis.publish(
                "asgi__group__service2", b'[{"id":3,"coordinates":[1,52]}]'
            )
            self.assertEqual(
                await anext(stream), b'data: [{"id":3,"coordinates":[1,52]}]\n\n'
            )

            # old format
            await redis.publish(
                "asgi__group__service1",
                msgpack.encode({"type": "move_vehicles", "items": [{"id": 5}]}),
            )
            self.assertEqual(await anext(stream), b'data: [{"id":5}]\n\n')

            # client disconnects - Django cancels the response task
            task = asyncio.ensure_future(anext(stream))
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(broadcaster.queues, {})
            self.assertEqual(await redis.pubsub_channels(), [])

        broadcaster.task.cancel()

    def test_bad_path(self):
        with self.assertRaises(Resolver404):
            resolve("/stream/stops/1")
        with self.assertRaises(Resolver404):
            resolve("/stream/services/1,,2")
