from datetime import datetime

import requests
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Q
from django.utils import timezone

from busstops.models import Service

from ...models import Operator, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand
from .import_bod_avl import get_line_name_query


def parse_datetime(string):
    dt = datetime.fromisoformat(string)
    return timezone.make_aware(dt)


class Command(ImportLiveVehiclesCommand):
    wait = 92

    def get_items(self):
        self.session = requests.Session()
        return super().get_items()

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("source_name", type=str)
        ImportLiveVehiclesCommand.add_arguments(parser)

    def handle(self, source_name, **options):
        self.source_name = self.vehicle_code_scheme = source_name
        super().handle(**options)

    @staticmethod
    def get_vehicle_identity(item):
        return f"{item['OperatorRef']}:{item['VehicleRef']}"

    @staticmethod
    def get_journey_identity(item):
        return (
            item["JourneyCode"],
            item["PublishedLineName"],
            item["DestinationRef"],
        )

    @staticmethod
    def get_item_identity(item):
        return item["RecordedAtTime"]

    @staticmethod
    def get_datetime(item):
        return parse_datetime(item["RecordedAtTime"])

    def get_operators(self, item):
        code = item["OperatorRef"]
        return Operator.objects.filter(
            Q(noc=code) | Q(operatorcode__code=code, operatorcode__source=self.source)
        )

    def get_vehicle(self, item):
        code = item["VehicleRef"]
        if code.isdigit():
            fleet_number = code
        else:
            fleet_number = None

        operators = self.get_operators(item)

        defaults = {
            "fleet_number": fleet_number,
            "source": self.source,
            "operator": operators[0] if operators else None,
            "code": code,
        }

        try:
            return self.vehicles.get_or_create(
                defaults, code__iexact=code, operator__in=operators
            )
        except self.vehicles.model.MultipleObjectsReturned:
            return (
                self.vehicles.filter(code__iexact=code, operator__in=operators).first(),
                False,
            )

    def get_service(self, item):
        line_name = item["PublishedLineName"]
        if not line_name:
            return
        services = Service.objects.filter(
            get_line_name_query(line_name),
            current=True,
            operator__in=self.get_operators(item),
        )
        try:
            try:
                return services.get()
            except Service.MultipleObjectsReturned:
                return (
                    services.filter(stops__locality__stoppoint=item["DestinationRef"])
                    .distinct()
                    .get()
                )
        except (Service.DoesNotExist, Service.MultipleObjectsReturned) as e:
            print(
                e,
                item["OperatorRef"],
                item["PublishedLineName"],
                item["DestinationRef"],
            )

    def get_journey(self, item, vehicle):
        code = item["JourneyCode"]
        if dt := item["DepartureTime"]:
            dt = parse_datetime(dt)
        else:
            dt = None

        latest_journey = vehicle.latest_journey
        if (
            latest_journey
            and latest_journey.code == code
            and latest_journey.datetime == dt
        ):
            return latest_journey

        if dt and (
            journey := vehicle.vehiclejourney_set.filter(
                date=timezone.localdate(dt), datetime=dt
            ).first()
        ):
            return journey

        journey = VehicleJourney(
            datetime=dt,
            code=code or "",
            route_name=item["PublishedLineName"] or "",
            service=self.get_service(item),
            destination=item["DestinationStopLocality"]
            or item["DestinationStopName"]
            or "",
            direction=item["DirectionRef"],
        )

        if journey.service_id and not journey.id and dt:
            journey.trip = journey.get_trip(
                departure_time=dt, destination_ref=item["DestinationRef"]
            )
            if journey.trip and not journey.destination:
                journey.destination = journey.trip.headsign or ""

        return journey

    def create_vehicle_location(self, item):
        bearing = item["Bearing"]
        if bearing in {"-1", "0"}:
            bearing = None
        return VehicleLocation(
            latlong=GEOSGeometry(f"POINT({item['Longitude']} {item['Latitude']})"),
            heading=bearing,
        )
