from msgspec.json import decode

from vehicles.utils import redis_client


def get_tracking(stop, services):
    if not redis_client:
        return

    set_names = [
        f"service{service.pk}vehicles" for service in services if service.tracking
    ]
    if not set_names:
        return

    vehicle_ids = list(redis_client.sunion(set_names))
    if not vehicle_ids:
        return

    vehicle_locations = redis_client.mget(
        [f"vehicle{int(vehicle_id)}" for vehicle_id in vehicle_ids]
    )
    vehicle_locations = [decode(item) for item in vehicle_locations if item]

    return vehicle_locations
