import functools
import io
import logging
import zipfile
from datetime import datetime, timedelta

import requests
import sentry_sdk
from lxml import etree
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.dateparse import parse_duration

from busstops.models import (
    Locality,
    Operator,
    OperatorCode,
    Service,
    ServiceCode,
    StopPoint,
)
from bustimes.models import Route, Trip

from ...models import Vehicle, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand, Status


logger = logging.getLogger(__name__)

_SIRI_NS = "http://www.siri.org.uk/siri"


def _elem_to_dict(elem):
    children = list(elem)
    if not children:
        return elem.text
    d = {}
    for child in children:
        key = child.tag.split("}", 1)[1] if "}" in child.tag else child.tag
        value = _elem_to_dict(child)
        existing = d.get(key)
        if existing is not None:
            if not isinstance(existing, list):
                d[key] = [existing]
            d[key].append(value)
        else:
            d[key] = value
    return d


occupancies = {
    "seatsAvailable": "Seats available",
    "standingAvailable": "Standing available",
    "full": "Full",
}


def get_destination_ref(destination_ref: str) -> str | None:
    destination_ref = destination_ref.removeprefix("NT")  # Nottingham City Transport

    if (
        " " in destination_ref
        or len(destination_ref) < 4
        or not destination_ref[:4].isdigit()
        or destination_ref[:3] == "000"
        or destination_ref[:3] == "999"
        or destination_ref[:3] == "980"
        or destination_ref[:3] == "900"
    ):
        # destination ref is not in the expected ATCO code format - maybe a postcode or other placeholder
        return

    return destination_ref


@functools.cache
def get_destination_name(destination_ref: str) -> str:
    try:
        return Locality.objects.get(stoppoint=destination_ref).name
    except Locality.DoesNotExist:
        if (
            destination_ref.isdigit()
            and destination_ref[0:1] != "0"
            and destination_ref[2:3] == "0"
        ):
            return get_destination_name(f"0{destination_ref}")
    return ""


def get_line_name_query(line_ref: str) -> Q:
    line_name = line_ref.replace("_", " ").strip()
    return (
        Exists(
            ServiceCode.objects.filter(
                service=OuterRef("id"), scheme__endswith="SIRI", code=line_ref
            )
        )
        | Q(line_name__iexact=line_name)
        | Exists(
            Route.objects.filter(service=OuterRef("id"), line_name__iexact=line_name)
        )
    )


