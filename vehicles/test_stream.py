import asyncio

from channels.layers import get_channel_layer
from channels_redis.pubsub import RedisPubSubLoopLayer
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

from .utils import CHANNEL_GROUP_PREFIX


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)
class VehiclesStreamTest(SimpleTestCase):
    async def test_services(self):
        response = await self.async_client.get("/stream/services/1,2")
        self.assertEqual(response.headers["Content-Type"], "text/event-stream")
        stream = response.streaming_content
        self.assertRegex(await anext(stream), rb"^retry: \d+\n\n$")

        layer = get_channel_layer()
        await layer.group_send(
            "service3",
            {"type": "move_vehicles", "items": [{"id": 4, "coordinates": [1, 52]}]},
        )
        await layer.group_send(
            "service2",
            {"type": "move_vehicles", "items": [{"id": 3, "coordinates": [1, 52]}]},
        )
        self.assertEqual(
            await anext(stream), b'data: [{"id":3,"coordinates":[1,52]}]\n\n'
        )

        # client disconnects - Django cancels the response task
        task = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(layer.groups, {})

    def test_bad_path(self):
        with self.assertRaises(Resolver404):
            resolve("/stream/stops/1")
        with self.assertRaises(Resolver404):
            resolve("/stream/services/1,,2")

    def test_group_prefix(self):
        # import_live_vehicles publishes straight to the pubsub layer's group channels
        layer = RedisPubSubLoopLayer(hosts=["redis://"])
        self.assertEqual(
            layer._get_group_channel_name("service1"),
            f"{CHANNEL_GROUP_PREFIX}service1",
        )
