import logging
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import requests
from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.core.cache import cache
from django.db.models import Prefetch, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from haversine import Unit, haversine_vector
from redis.exceptions import ResponseError
from rest_framework import pagination, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from sql_util.utils import Exists

from busstops.models import Locality, Operator, Service, StopPoint
from bustimes.models import StopTime, Trip
from bustimes.utils import contiguous_stoptimes_only
from departures.gtfsr import maybe_get_and_apply_trip_update
from disruptions.models import Call
from tfl import countdown
from tfl import utils as tfl_utils
from tfl.models import Journey
from vehicles.models import (
    Livery,
    Vehicle,
    VehicleJourney,
    VehicleLocation,
    VehicleType,
)
from vehicles.time_aware_polyline import (
    decode_time_aware_polyline,
    encode_time_aware_polyline,
)
from vehicles.utils import calculate_bearing, redis_client
from vehicles.views import get_vehicle_locations

from . import filters, serializers

logger = logging.getLogger(__name__)


class BadException(APIException):
    status_code = 400


class LimitOffsetPagination(pagination.LimitOffsetPagination):
    max_limit = 1000


class CursorPagination(pagination.CursorPagination):
    ordering = "-pk"
    page_size = 100


class CursorPaginationWithSmallerPageSize(CursorPagination):
    page_size = 10


class VehicleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Vehicle.objects.select_related("vehicle_type", "livery", "operator", "garage")
        .annotate(
            special_features=ArrayAgg(
                "features__name", filter=~Q(features=None), order_by="name"
            ),
        )
        .order_by("id")
    )
    serializer_class = serializers.VehicleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.VehicleFilter
    pagination_class = LimitOffsetPagination


class LiveryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Livery.objects.order_by("id")
    serializer_class = serializers.LiverySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.LiveryFilter


class VehicleTypeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = VehicleType.objects.all()
    serializer_class = serializers.VehicleTypeSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.VehicleTypeFilter


class OperatorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Operator.objects.filter(
            Exists("vehicle") | Exists("service", filter=Q(service__current=True))
        )
        .order_by("noc")
        .defer("address", "email", "phone", "search_vector")
    )
    serializer_class = serializers.OperatorSerializer
    pagination_class = CursorPagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.OperatorFilter


class ServiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Service.objects.filter(current=True).prefetch_related("operator")
    serializer_class = serializers.ServiceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.ServiceFilter


class StopViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        StopPoint.objects.order_by("atco_code")
        .select_related("locality")
        .annotate(
            line_names=ArrayAgg(
                "stopusage__line_name",
                filter=Q(stopusage__service__current=True),
                distinct=True,
                default=None,
            )
        )
    )
    serializer_class = serializers.StopSerializer
    pagination_class = CursorPagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.StopFilter


class TripViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Trip.objects.select_related(
            "route__service", "route__source", "operator", "calendar"
        )
        .prefetch_related("notes")
        .annotate(
            destination_name=Coalesce(
                "headsign", "destination__locality__name", "destination__common_name"
            )
        )
    )
    serializer_class = serializers.TripSerializer
    pagination_class = CursorPagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.TripFilter

    @staticmethod
    def get_stops(obj, date=None):
        trips = obj.get_parts(date)
        multiple_trips = len(trips) > 1
        if multiple_trips:
            stops = StopTime.objects.filter(trip__in=trips).order_by(
                "trip__start", "id"
            )
        else:
            stops = trips[0].stoptime_set.order_by("id")
        stops = (
            stops.select_related("stop")
            .defer("stop__search_vector")
            .prefetch_related(
                Prefetch("stop__locality", queryset=Locality.objects.only("name"))
            )
        )
        if obj.notes.all():
            stops = stops.annotate(note_codes=ArrayAgg("notes__code"))
        if multiple_trips:
            stops = contiguous_stoptimes_only(stops, obj.id)
        return stops

    @staticmethod
    def set_call_conditions(stops, date):
        cancelled = set(
            Call.objects.filter(
                stop_time__in=[stop.id for stop in stops if stop.id],
                journey__date=date,
                journey__situation__current=True,
                condition="notStopping",
            ).values_list("stop_time", flat=True)
        )
        for stop in stops:
            if stop.id in cancelled:
                stop.call_condition = "notStopping"

    def get_object(self):
        obj = super().get_object()
        obj.stops = self.get_stops(obj)

        maybe_get_and_apply_trip_update(obj, obj.stops)

        return obj


class VehicleJourneyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = VehicleJourney.objects.select_related("vehicle")
    serializer_class = serializers.VehicleJourneySerializer
    pagination_class = CursorPaginationWithSmallerPageSize
    filter_backends = (DjangoFilterBackend,)
    filterset_class = filters.VehicleJourneyFilter

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "details":
            qs = qs.select_related(
                "service",
                "trip__route__service",
                "trip__route__source",
                "trip__operator",
            )
        return qs

    @staticmethod
    def set_actual_times(stop_times, locations):
        stops = [st for st in stop_times if st.stop and st.stop.latlong]
        if not stops:
            return

        stop_coords = [(st.stop.latlong.y, st.stop.latlong.x) for st in stops]
        vehicle_coords = [
            (loc["coordinates"][1], loc["coordinates"][0]) for loc in locations
        ]
        stop_headings = np.array(
            [
                st.stop.get_heading() if st.stop.get_heading() is not None else np.nan
                for st in stops
            ],
            dtype=float,
        )
        try:
            haversine_vector_results = haversine_vector(
                stop_coords, vehicle_coords, Unit.METERS, comb=True
            )
        except ValueError:
            logger.exception("error calculating vehicle headings")
            return

        for distances, location in zip(haversine_vector_results, locations):
            vehicle_heading = location.get("direction")
            if vehicle_heading is not None:
                heading_diff = np.abs(
                    ((stop_headings - vehicle_heading) + 180) % 360 - 180
                )
                aligned = np.isnan(heading_diff) | (heading_diff < 90)
                if aligned.any():
                    idx = int(np.argmin(np.where(aligned, distances, np.inf)))
                else:
                    idx = int(np.argmin(distances))
            else:
                idx = int(np.argmin(distances))

            if distances[idx] < 100:
                stop = stops[idx]
                if (
                    stop.arrival is not None
                    and stop.departure is not None
                    and stop.arrival != stop.departure
                    and not getattr(stop, "actual_arrival_time", None)
                ):
                    stop.actual_arrival_time = location["datetime"]
                stop.actual_departure_time = location["datetime"]

    @staticmethod
    def trip_from_siri(instance):
        try:
            mvj = instance.vehicle.latest_journey_data["MonitoredVehicleJourney"]
            origin_ref = mvj["OriginRef"].upper()
            dest_ref = mvj["DestinationRef"].upper()
            stops = {
                stop.atco_code.upper(): stop
                for stop in StopPoint.objects.filter(
                    Q(atco_code__iexact=origin_ref) | Q(atco_code__iexact=dest_ref)
                )
            }
        except (KeyError, ValueError, TypeError):
            return

        origin = stops.get(origin_ref) or StopPoint(common_name=mvj.get("OriginName"))
        dest = stops.get(dest_ref) or StopPoint(common_name=mvj.get("DestinationName"))

        if start := mvj.get("OriginAimedDepartureTime"):
            start = timezone.localtime(datetime.fromisoformat(start))
            start = timedelta(hours=start.hour, minutes=start.minute)
        if end := mvj.get("DestinationAimedArrivalTime"):
            end = timezone.localtime(datetime.fromisoformat(end))
            end = timedelta(hours=end.hour, minutes=end.minute)

        trip = Trip(start=start, end=end, operator=instance.vehicle.operator)
        trip.stops = [
            StopTime(stop=origin, departure=start, timing_point=True),
            StopTime(stop=dest, arrival=end, timing_point=True),
        ]
        return trip

    @staticmethod
    def trip_from_tfl(instance, base_version=None):
        journey = Journey.from_vehicle_journey(instance, base_version)
        if not journey:
            return
        stop_times = tfl_utils.get_stop_times(journey)
        if not stop_times:
            return

        # start_time can be after 24:00, relative to the previous day -
        # make it relative to instance.date
        start_time = journey.start_time
        if instance.datetime:
            offset = (
                tfl_utils.get_service_date(journey, instance.datetime) - instance.date
            )
            if offset:
                start_time += offset
                for stop_time in stop_times:
                    stop_time.arrival += offset
                    stop_time.departure += offset

        trip = Trip(start=start_time)
        trip.stops = stop_times
        return trip

    @staticmethod
    def get_tfl_arrivals(reg):
        if (data := countdown.get_vehicle_predictions(reg)) is not None:
            return data

        cache_key = f"TflVehicle:{reg}"

        if (data := cache.get(cache_key)) is None:
            data = []
            try:
                response = requests.get(
                    f"https://api.tfl.gov.uk/Vehicle/{reg}/Arrivals",
                    params=settings.TFL,
                    timeout=8,
                )
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError):
                logger.warning("TfL arrivals error", exc_info=True)
            cache.set(cache_key, data, 60)

        return data

    @staticmethod
    def get_tfl_base_version(instance, data):
        for item in data:
            if item.get("tripId") == instance.code and item.get("baseVersion"):
                return item["baseVersion"]

    @classmethod
    def tfl_arrivals(cls, instance, reg):
        data = [
            item
            # (predictions for the vehicle's next trip too)
            for item in cls.get_tfl_arrivals(reg)
            if item.get("tripId", instance.code) == instance.code
        ]

        stops = instance.trip.stops
        trip_stops = {stop_time.stop_id: stop_time.stop for stop_time in stops}
        other_stops = StopPoint.objects.in_bulk(
            [item["naptanId"] for item in data if item["naptanId"] not in trip_stops]
        )

        matched = {}  # index in stops: expected arrival time
        to_insert = defaultdict(list)  # index in stops: unmatched predictions
        position = 0
        pending = []  # unmatched, to be inserted before the next matched stop
        for item in sorted(data, key=lambda item: item["expectedArrival"]):
            expected_arrival = timezone.localtime(
                datetime.fromisoformat(item["expectedArrival"])
            )
            atco_code = item["naptanId"]
            indices = [
                i
                for i, stop_time in enumerate(stops)
                if stop_time.stop_id == atco_code and i not in matched
            ]
            # predictions can be slightly out of order (by a few seconds) -
            # so match an earlier stop if there's no later one
            index = next(
                (i for i in indices if i >= position),
                indices[-1] if indices else None,
            )
            if index is None:
                stop = (
                    other_stops.get(atco_code)
                    or trip_stops.get(atco_code)
                    or StopPoint(common_name=item["stationName"])
                )
                stop_time = StopTime(stop=stop)
                stop_time.expected_arrival_time = expected_arrival
                pending.append(stop_time)
            else:
                matched[index] = expected_arrival
                to_insert[index] += pending
                position = max(position, index + 1)
                pending = []

        previous = None
        for index in sorted(matched):
            # times in stop order shouldn't go backwards
            if previous and matched[index] < previous:
                matched[index] = previous
            stops[index].expected_arrival_time = previous = matched[index]

        if pending:
            # don't put unmatched predictions before stops scheduled earlier
            midnight = timezone.make_aware(
                datetime.combine(instance.date, datetime.min.time())
            )
            while position < len(stops) and (
                (scheduled := stops[position].departure_or_arrival()) is not None
                and midnight + scheduled < pending[0].expected_arrival_time
            ):
                position += 1
            to_insert[position] += pending

        for index in sorted(to_insert, reverse=True):
            stops[index:index] = to_insert[index]

    @action(detail=True)
    def details(self, request, pk=None, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        extra_data = {}

        tzinfo = (
            instance.trip and instance.trip.route and instance.trip.route.timezone
        ) or None

        locations = []
        if redis_client:
            polyline = None
            try:
                if polyline := redis_client.get(instance.get_redis_key()):
                    polyline = polyline.decode()
                    locations = [
                        {
                            "id": timestamp,
                            "coordinates": [x, y],
                            "datetime": datetime.fromtimestamp(
                                timestamp, tzinfo or timezone.get_current_timezone()
                            ),
                        }
                        for x, y, timestamp in decode_time_aware_polyline(polyline)
                    ]
                    for i, location in enumerate(locations):
                        previous = (
                            locations[i - 1]["coordinates"]
                            if i > 0
                            else location["coordinates"]
                        )
                        following = (
                            locations[i + 1]["coordinates"]
                            if i + 1 < len(locations)
                            else location["coordinates"]
                        )
                        location["direction"] = calculate_bearing(previous, following)
            except ResponseError:
                # old 'list' type
                raw_locations = redis_client.lrange(instance.get_redis_key(), 0, -1)
                locations = [
                    VehicleLocation.decode_appendage(loc, tzinfo)
                    for loc in raw_locations
                ]
                locations.sort(key=lambda loc: loc["datetime"])

            filtered = []
            stationary = False
            previous = None
            previous_coords = None
            for location in locations:
                coords = location["coordinates"]
                if previous_coords:
                    dx = coords[0] - previous_coords[0]
                    dy = coords[1] - previous_coords[1]
                    if dx * dx + dy * dy < 2.5e-7:  # 0.0005 degrees squared
                        stationary = True
                    elif stationary:
                        filtered.append(previous)
                        stationary = False
                if not stationary:
                    filtered.append(location)
                    previous_coords = coords
                previous = location
            if stationary:
                filtered.append(location)
            locations = filtered

            if not polyline:
                polyline = encode_time_aware_polyline(
                    [
                        [
                            loc["coordinates"][0],
                            loc["coordinates"][1],
                            int(loc["datetime"].timestamp()),
                        ]
                        for loc in locations
                    ]
                )

            extra_data["time_aware_polyline"] = polyline

        if instance.service_id:
            extra_data["service"] = {
                "id": instance.service_id,
                "slug": instance.service.slug,
            }

        current_trip = (
            instance.vehicle_id and instance.id == instance.vehicle.latest_journey_id
        )

        is_tfl = instance.vehicle and instance.vehicle.is_tfl()
        if is_tfl:
            mvj = instance.vehicle.latest_journey_data["MonitoredVehicleJourney"]

        if is_tfl and not instance.trip and instance.code.isdigit():
            base_version = None
            if current_trip:
                base_version = self.get_tfl_base_version(
                    instance, self.get_tfl_arrivals(mvj["VehicleRef"])
                )
            instance.trip = self.trip_from_tfl(instance, base_version)

        if current_trip and not instance.trip:
            instance.trip = self.trip_from_siri(instance)

        if instance.trip:
            instance.trip.destination_name = None
            if instance.trip.id:
                instance.trip.stops = list(
                    TripViewSet.get_stops(instance.trip, instance.date)
                )
                TripViewSet.set_call_conditions(instance.trip.stops, instance.date)

                if current_trip or not instance.vehicle_id:
                    maybe_get_and_apply_trip_update(instance.trip, instance.trip.stops)

            if is_tfl and current_trip:
                self.tfl_arrivals(instance, mvj["VehicleRef"])

            if locations:
                self.set_actual_times(instance.trip.stops, locations)
            trip_serializer = serializers.TripSerializer(
                instance.trip,
                context={
                    "date": instance.date,
                    "tzinfo": tzinfo,
                },
            )

            extra_data["trip"] = trip_serializer.data

        if current_trip or not instance.vehicle_id:
            if instance.service_id:
                params = {
                    "service_ids": [instance.service_id],
                }
            else:
                params = {"vehicle_ids": [instance.vehicle_id or instance.id]}
            this = None
            if live := get_vehicle_locations(**params):
                # check that this journey is actually tracking (not an old journey)
                for item in live:
                    if instance.id == item["journey_id"]:
                        this = item
                        extra_data["live"] = live
                        break

            if this and not extra_data.get("time_aware_polyline"):
                # one-item polyline
                extra_data["time_aware_polyline"] = encode_time_aware_polyline(
                    (
                        (
                            this["coordinates"][0],
                            this["coordinates"][1],
                            int(datetime.fromisoformat(this["datetime"]).timestamp()),
                        ),
                    )
                )

        if (
            not (instance.trip and instance.trip.operator)
            and instance.vehicle_id
            and instance.vehicle.operator
        ):
            extra_data["operator"] = {
                "noc": instance.vehicle.operator.noc,
                "slug": instance.vehicle.operator.slug,
                "name": instance.vehicle.operator.name,
            }

        # same journeys as the service or vehicle's journeys list (vehicles/views.py)
        next_previous_filter = None
        if request.GET.get("context") == "service":
            if instance.service_id:
                next_previous_filter = {
                    "date": instance.date,
                    "service_id": instance.service_id,
                }
            elif instance.vehicle_id:
                # ad-hoc service
                next_previous_filter = {
                    "date": instance.date,
                    "service_id": None,
                    "route_name": instance.route_name,
                    "vehicle__operator_id": instance.vehicle.operator_id,
                }
        elif instance.vehicle_id:
            next_previous_filter = {
                "date": instance.date,
                "vehicle_id": instance.vehicle_id,
            }

        if next_previous_filter:
            try:
                next_journey = instance.get_next_by_datetime(**next_previous_filter)
            except VehicleJourney.DoesNotExist:
                pass
            else:
                extra_data["next"] = {
                    "id": next_journey.id,
                    "datetime": timezone.localtime(next_journey.datetime, tzinfo),
                }
            try:
                previous_journey = instance.get_previous_by_datetime(
                    **next_previous_filter
                )
            except VehicleJourney.DoesNotExist:
                pass
            else:
                extra_data["previous"] = {
                    "id": previous_journey.id,
                    "datetime": timezone.localtime(previous_journey.datetime, tzinfo),
                }

        with timezone.override(tzinfo or timezone.get_current_timezone()):
            return Response(serializer.data | extra_data)
