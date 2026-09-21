import logging
from datetime import UTC, datetime

from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Exists, OuterRef, Q
from django.utils.timezone import localdate

from busstops.models import Operator, Service, StopPoint

from ...models import Vehicle, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand

logger = logging.getLogger(__name__)

# "fn" "fleetNumber": "10452",
# "ut" "updateTime": "1599550016135",
# "oc" "operatingCompany": "SDVN",
# "sn" "serviceNumber": "RED",
# "dn" "direction": "INBOUND",
# "sd" "serviceId": "XDARED0.I",
# "so" "shortOpco": "SCD",
# "sr" "serviceDescription": "Honiton Road Park & Ride - Exeter, Paris Street",
# "cd" "cancelled": "False",
# "vc" "vehicleActivityCancellation": "False",
# "la" "latitude": "50.7314606",
# "lo" "longitude": "-3.7003877",
# "hg" "heading": "66",
# "cg" "calculatedHeading": "",
# "dd" "destinationDisplay": "City Centre Paris S",
# "or" "originStopReference": "1100DEC10843",
# "on" "originStopName": "Honiton Road P&R",
# "nr" "nextStopReference": "1100DEC10085",
# "nn" "nextStopName": "Sidwell Street",
# "fr" "finalStopReference": "1100DEC10468",
# "fs" "finalStopName": "Paris Street",
# "ao" "aimedOriginStopDepartureTime": "",
# "eo" "expectedOriginStopDepartureTime": "1599414000000",
# "an" "aimedNextStopArrivalTime": "1599414720000",
# "en" "expectedNextStopArrivalTime": "1599414756000",
# "ax" "aimedNextStopDepartureTime": "1599414720000",
# "ex" "expectedNextStopDepartureTime": "1599414522000",
# "af" "aimedFinalStopArrivalTime": "1599414780000",
# "ef" "expectedFinalStopArrivalTime": "1599414728000",
# "ku" "kmlUrl": "https://tis-kml-stagecoach.s3.amazonaws.com/kml/0017f465-8178-4bfb-bfaa-43a81386120e.kml",
# "td" "tripId": "7127",
# "pr" "previousStopOnRoute": "1100DEC10843",
# "cs" "currentStopOnRoute": "",
# "ns" "nextStopOnRoute": "",
# "jc" "isJourneyCompletedHeuristic": "False",
# "rg" "rag": "A"


def parse_timestamp(timestamp):
    if timestamp:
        return datetime.fromtimestamp(int(timestamp) / 1000, UTC)


def has_stop(stop):
    return Exists(
        StopPoint.objects.filter(service=OuterRef("pk"), locality__stoppoint=stop)
    )


occupancies = {"R": "busy", "A": "seats available", "G": "many seats available"}


class Command(ImportLiveVehiclesCommand):
    source_name = vehicle_code_scheme = "Stagecoach"

    def do_source(self):
        self.operators = Operator.objects.filter(group__name="Stagecoach").in_bulk()
        return super().do_source()

    @staticmethod
    def get_vehicle_identity(item):
        return f"{item['oc']}:{item['so']}:{item['fn']}"

    @staticmethod
    def get_journey_identity(item):
        return (
            item.get("td"),  # journey code
            item.get("ao") or item.get("eo"),  # aimed or expected origin departure time
            item.get("fs"),  # destination
            item.get("sn"),  # service number
        )

    @staticmethod
    def get_item_identity(item):
        return item["ut"]

    @staticmethod
    def get_datetime(item):
        return parse_timestamp(item["ut"])

    def get_items(self):
        return super().get_items()["services"]

    def get_vehicle(self, item) -> tuple[Vehicle, bool]:
        vehicle_code = item["fn"]

        operator_id = item.get("oc")

        if operator_id in self.operators:
            operator = self.operators[operator_id]
        else:
            operator = None

        vehicle = Vehicle.objects.filter(
            operator=None, code__iexact=vehicle_code
        ).first()
        if vehicle or item.get("hg") == "0":
            return vehicle, False

        return Vehicle.objects.filter(operator__in=self.operators).get_or_create(
            {"operator": operator, "source": self.source, "fleet_code": vehicle_code},
            code=vehicle_code,
        )

    def get_journey(self, item, vehicle):
        if item.get("ao"):  # aimedOriginStopDepartureTime
            departure_time = parse_timestamp(item["ao"])
        else:
            departure_time = None

        if departure_time:
            if (
                vehicle.latest_journey
                and abs(
                    vehicle.latest_journey.datetime - departure_time
                ).total_seconds()
                < 60
            ):
                return vehicle.latest_journey

            if journey := vehicle.vehiclejourney_set.filter(
                date=localdate(departure_time), datetime=departure_time
            ).first():
                return journey
        elif item.get("eo"):  # expectedOriginStopDepartureTime
            departure_time = parse_timestamp(item["eo"])

        journey = VehicleJourney(
            datetime=departure_time,
            destination=item.get("dd", "")
            or item.get("fs", ""),  # destinationDisplay or finalStopName
            route_name=item.get("sn", ""),  # serviceNumber
        )

        if code := item.get("td", ""):  # trip id:
            journey.code = code

        if not journey.service_id and journey.route_name:
            services = Service.objects.filter(current=True, operator__in=self.operators)

            stop = item.get("or") or item.get("pr") or item.get("nr")

            if stop:
                services = services.filter(has_stop(stop))

            if item.get("fr"):
                services = services.filter(has_stop(item["fr"]))

            journey.service = services.filter(
                Q(route__line_name__iexact=journey.route_name)
                | Q(line_name__iexact=journey.route_name)
            ).first()

            if not journey.service:
                logger.info(
                    "%s %s %s",
                    journey.route_name,
                    item.get("or"),
                    vehicle.get_absolute_url(),
                )

        if departure_time and journey.service and not journey.id:
            journey.direction = item["dn"].lower()
            journey.trip = journey.get_trip(
                destination_ref=item.get("fr"),
                origin_ref=item.get("or"),
                departure_time=departure_time,
            )

        return journey

    def create_vehicle_location(self, item):
        return VehicleLocation(
            latlong=GEOSGeometry(f"POINT({item['lo'] or 0} {item['la'] or 0})"),
            heading=item.get("hg"),
            # occupancy=occupancies.get(item.get("rg")),
        )
