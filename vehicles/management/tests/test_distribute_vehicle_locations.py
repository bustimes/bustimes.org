import struct
from unittest import mock
from uuid import uuid4

import fakeredis
from django.test import TestCase

from vehicles.time_aware_polyline import (
    decode_time_aware_polyline,
    encode_time_aware_polyline,
)

from ..commands.distribute_vehicle_locations import Command


class DistributeVehicleLocationsTest(TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeAsyncRedis()
        patcher = mock.patch(
            "vehicles.management.commands.distribute_vehicle_locations.async_redis_client",
            self.redis,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.command = Command()

    async def test_new_vehicle(self):
        uuid = str(uuid4())

        await self.command.handle_items([(uuid, 1000, 1.234, 51.234)])

        polyline = await self.redis.get(uuid)
        self.assertEqual(
            decode_time_aware_polyline(polyline.decode()), [[1.234, 51.234, 1000]]
        )

    async def test_existing_string(self):
        uuid = str(uuid4())
        existing = encode_time_aware_polyline([[1.0, 51.0, 1000]])
        await self.redis.set(uuid, existing)

        await self.command.handle_items([(uuid, 1010, 1.001, 51.001)])

        polyline = (await self.redis.get(uuid)).decode()
        self.assertEqual(
            decode_time_aware_polyline(polyline),
            [[1.0, 51.0, 1000], [1.001, 51.001, 1010]],
        )

    async def test_migrate_old_list(self):
        uuid = str(uuid4())
        # old struct-packed appendage format, as decoded by VehicleLocation.decode_appendage
        entry = struct.pack("I 2f ?h ?h", 1000, 1.0, 51.0, True, 90, False, 0)
        await self.redis.rpush(uuid, entry)

        await self.command.handle_items([(uuid, 1010, 1.001, 51.001)])

        # the list key should have been replaced by a migrated, then extended, string
        self.assertEqual(await self.redis.type(uuid), b"string")
        polyline = (await self.redis.get(uuid)).decode()
        self.assertEqual(
            decode_time_aware_polyline(polyline),
            [[1.0, 51.0, 1000], [1.001, 51.001, 1010]],
        )

    async def test_two_vehicles_share_pipeline(self):
        uuid_a = str(uuid4())
        uuid_b = str(uuid4())

        await self.command.handle_items(
            [
                (uuid_a, 1000, 1.0, 51.0),
                (uuid_b, 1000, 2.0, 52.0),
            ]
        )

        polyline_a = (await self.redis.get(uuid_a)).decode()
        polyline_b = (await self.redis.get(uuid_b)).decode()
        self.assertEqual(decode_time_aware_polyline(polyline_a), [[1.0, 51.0, 1000]])
        self.assertEqual(decode_time_aware_polyline(polyline_b), [[2.0, 52.0, 1000]])

    async def test_accumulates_across_calls(self):
        uuid = str(uuid4())

        await self.command.handle_items([(uuid, 1000, 1.0, 51.0)])
        await self.command.handle_items([(uuid, 1010, 1.001, 51.001)])
        await self.command.handle_items([(uuid, 1020, 1.002, 51.002)])

        polyline = (await self.redis.get(uuid)).decode()
        self.assertEqual(
            decode_time_aware_polyline(polyline),
            [[1.0, 51.0, 1000], [1.001, 51.001, 1010], [1.002, 51.002, 1020]],
        )

    async def test_unmoved_point_not_appended(self):
        uuid = str(uuid4())

        await self.command.handle_items([(uuid, 1000, 1.0, 51.0)])
        # same coordinates, later timestamp - vehicle hasn't moved
        await self.command.handle_items([(uuid, 1010, 1.0, 51.0)])
        await self.command.handle_items([(uuid, 1020, 1.002, 51.002)])

        polyline = (await self.redis.get(uuid)).decode()
        self.assertEqual(
            decode_time_aware_polyline(polyline),
            [[1.0, 51.0, 1000], [1.002, 51.002, 1020]],
        )

    async def test_unmoved_point_not_appended_existing_string(self):
        uuid = str(uuid4())
        existing = encode_time_aware_polyline([[1.0, 51.0, 1000]])
        await self.redis.set(uuid, existing)

        # same coordinates as the last stored point - vehicle hasn't moved
        await self.command.handle_items([(uuid, 1010, 1.0, 51.0)])

        # nothing should have been appended
        polyline = (await self.redis.get(uuid)).decode()
        self.assertEqual(decode_time_aware_polyline(polyline), [[1.0, 51.0, 1000]])
