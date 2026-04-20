from datetime import timedelta, datetime

from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Q

from busstops.models import Operator, Service

from ...models import Vehicle, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand


def parse_date(date):
    return datetime.strptime(date, "%d.%m.%Y").date()


class Command(ImportLiveVehiclesCommand):
    source_name = vehicle_code_scheme = "Translink"
    url = "https://vpos.translinkniplanner.co.uk/velocmap/vmi/VMI"

    def do_source(self):
        self.operators = Operator.objects.filter(
            noc__in=("FY", "GLE", "GDR", "MET", "ULB", "UTS")
        ).in_bulk()

        return super().do_source()

    @staticmethod
    def get_datetime(item):
        return datetime.fromisoformat(item["Timestamp"])

    @staticmethod
    def get_vehicle_identity(item):
        return item["VehicleIdentifier"]

    @staticmethod
    def get_journey_identity(item):
        return (
            item["JourneyIdentifier"],
            item["DirectionText"],
            item["LineText"],
        )

    @staticmethod
    def get_item_identity(item):
        return item["Timestamp"]

    def get_vehicle(self, item) -> tuple[Vehicle, bool]:
        vehicle_code = item["VehicleIdentifier"]

        operator_id, fleet_code = vehicle_code.split("-", 1)

        if operator_id == "TM":
            operator_id = "MET"

        vehicle = self.vehicles.filter(
            fleet_code__iexact=fleet_code,
            operator=operator_id,
        ).first()
        if not vehicle:
            vehicle = self.vehicles.filter(
                fleet_code__iexact=fleet_code,
                operator__in=self.operators,
            ).first()

        if vehicle:
            if vehicle.code != vehicle_code:
                vehicle.code = vehicle_code
                vehicle.save(update_fields=["code"])
            return vehicle, False

        return Vehicle.objects.get_or_create(
            {"fleet_code": fleet_code, "source": self.source},
            operator_id=operator_id,
            code=vehicle_code,
        )

    def get_journey(self, item, vehicle):
        journey = VehicleJourney(
            code=item["JourneyIdentifier"],
            destination=item["DirectionText"],
            route_name=item["LineText"],
        )

        if "#" in journey.code:
            journey.code = journey.code.split("#", 1)[0]

        if (latest_journey := vehicle.latest_journey) and (
            journey.code == latest_journey.code
            and journey.route_name == latest_journey.route_name
        ):
            return latest_journey

        journey.service = Service.objects.filter(
            Q(route__line_name__iexact=journey.route_name)
            | Q(route__line_name__iexact=f"G{journey.route_name}"),
            operator=vehicle.operator_id,
            current=True,
        ).first()
        if not journey.service:
            journey.service = Service.objects.filter(
                operator__in=self.operators,
                route__line_name__iexact=journey.route_name,
                current=True,
            ).first()

        if journey.service:
            journey.trip = journey.get_trip(date=parse_date(item["DayOfOperation"]))
            print(journey.trip)

        return journey

    def create_vehicle_location(self, item):
        delay = item.get("Delay")
        if delay is not None:
            delay = timedelta(seconds=delay)
        return VehicleLocation(
            latlong=GEOSGeometry(f"POINT({item['X']} {item['Y']})"),
            delay=delay,
        )
