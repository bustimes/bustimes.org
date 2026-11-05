import requests
from django.core.management.base import BaseCommand

from ...models import Vehicle, VehicleType


class Command(BaseCommand):
    def handle(self, *args, **options):
        response = requests.get(
            "https://www.zditm.szczecin.pl/api/v1/vehicles",
            headers={"User-Agent": "bustimes.org"},
            timeout=10,
        )
        response.raise_for_status()

        vehicles = {
            vehicle.code: vehicle
            for vehicle in Vehicle.objects.filter(operator="ZDITM")
        }
        vehicle_types = {}
        to_update = []

        for item in response.json()["data"]:
            vehicle = vehicles.get(str(item["vehicle_id"]))
            if not vehicle:
                continue
            changed = False

            if not vehicle.vehicle_type_id and (model := item["vehicle_model"]):
                model = model.replace("<->", "(bidirectional)")
                if model not in vehicle_types:
                    vehicle_types[model] = VehicleType.objects.get_or_create(
                        name=model,
                        defaults={
                            "style": "tram" if item["vehicle_type"] == "tram" else ""
                        },
                    )[0]
                vehicle.vehicle_type = vehicle_types[model]
                changed = True

            # subcontractor
            if (operator := item["vehicle_operator"]) and (
                not vehicle.data or vehicle.data.get("Operator") != operator
            ):
                vehicle.data = {**(vehicle.data or {}), "Operator": operator}
                changed = True

            if changed:
                to_update.append(vehicle)

        Vehicle.objects.bulk_update(to_update, ["vehicle_type", "data"])
