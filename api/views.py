import logging
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import pagination, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

import numpy as np

from vehicles.time_aware_polyline import encode_time_aware_polyline

from busstops.models import Operator, Service, StopPoint
from bustimes.models import StopTime, Trip
from bustimes.utils import contiguous_stoptimes_only
from vehicles.models import (
    Livery,
    Vehicle,
    VehicleJourney,
    VehicleLocation,
    VehicleType,
)
from vehicles.utils import redis_client
from vehicles.views import get_vehicle_locations

from sql_util.utils import Exists
from haversine import Unit, haversine_vector

from . import filters, serializers


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
            special_features=ArrayAgg("features__name", filter=~Q(features=None)),
        )
        .order_by("id")
    )
    serializer_class = serializers.VehicleSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.VehicleFilter
    pagination_class = LimitOffsetPagination


class LiveryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Livery.objects.order_by("id")
    serializer_class = serializers.LiverySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.LiveryFilter


class VehicleTypeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = VehicleType.objects.all()
    serializer_class = serializers.VehicleTypeSerializer
    filter_backends = [DjangoFilterBackend]
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
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.OperatorFilter


class ServiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Service.objects.filter(current=True).prefetch_related("operator")
    serializer_class = serializers.ServiceSerializer
    filter_backends = [DjangoFilterBackend]
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
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.StopFilter


class TripViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Trip.objects.select_related("route__service", "operator")
        .prefetch_related("notes")
        .annotate(
            destination_name=Coalesce(
                "headsign", "destination__locality__name", "destination__common_name"
            )
        )
    )
    serializer_class = serializers.TripSerializer
    pagination_class = CursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.TripFilter

    @staticmethod
    def get_stops(obj):
        trips = obj.get_trips()
        stops = (
            StopTime.objects.filter(trip__in=trips)
            .select_related("stop__locality")
            .defer(
                "stop__search_vector",
                "stop__locality__search_vector",
                "stop__locality__latlong",
            )
            .order_by("trip__start", "id")
            # .annotate(
            #     call_condition=Subquery(
            #         Call.objects.filter(
            #             stop_time=OuterRef("id"),
            #             journey__trip=OuterRef("trip"),
            #             journey__situation__current=True,
            #         ).values("condition")[:1]
            #     )
            # )
        )
        if obj.notes.all():
            stops = stops.annotate(note_codes=ArrayAgg("notes__code"))
        if len(trips) > 1:
            stops = contiguous_stoptimes_only(stops, obj.id)
        return stops

    def get_object(self):
        obj = super().get_object()
        obj.stops = self.get_stops(obj)
        return obj


class VehicleJourneyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = VehicleJourney.objects.select_related("vehicle")
    serializer_class = serializers.VehicleJourneySerializer
    pagination_class = CursorPaginationWithSmallerPageSize
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.VehicleJourneyFilter

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "details":
            qs = qs.select_related("service", "trip__route__service", "trip__operator")
        return qs

    @staticmethod
    def set_actual_departure_times(stop_times, locations):
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
        except ValueError as e:
            logging.exception(e)
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
                stops[idx].actual_departure_time = location["datetime"]

    @action(detail=True)
    def details(self, request, pk=None):
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        extra_data = {}

        locations = []
        if redis_client:
            raw_locations = redis_client.lrange(instance.get_redis_key(), 0, -1)
            locations = [VehicleLocation.decode_appendage(loc) for loc in raw_locations]
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

        if instance.trip:
            instance.trip.destination_name = None
            instance.trip.stops = list(TripViewSet.get_stops(instance.trip))
            if locations:
                self.set_actual_departure_times(instance.trip.stops, locations)
            trip_serializer = serializers.TripSerializer(
                instance.trip, context={"include_track": False}
            )
            extra_data["trip"] = trip_serializer.data

        if instance.service_id:
            extra_data["service"] = {
                "id": instance.service_id,
                "slug": instance.service.slug,
            }
            if (
                locations
                and instance.vehicle_id
                and instance.id == instance.vehicle.latest_journey_id
            ):
                extra_data["live"] = get_vehicle_locations(
                    vehicle_ids=[instance.vehicle_id],
                    stop_times=(instance.trip.stops if instance.trip else None),
                )

        if not instance.trip and instance.vehicle.operator:
            extra_data["operator"] = {
                "noc": instance.vehicle.operator.noc,
                "slug": instance.vehicle.operator.slug,
                "name": instance.vehicle.operator.name,
            }

        next_previous_filter = {
            "date": instance.date,
            "vehicle_id": instance.vehicle_id,
        }
        try:
            next_journey = instance.get_next_by_datetime(**next_previous_filter)
        except VehicleJourney.DoesNotExist:
            pass
        else:
            extra_data["next"] = {
                "id": next_journey.id,
                "datetime": timezone.localtime(next_journey.datetime),
            }
        try:
            previous_journey = instance.get_previous_by_datetime(**next_previous_filter)
        except VehicleJourney.DoesNotExist:
            pass
        else:
            extra_data["previous"] = {
                "id": previous_journey.id,
                "datetime": timezone.localtime(previous_journey.datetime),
            }

        return Response(serializer.data | extra_data)
