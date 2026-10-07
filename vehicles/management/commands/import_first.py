from datetime import datetime, timedelta

from django.contrib.gis.geos import Point
from django.utils import timezone

from busstops.models import Service

from ...models import Vehicle, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand, logger


class Command(ImportLiveVehiclesCommand):
    source_name = vehicle_code_scheme = "First"

    def get_items(self):
        return super().get_items()["member"]

    def get_datetime(self, item):
        recorded_at_time = datetime.fromisoformat(item["status"]["recorded_at_time"])
        if recorded_at_time > self.source.datetime:
            recorded_at_time -= timedelta(hours=1)
        return recorded_at_time

    def get_vehicle_identity(self, item):
        return self.split_vehicle_id(item)[1]

    @staticmethod
    def get_journey_identity(item):
        return item["status"]["vehicle_id"]

    @staticmethod
    def get_item_identity(item):
        if "id" in item:
            del item["id"]
        return item["status"]["recorded_at_time"]

    def get_vehicle(self, item):
        vehicle_code = self.split_vehicle_id(item)[1]

        fleet_number = vehicle_code if vehicle_code.isdigit() else None

        if fleet_number and (
            vehicle := Vehicle.objects.filter(
                operator__group__name="First", code=vehicle_code
            ).first()
        ):
            return vehicle, False

        return Vehicle.objects.get_or_create(
            {
                "source": self.source,
                "fleet_code": str(fleet_number or ""),
                "fleet_number": fleet_number,
            },
            operator_id=item["operator"],
            code=vehicle_code,
        )

    def get_journey(self, item, vehicle):
        # origin aimed departure time
        departure_time = item["stops"][0]["date"] + " " + item["stops"][0]["time"]
        departure_time = timezone.make_aware(
            datetime.strptime(departure_time, "%Y-%m-%d %H:%M")  # noqa: DTZ007
        )

        try:
            service = (
                Service.objects.filter(
                    current=True,
                    operator=item["operator"],
                    route__line_name__iexact=item["line_name"],
                )
                .distinct()
                .get()
            )
        except (Service.DoesNotExist, Service.MultipleObjectsReturned) as e:
            print(e, item["operator"], item["line_name"])
            service = None

        destination = item["stops"][-1]
        if destination["locality"]:
            destination = destination["locality"].split(", ", 1)[0]
        else:
            destination = destination["stop_name"].split(", ", 1)[0]
        journey = VehicleJourney(
            route_name=item["line_name"],
            code=self.split_vehicle_id(item)[0],
            datetime=departure_time,
            source=self.source,
            destination=destination,
            vehicle=vehicle,
            service=service,
        )
        journey.trip = journey.get_trip(
            departure_time=departure_time,
            destination_ref=item["stops"][-1]["atcocode"],
        )
        if not journey.date:
            journey.date = timezone.localdate(departure_time)

        return journey

    def create_vehicle_location(self, item):
        heading = item["status"]["bearing"]
        if heading == -1:
            heading = None

        return VehicleLocation(
            latlong=Point(*item["status"]["location"]["coordinates"]),
            heading=heading,
        )

    @staticmethod
    def split_vehicle_id(item: dict):
        prefix = f"{item['operator']}-{item['dir']}-"
        suffix = f"-{item['line_name']}"
        vehicle = item["status"]["vehicle_id"]

        if vehicle.startswith(prefix) and vehicle.endswith(suffix):
            vehicle = vehicle.removesuffix(suffix).removeprefix(prefix)
            return vehicle[11:].split("-", 1)
        logger.warning(
            "vehicle %s doesn't have prefix %s and/or suffix %s",
            vehicle,
            prefix,
            suffix,
            exc_info=True,
        )
        parts = vehicle.split("-")
        assert len(parts) >= 8
        item["line_name"] = parts[-1]
        item["operator"] = parts[0]
        item["dir"] = parts[1]
        return parts[5], "-".join(parts[6:-1])

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("operator", type=str)
        parser.add_argument("route_name", type=str, nargs="?")

    def handle(self, operator, route_name=None, *args, **options):
        self.session.params["operator"] = operator
        super().handle(*args, **options)