class Command(ImportLiveVehiclesCommand):
    source_name = "Bus Open Data"
    vehicle_code_scheme = "BODS"
    services = (
        Service.objects.using(settings.READ_DATABASE)
        .filter(current=True)
        .defer("geometry", "search_vector")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hist = {}

    @staticmethod
    def get_datetime(item):
        return datetime.fromisoformat(item["RecordedAtTime"])

    @functools.cache
    def get_operator(self, operator_ref):
        # all operators with a matching OperatorCode,
        # or (if no such OperatorCode) the one with a matching id
        operator_codes = self.source.operatorcode_set.filter(code=operator_ref)
        return Operator.objects.filter(
            Exists(operator_codes.filter(operator=OuterRef("pk")))
            | Q(noc=operator_ref) & ~Exists(operator_codes)
        )

    def get_vehicle(self, item):
        monitored_vehicle_journey = item["MonitoredVehicleJourney"]
        operator_ref = monitored_vehicle_journey["OperatorRef"]
        vehicle_ref = monitored_vehicle_journey["VehicleRef"] or ""

        vehicle_ref = vehicle_ref.removeprefix(f"{operator_ref}-")

        try:
            vehicle_unique_id = item["Extensions"]["VehicleJourney"]["VehicleUniqueId"]
        except (KeyError, TypeError):
            vehicle_unique_id = None

        if not vehicle_ref and vehicle_unique_id:
            vehicle_ref = vehicle_unique_id

        if (
            not vehicle_ref.isdigit()
            and vehicle_ref.isupper()
            and len(vehicle_ref) > 6
            and not vehicle_ref.startswith("BUS")
        ):
            # assume vehicle ref is globally unique (cos it looks like a vehicle reg?)
            try:
                return self.vehicles.get(code__iexact=vehicle_ref), False
            except (Vehicle.DoesNotExist, Vehicle.MultipleObjectsReturned):
                pass

        # ffs
        if operator_ref == "MARS" and vehicle_unique_id:
            vehicle_ref = vehicle_unique_id

        defaults = {"code": vehicle_ref, "source": self.source}

        operators = self.get_operator(operator_ref)

        if operator_ref == "TFLO":
            defaults["livery_id"] = 262
            defaults["operator_id"] = operator_ref
            if vehicle_ref.startswith("TMP"):
                defaults["notes"] = "Spare ticket machine"
                defaults["locked"] = True
            vehicles = self.vehicles.filter(
                Q(operator__in=operators) | Q(operator=None)
            )
        elif not operators:
            vehicles = self.vehicles.filter(operator=None)
        elif len(operators) == 1:
            operator = operators[0]

            defaults["operator"] = operator
            if operator.group_id:
                condition = Q(operator__group_id=operator.group_id)
                vehicles = self.vehicles.filter(condition)
            else:
                vehicles = self.vehicles.filter(operator=operator)
        else:
            defaults["operator"] = operators[0]
            for operator in operators:
                if operator.noc == operator_ref:
                    # National Express rather than Dublin Express for example
                    defaults["operator"] = operator
            vehicles = self.vehicles.filter(operator__in=operators)

        condition = Q(code__iexact=vehicle_ref)
        if vehicle_ref.isdigit():
            defaults["fleet_number"] = vehicle_ref
            if operators:
                condition |= Q(code__endswith=f"-{vehicle_ref}") | Q(
                    code__startswith=f"{vehicle_ref}_"
                )
        elif "_-_" in vehicle_ref:
            fleet_number, reg = vehicle_ref.split("_-_", 2)
            if fleet_number.isdigit():
                defaults["fleet_number"] = fleet_number
                reg = reg.replace("_", "")
                defaults["reg"] = reg
        if "fleet_number" not in defaults and vehicle_unique_id:
            # VehicleUniqueId
            if len(vehicle_unique_id) < len(vehicle_ref):
                defaults["fleet_code"] = vehicle_unique_id
                if vehicle_unique_id.isdigit():
                    defaults["fleet_number"] = vehicle_unique_id

        vehicles = vehicles.filter(condition)

        try:
            vehicle, created = vehicles.get_or_create(defaults)
        except (Vehicle.MultipleObjectsReturned, IntegrityError) as e:
            print(e, operator_ref, vehicle_ref)
            vehicle = vehicles.first()
            created = False
        else:
            if "fleet_code" in defaults and not vehicle.fleet_code:
                vehicle.fleet_code = defaults["fleet_code"]
                if "fleet_number" in defaults:
                    vehicle.fleet_number = defaults["fleet_number"]
                vehicle.save(update_fields=["fleet_code", "fleet_number"])

        return vehicle, created

    def get_service(self, operators, item, line_ref, vehicle_operator_id):
        monitored_vehicle_journey = item["MonitoredVehicleJourney"]

        if destination_ref := monitored_vehicle_journey.get("DestinationRef"):
            destination_ref = get_destination_ref(destination_ref)

        # filter by LineRef or (if present and different) TicketMachineServiceCode
        line_name_query = get_line_name_query(line_ref)
        try:
            ticket_machine_service_code = item["Extensions"]["VehicleJourney"][
                "Operational"
            ]["TicketMachine"]["TicketMachineServiceCode"]
        except (KeyError, TypeError):
            pass
        else:
            if ticket_machine_service_code.lower() != line_ref.lower():
                line_name_query |= get_line_name_query(ticket_machine_service_code)

        services = self.services.filter(line_name_query).defer("geometry")

        if item["MonitoredVehicleJourney"]["OperatorRef"] == "TFLO":
            return services.filter(route__source__name="L").first()

        if not operators:
            pass
        elif len(operators) == 1 and operators[0].group_id and destination_ref:
            operator = operators[0]

            # first try taking OperatorRef at face value
            # (temporary while some services may have no StopUsages)
            try:
                return services.filter(operator=operator).get()
            except (Service.DoesNotExist, Service.MultipleObjectsReturned):
                pass

            condition = Q(group_id=operator.group_id)

            # in case the vehicle operator has a different group (e.g. HCTY)
            if vehicle_operator_id != operator.noc:
                condition |= Q(noc=vehicle_operator_id)

            services = services.filter(
                Exists(Operator.objects.filter(condition, service=OuterRef("pk")))
            )
            # we don't just use 'operator__parent=' because a service can have multiple operators

            # we will use the DestinationRef later to find out exactly which operator it is,
            # because the OperatorRef field is unreliable,
            # e.g. sometimes has the wrong up First Yorkshire operator code

        elif operators:
            if len(operators) == 1:
                operator = operators[0]
                condition = Q(operator=operator)
                if vehicle_operator_id != operator.noc:
                    condition |= Q(operator=vehicle_operator_id)
                services = services.filter(condition)
            else:
                services = services.filter(
                    Exists(
                        Service.operator.through.objects.filter(
                            operator__in=operators, service=OuterRef("id")
                        )
                    )
                )

            try:
                return services.get()
            except Service.DoesNotExist:
                return
            except Service.MultipleObjectsReturned:
                pass

        if destination_ref:
            # cope with a missing leading zero
            atco_code__startswith = Q(atco_code__startswith=destination_ref[:3])
            if (
                destination_ref.isdigit()
                and destination_ref[0] != "0"
                and destination_ref[3] == "0"
            ):
                atco_code__startswith |= Q(
                    atco_code__startswith=f"0{destination_ref}[:3]"
                )

            stops = StopPoint.objects.filter(
                atco_code__startswith, service=OuterRef("pk")
            )
            services = services.filter(Exists(stops))
            try:
                return services.get()
            except Service.DoesNotExist:
                return
            except Service.MultipleObjectsReturned:
                condition = Exists(
                    StopPoint.objects.filter(
                        service=OuterRef("pk"), atco_code=destination_ref
                    )
                )
                origin_ref = monitored_vehicle_journey.get("OriginRef")
                if origin_ref:
                    condition &= Exists(
                        StopPoint.objects.filter(
                            service=OuterRef("pk"), atco_code=origin_ref
                        )
                    )
                try:
                    return services.get(condition)
                except Service.DoesNotExist:
                    pass
                except Service.MultipleObjectsReturned:
                    services = services.filter(condition)

        else:
            latlong = self.create_vehicle_location(item).latlong
            try:
                return services.get(geometry__bboverlaps=latlong)
            except (Service.DoesNotExist, Service.MultipleObjectsReturned):
                pass

        try:
            # in case there was MultipleObjectsReturned caused by a bogus ServiceCode
            # e.g. both Somerset 21 and 21A have 21A ServiceCode
            return services.get(line_name__iexact=line_ref)
        except (Service.DoesNotExist, Service.MultipleObjectsReturned):
            pass

        try:
            when = self.get_datetime(item)
            trips = Trip.objects.filter(
                **{f"calendar__{when:%a}".lower(): True}, route__service=OuterRef("pk")
            )
            return services.get(Exists(trips))
        except (Service.DoesNotExist, Service.MultipleObjectsReturned):
            pass

    def get_journey(self, item, vehicle):
        monitored_vehicle_journey = item["MonitoredVehicleJourney"]

        journey_ref = monitored_vehicle_journey.get("VehicleJourneyRef")

        if not journey_ref:
            try:
                journey_ref = monitored_vehicle_journey["FramedVehicleJourneyRef"][
                    "DatedVehicleJourneyRef"
                ]
            except (KeyError, ValueError):
                pass

        if journey_ref == "UNKNOWN":
            journey_ref = None

        try:
            ticket_machine = item["Extensions"]["VehicleJourney"]["Operational"][
                "TicketMachine"
            ]
            journey_code = ticket_machine["JourneyCode"]
        except (KeyError, TypeError):
            journey_code = journey_ref
            ticket_machine = None
        else:
            if journey_code == "0000":
                journey_code = journey_ref
            elif not journey_ref:
                journey_ref = journey_code  # what we will use for finding matching trip

        route_name = monitored_vehicle_journey.get(
            "PublishedLineName"
        ) or monitored_vehicle_journey.get("LineRef", "")
        if not route_name and ticket_machine:
            route_name = ticket_machine.get("TicketMachineServiceCode", "")

        if origin_aimed_departure_time := monitored_vehicle_journey.get(
            "OriginAimedDepartureTime"
        ):
            origin_aimed_departure_time = datetime.fromisoformat(
                origin_aimed_departure_time
            )

        journey = None

        journeys = vehicle.vehiclejourney_set

        dt = self.get_datetime(item)

        operator_ref = monitored_vehicle_journey["OperatorRef"]

        # treat the weird Nottingham City Transport data specially
        if operator_ref == "NCTR" and origin_aimed_departure_time is None:
            try:
                origin_aimed_departure_time = timezone.make_aware(
                    datetime.fromisoformat(journey_ref[-30:-11])
                )
            except ValueError:
                pass

        if origin_aimed_departure_time:
            difference = origin_aimed_departure_time - dt
            if difference > timedelta(
                hours=20
            ):  # more than 20 hours in the future? subtract a day
                origin_aimed_departure_time -= timedelta(days=1)

        latest_journey = vehicle.latest_journey
        if latest_journey:
            if origin_aimed_departure_time:
                if latest_journey.datetime == origin_aimed_departure_time:
                    journey = latest_journey
                else:
                    journey = journeys.filter(
                        datetime=origin_aimed_departure_time,
                        date=timezone.localdate(dt),
                    ).first()
            elif journey_ref:
                dt = self.get_datetime(item)
                THREE_HOURS = timedelta(hours=3)
                if (
                    route_name == latest_journey.route_name
                    and journey_ref == latest_journey.code
                ):
                    if dt - latest_journey.datetime < THREE_HOURS:
                        journey = latest_journey
                else:
                    journey = journeys.filter(
                        route_name=route_name,
                        code=journey_ref,
                        date=timezone.localdate(dt),
                        datetime__gt=dt - THREE_HOURS,
                    ).last()

        if not journey:
            journey = VehicleJourney(
                route_name=route_name,
                vehicle=vehicle,
                source=self.source,
                datetime=origin_aimed_departure_time,
            )

        if journey_ref:
            journey.code = journey_ref

        if destination_ref := monitored_vehicle_journey.get("DestinationRef"):
            destination_ref = get_destination_ref(destination_ref)

        if operator_ref == "TFLO":
            journey.destination = monitored_vehicle_journey.get("DestinationName")
        else:
            if destination_ref and (
                destination := get_destination_name(destination_ref)
            ):
                # try getting the stop locality name - usually more descriptive than "Bus_Station"
                journey.destination = destination
            elif destination := monitored_vehicle_journey.get("DestinationName"):
                journey.destination = destination.replace("_", " ")
            else:
                journey.direction = monitored_vehicle_journey.get("DirectionRef", "")

        if not journey.service_id and route_name:
            operators = self.get_operator(operator_ref)
            journey.service = self.get_service(
                operators, item, route_name, vehicle.operator_id
            )

            if not operators and journey.service and journey.service.operator.all():
                # create new OperatorCode
                operator = journey.service.operator.all()[0]
                try:
                    OperatorCode.objects.create(
                        source=self.source, operator=operator, code=operator_ref
                    )
                except IntegrityError:
                    pass

                if not vehicle.operator_id:
                    vehicle.operator = operator
                    try:
                        vehicle.save(update_fields=["operator"])
                    except IntegrityError:
                        pass

            # match trip (timetable) to journey:
            if journey.service and (origin_aimed_departure_time or journey_ref):
                block_ref = monitored_vehicle_journey.get("BlockRef")

                arrival_time = monitored_vehicle_journey.get(
                    "DestinationAimedArrivalTime"
                )
                if arrival_time:
                    arrival_time = datetime.fromisoformat(arrival_time)

                if trip := journey.get_trip(
                    datetime=dt,
                    operator_ref=operator_ref,
                    origin_ref=monitored_vehicle_journey.get("OriginRef"),
                    destination_ref=destination_ref,
                    departure_time=origin_aimed_departure_time,
                    arrival_time=arrival_time,
                    journey_code=journey_code,
                    block_ref=block_ref,
                ):
                    journey.trip = trip

                    if operator_ref == "NATX" and trip.block != block_ref:
                        trip.block = block_ref
                        trip.save(update_fields=["block"])

                    if (
                        not (destination_ref and journey.destination)
                        and trip.destination_id
                    ):
                        journey.destination = (
                            trip.headsign
                            or get_destination_name(trip.destination_id)
                            or journey.destination
                        )

                    if (
                        (vehicle.operator_id is None or vehicle.operator_id == "TFLO")
                        and trip.operator_id
                        or (
                            vehicle.operator_id == "PLYC"
                            and trip.operator_id == "TFCN"
                            or vehicle.operator_id == "TFCN"
                            and trip.operator_id == "PLYC"
                        )
                    ):
                        vehicle.operator_id = trip.operator_id
                        vehicle.save(update_fields=["operator"])

        return journey

    @staticmethod
    def create_vehicle_location(item):
        monitored_vehicle_journey = item["MonitoredVehicleJourney"]
        location = monitored_vehicle_journey["VehicleLocation"]
        latlong = GEOSGeometry(f"POINT({location['Longitude']} {location['Latitude']})")
        bearing = monitored_vehicle_journey.get("Bearing")
        if bearing:
            # Assume '0' means None. There's only a 1/360 chance the bus is actually facing exactly north
            bearing = float(bearing) or None
        delay = monitored_vehicle_journey.get("Delay")
        if delay:
            delay = parse_duration(delay)
        location = VehicleLocation(
            latlong=latlong,
            heading=bearing,
            delay=delay,
            occupancy=occupancies.get(monitored_vehicle_journey.get("Occupancy")),
            block=monitored_vehicle_journey.get("BlockRef"),
        )
        if monitored_vehicle_journey["OperatorRef"] == "TFLO":
            location.tfl_code = monitored_vehicle_journey["VehicleRef"]
        extensions = item.get("Extensions")
        if extensions:
            extensions = extensions.get("VehicleJourney") or extensions.get(
                "VehicleJourneyExtensions"
            )
        if extensions:
            location.occupancy_thresholds = extensions.get("OccupancyThresholds")
            if "SeatedOccupancy" in extensions:
                location.seated_occupancy = int(extensions["SeatedOccupancy"])
            if "SeatedCapacity" in extensions:
                location.seated_capacity = int(extensions["SeatedCapacity"])
            if "WheelchairOccupancy" in extensions:
                location.wheelchair_occupancy = int(extensions["WheelchairOccupancy"])
            if "WheelchairCapacity" in extensions:
                location.wheelchair_capacity = int(extensions["WheelchairCapacity"])
        return location

    def get_items(self):
        response = self.session.get(self.source.url, timeout=61)

        if not response.ok:
            print(response.headers, response.content, response)
            return []

        if response.headers["content-type"] == "application/zip":
            with (
                sentry_sdk.start_span(name="unzip"),
                zipfile.ZipFile(io.BytesIO(response.content)) as archive,
            ):
                namelist = archive.namelist()
                assert len(namelist) == 1
                with archive.open(namelist[0]) as open_file:
                    data = open_file.read()
        else:
            data = response.content

        with sentry_sdk.start_span(name="parse XML"):
            root = etree.fromstring(data)

        ns = _SIRI_NS
        service_delivery = root.find(f"{{{ns}}}ServiceDelivery")

        if service_delivery is None:  # downloaded error page
            return

        previous_time = self.source.datetime

        self.source.datetime = datetime.fromisoformat(
            service_delivery.findtext(f"{{{ns}}}ResponseTimestamp")
        )

        if (
            self.source.datetime
            and previous_time
            and self.source.datetime < previous_time
        ):
            return  # don't return old data

        delivery = service_delivery.find(f"{{{ns}}}VehicleMonitoringDelivery")
        return [_elem_to_dict(a) for a in delivery.findall(f"{{{ns}}}VehicleActivity")]

    @staticmethod
    def get_vehicle_identity(item):
        monitored_vehicle_journey = item["MonitoredVehicleJourney"]
        operator_ref = monitored_vehicle_journey["OperatorRef"]
        vehicle_ref = monitored_vehicle_journey["VehicleRef"]

        try:
            vehicle_unique_id = item["Extensions"]["VehicleJourney"]["VehicleUniqueId"]
        except (KeyError, TypeError):
            pass
        else:
            vehicle_ref = f"{vehicle_ref}:{vehicle_unique_id}"

        return f"{operator_ref}:{vehicle_ref}"

    @staticmethod
    def get_journey_identity(item):
        monitored_vehicle_journey = item["MonitoredVehicleJourney"]
        line_ref = monitored_vehicle_journey.get("LineRef")
        line_name = monitored_vehicle_journey.get("PublishedLineName")

        try:
            journey_ref = monitored_vehicle_journey["FramedVehicleJourneyRef"]
        except (KeyError, ValueError):
            journey_ref = monitored_vehicle_journey.get("VehicleJourneyRef")

        departure = monitored_vehicle_journey.get("OriginAimedDepartureTime")
        direction = monitored_vehicle_journey.get("DirectionRef")
        destination = monitored_vehicle_journey.get("DestinationName")

        return f"{line_ref} {line_name} {journey_ref} {departure} {direction} {destination}"

    @staticmethod
    def get_item_identity(item):
        return item["RecordedAtTime"]

    def update(self):
        with sentry_sdk.start_transaction(name="bod_avl_update"):
            now = timezone.now()

            with sentry_sdk.start_span(name="get changed items"):
                try:
                    (
                        changed_items,
                        changed_journey_items,
                        changed_item_identities,
                        changed_journey_identities,
                        total_items,
                    ) = self.get_changed_items()
                except requests.exceptions.RequestException as e:
                    logger.exception(e)
                    self.session.close()
                    self.session = requests.Session()
                    return 30

            age = int((now - self.source.datetime).total_seconds())
            if age > 0:
                self.hist[now.second % 10] = age
                print(self.hist)
                print(
                    f"{now.second=} {age=}  {total_items=}  {len(changed_items)=}  {len(changed_journey_items)=}"
                )

            with sentry_sdk.start_span(name="handle quick items") as span:
                span.set_data("count", len(changed_items))
                self.handle_items(changed_items, changed_item_identities)
            with sentry_sdk.start_span(name="handle changed journey items") as span:
                span.set_data("count", len(changed_journey_items))
                self.handle_items(changed_journey_items, changed_journey_identities)

            time_taken = (timezone.now() - now).total_seconds()

            # stats for last 50 updates:
            bod_status = cache.get("bod_avl_status", [])
            bod_status.append(
                Status(
                    now,
                    self.source.datetime,
                    now - self.source.datetime,
                    total_items,
                    len(changed_items) + len(changed_journey_items),
                    time_taken,
                )
            )
            bod_status = bod_status[-50:]
            cache.set("bod_avl_status", bod_status, 800)

            print(f"{time_taken=}")

            if time_taken > 11:
                return 0

            # bods updates "every 10 seconds",
            # it's usually worth waiting 0-9 seconds
            # before the next fetch
            # for maximum freshness:

            if age > 1:
                witching_hour = min(self.hist, key=self.hist.get)
                worst_hour = max(self.hist, key=self.hist.get)
                now = timezone.now().second % 10
                wait = witching_hour - now
                if wait <= 0:
                    wait += 10
                diff = worst_hour - witching_hour
                print(f"{witching_hour=} {worst_hour=} {diff=} {now=} {wait=}\n")
                if diff % 10 == 9:
                    return wait

            return max(11 - time_taken, 0)
