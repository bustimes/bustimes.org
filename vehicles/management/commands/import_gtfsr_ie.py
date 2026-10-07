from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.utils.dateparse import parse_duration
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Service
from bustimes.models import Calendar, Trip

from ...models import Vehicle, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand

occupancies = {
    0: "Empty",
    1: "Many seats available",
    2: "Few seats available",
    3: "Standing room only",
    4: "Crushed standing room only",
    5: "Full",
    6: "Not accepting passengers",
    7: "No data available",
    8: "Not boardable",
}


class Command(ImportLiveVehiclesCommand):
    source_name = "Realtime Transport Operators"
    vehicle_code_scheme = "NTA"
    trip_id_field = "ticket_machine_code"
    trip_select_related = ("route__service",)

    def do_source(self):
        self.tzinfo = ZoneInfo("Europe/Dublin")
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.url = "https://api.nationaltransport.ie/gtfsr/v2/Vehicles"
        assert settings.NTA_API_KEY
        self.session.headers.update({"x-api-key": settings.NTA_API_KEY})
        return self

    @staticmethod
    def get_datetime(item):
        return datetime.fromtimestamp(item.vehicle.timestamp, UTC)

    @staticmethod
    def get_vehicle_identity(item):
        return item.vehicle.vehicle.id

    @staticmethod
    def get_journey_identity(item):
        return (
            item.vehicle.trip.route_id,
            item.vehicle.trip.trip_id,
            item.vehicle.trip.start_date,
        )

    @staticmethod
    def get_item_identity(item):
        return item.vehicle.timestamp

    def get_response(self):
        response = self.session.get(self.url, timeout=10)
        response.raise_for_status()

        if etag := response.headers.get("etag"):
            self.session.headers.update({"if-none-match": etag})

        return response

    def get_feed(self):
        response = self.get_response()

        if response.status_code == 304:  # not modified
            return None

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        self.source.datetime = datetime.fromtimestamp(feed.header.timestamp, UTC)

        return feed

    def get_items(self):
        if feed := self.get_feed():
            return feed.entity

    def get_changed_items(self):
        (
            changed_items,
            changed_journey_items,
            changed_item_identities,
            changed_journey_identities,
            total_items,
        ) = super().get_changed_items()

        # prefetch trips
        trips = {item.vehicle.trip.trip_id for item in changed_journey_items}
        trips = Trip.objects.filter(
            **{f"{self.trip_id_field}__in": trips},
            route__source=self.source,
            route__service__current=True,
        ).select_related(*self.trip_select_related)
        self.trips = {getattr(trip, self.trip_id_field): trip for trip in trips}

        return (
            changed_items,
            changed_journey_items,
            changed_item_identities,
            changed_journey_identities,
            total_items,
        )

    def get_vehicle(self, item):
        vehicle_code = item.vehicle.vehicle.id
        return Vehicle.objects.get_or_create(code=vehicle_code, source=self.source)

    def get_journey(self, item, vehicle):
        # GTFS spec for working out datetimes:
        start_date = datetime.strptime(  # noqa: DTZ007 - tzinfo applied below
            f"{item.vehicle.trip.start_date} 12:00:00",
            "%Y%m%d %H:%M:%S",
        )
        start_time = parse_duration(item.vehicle.trip.start_time)
        start_date_time = (start_date + start_time - timedelta(hours=12)).replace(
            tzinfo=self.tzinfo
        )

        # assert not (datetime.fromtimestamp(item.vehicle.timestamp) - start_date_time > timedelta(hours=12))

        journey = VehicleJourney(code=item.vehicle.trip.trip_id)

        if (
            latest_journey := vehicle.latest_journey
        ) and latest_journey.code == journey.code:
            return latest_journey

        journey.datetime = start_date_time

        if journey.code in self.trips:
            trip = self.trips[journey.code]
            service = trip.route.service
            # simple. not sure we need the other shite any more
        else:
            service = None
            services = Service.objects.filter(
                current=True,
                route__source=self.source,
                route__code=item.vehicle.trip.route_id,
            ).distinct()
            if not services:
                if "_" in item.vehicle.trip.route_id:
                    suffix = item.vehicle.trip.route_id.split("_", 1)[1]
                else:
                    suffix = item.vehicle.trip.route_id
                services = Service.objects.filter(
                    current=True,
                    route__source=self.source,
                    route__code__endswith=f"_{suffix}",
                ).distinct()

            if services:
                service = services[0]

            trips = Trip.objects.filter(ticket_machine_code=journey.code)
            if service:
                trips = trips.filter(route__service=service)

            trip = None

            if service and not trips:
                trips = Trip.objects.filter(
                    route__service=service,
                    route__source=self.source,
                    start=start_time,
                    inbound=item.vehicle.trip.direction_id == 1,
                )

            if trips:
                if len(trips) > 1:
                    calendar_ids = [trip.calendar_id for trip in trips]
                    trips = trips.filter(
                        calendar__in=Calendar.objects.active_on(
                            start_date, calendar_ids
                        )
                    )
                    trip = trips.first()
                else:
                    trip = trips[0]

        if service:
            journey.service = service

        if trip:
            if not journey.service:
                journey.service = trip.route.service
            journey.trip = trip

            journey.destination = trip.headsign or ""
            if trip.operator_id and not vehicle.operator_id:
                vehicle.operator_id = trip.operator_id
                vehicle.save(update_fields=["operator"])

        if journey.service:
            journey.route_name = journey.service.line_name

        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey

    def create_vehicle_location(self, item):
        return VehicleLocation(
            heading=item.vehicle.position.bearing or None,
            latlong=GEOSGeometry(
                f"POINT({item.vehicle.position.longitude} {item.vehicle.position.latitude})"
            ),
            occupancy=occupancies.get(item.vehicle.occupancy_status)
            if item.vehicle.HasField("occupancy_status")
            else None,
        )
