from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Q

from busstops.models import Operator
from bustimes.models import Calendar, Route, StopTime

from ...models import VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand


def hyphenate(reg: str) -> str:
    i = 0
    while i < len(reg) and reg[i].isdigit():
        i += 1
    j = i
    while j < len(reg) and reg[j].isalpha():
        j += 1
    return f"{reg[:i]}-{reg[i:j]}-{reg[j:]}"


def get_latlong(item):
    return GEOSGeometry(f"POINT({item[2]} {item[1]})")


class Command(ImportLiveVehiclesCommand):
    source_name = vehicle_code_scheme = "Swords Express"
    operator = "Swords Express"
    url = "https://www.swordsexpress.com/app/themes/swordsexpress/resources/assets/scripts/latlong.php"

    def do_source(self):
        self.operator = Operator.objects.get(name=self.source_name)
        self.tzinfo = ZoneInfo("Europe/Dublin")
        super().do_source()

    def get_datetime(self, item: list):
        if len(item) >= 3:
            return datetime.fromisoformat(item[3]).replace(tzinfo=self.tzinfo)

    @staticmethod
    def get_vehicle_identity(item: list):
        return item[0]

    @staticmethod
    def get_journey_identity(item):
        # changes when vehicle stops/starts tracking
        return len(item)

    @staticmethod
    def get_item_identity(item):
        return item

    def get_vehicle(self, item):
        defaults = {
            "reg": hyphenate(item[0]),
            "source": self.source,
        }
        return self.vehicles.get_or_create(
            defaults, operator=self.operator, code=item[0]
        )

    def get_journey(self, item, _):
        if len(item) < 3:
            return

        journey = VehicleJourney()

        when = self.get_datetime(item)
        date = when.date()

        routes = Route.objects.filter(
            service__operator=self.operator, service__current=True
        ).active_on(date)
        ten_minutes = timedelta(minutes=10)
        now = timedelta(hours=when.hour, minutes=when.minute)
        time_range = (now - ten_minutes, now + ten_minutes)

        stop_times = StopTime.objects.filter(
            Q(departure__range=time_range) | Q(arrival__range=time_range),
            trip__route__in=routes,
            trip__calendar__in=Calendar.objects.active_on(date),
            stop__latlong__dwithin=(get_latlong(item), 0.01),
        ).select_related("trip__route__service")

        if stop_times and all(
            stop_times[0].trip_id == stop_time.trip_id for stop_time in stop_times[1:]
        ):
            journey.trip = stop_times[0].trip
            journey.service = journey.trip.route.service
            journey.route_name = journey.trip.route.line_name
            journey.destination = journey.trip.headsign
        return journey

    def create_vehicle_location(self, item):
        if len(item) >= 3:
            return VehicleLocation(latlong=get_latlong(item))
