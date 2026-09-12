import json
import time
from datetime import datetime
from http import HTTPStatus

import requests
from django.contrib.gis.geos import GEOSGeometry
from django.utils import timezone

from busstops.models import DataSource, Service

from ...models import VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand


class Command(ImportLiveVehiclesCommand):
    def handle_item(self, item, vehicle):
        parts = item.split("|")

        (
            _dunno,
            vehicle_code,
            journey_code,
            route,
            direction,
            latitude,
            longitude,
            timestamp,
            bearing,
            _mode,
        ) = parts

        recorded_at_time = datetime.fromisoformat(timestamp)

        if vehicle:
            created = False
        else:
            fleet_number = int(vehicle_code) if vehicle_code.isdigit() else None
            vehicle, created = self.vehicles.get_or_create(
                {
                    "operator_id": self.operator_id,
                    "fleet_code": str(fleet_number or vehicle_code),
                    "fleet_number": fleet_number,
                },
                source=self.source,
                code=vehicle_code,
            )

        journey = None
        if not created and (
            vehicle.latest_journey
            and vehicle.latest_journey.code == journey_code
            and vehicle.latest_journey.route_name == route
            and vehicle.latest_journey.direction == direction
        ):
            journey = vehicle.latest_journey

        if not journey:
            try:
                service = (
                    Service.objects.filter(
                        current=True,
                        operator=self.operator_id,
                        source=self.timetable_source,
                        line_name__iexact=route,
                        # route__line_name__iexact=route,
                    )
                    # .distinct()
                    .get()
                )
            except (Service.DoesNotExist, Service.MultipleObjectsReturned):
                service = None
            else:
                if not service.tracking:
                    service.tracking = True
                    service.save(update_fields=["tracking"])

            date = timezone.localdate(recorded_at_time)
            journey = vehicle.vehiclejourney_set.filter(
                date=date, code=journey_code
            ).first()

            if not journey:
                journey = VehicleJourney(
                    datetime=recorded_at_time,
                    vehicle=vehicle,
                )
            journey.code = journey_code
            journey.service = service
            if service:
                journey.trip = journey.get_trip(journey_code=journey_code)

            if not journey.date:
                journey.date = date
            journey.route_name = route
            journey.source = self.source
            journey.direction = direction
            journey.save()

        if vehicle.latest_journey != journey:
            vehicle.latest_journey = journey
            vehicle.latest_journey_data = parts
            vehicle.save(update_fields=["latest_journey", "latest_journey_data"])

        if bearing == "-1":
            bearing = None

        location = VehicleLocation(
            GEOSGeometry(f"POINT({longitude} {latitude})"),
            heading=bearing,
        )
        location.id = vehicle.id
        location.datetime = recorded_at_time
        location.journey = journey

        self.to_save.append((location, vehicle))

    def handle_items(self, items):
        vehicle_codes = [item.split("|", 2)[1] for item in items]
        vehicles = self.vehicles.filter(source=self.source, code__in=vehicle_codes)
        vehicles = {vehicle.code: vehicle for vehicle in vehicles}

        for vehicle_code, item in zip(vehicle_codes, items):
            self.handle_item(item, vehicles.get(vehicle_code))
        self.save()

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("source_name", type=str)

    def handle(self, source_name, *args, **options):
        self.source = DataSource.objects.get(name=source_name)
        hub_url = self.source.url

        self.timetable_source = DataSource.objects.get(name="IM")
        self.operator_id = "BVAN"
        self.region_id = "IM"

        session = requests.Session()

        negotiation = session.post(f"{hub_url}/negotiate?negotiateVersion=1").json()

        response = session.post(
            hub_url,
            params={
                "id": negotiation["connectionToken"],
            },
            data=json.dumps({"protocol": "json", "version": 1}) + "\x1e",
        )

        while (
            response := session.get(
                hub_url,
                params={
                    "id": negotiation["connectionToken"],
                    "_": int(time.time() * 1000),
                },
            )
        ).ok or response.status_code == HTTPStatus.GATEWAY_TIMEOUT:
            if response.ok:
                for part in response.content.split(b"\x1e"):
                    if part:
                        part = json.loads(part)
                        if "arguments" in part:
                            self.handle_items(
                                [
                                    location
                                    for argument in part["arguments"]
                                    for location in argument["locations"]
                                ]
                            )
