from channels.generic.websocket import AsyncJsonWebsocketConsumer


# firehose
class VehicleLocationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("vehicle_locations", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        print(close_code)
        await self.channel_layer.group_discard("vehicle_locations", self.channel_name)

    async def move_vehicles(self, event):
        items = event.get("items", [])
        # Group flat list into tuples: [[x, y, id], [x, y, id], ...]
        grouped = list(zip(items[::3], items[1::3], items[2::3]))
        message = {"items": grouped}

        # Send message to WebSocket
        await self.send_json(message)
