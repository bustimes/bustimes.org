from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, Q
from django.forms import DateInput
from django.forms.widgets import NumberInput, TextInput
from django_filters.rest_framework import (
    CharFilter,
    DateFilter,
    DateTimeFilter,
    FilterSet,
    ModelChoiceFilter,
    OrderingFilter,
)

from busstops.models import Operator, Service, StopPoint
from bustimes.models import Trip
from vehicles.models import DataSource, Livery, Vehicle, VehicleType


class VehicleFilter(FilterSet):
    search = CharFilter(method="search_filter", label="Search")
    fleet_code = CharFilter(lookup_expr="iexact")
    reg = CharFilter(lookup_expr="iexact")
    code = CharFilter("vehiclecode__code", label="Code")
    operator = ModelChoiceFilter(queryset=Operator.objects, widget=TextInput)
    vehicle_type = ModelChoiceFilter(queryset=VehicleType.objects, widget=TextInput)
    livery = ModelChoiceFilter(queryset=Livery.objects, widget=TextInput)

    ordering = OrderingFilter(fields=(("id", "id"),))

    def search_filter(self, queryset, name, value):
        return queryset.filter(Q(reg__iexact=value) | Q(fleet_code__iexact=value))

    class Meta:
        model = Vehicle
        fields = ("id", "slug", "withdrawn")


class VehicleJourneyFilter(FilterSet):
    vehicle = ModelChoiceFilter(queryset=Vehicle.objects, widget=NumberInput)
    service = ModelChoiceFilter(queryset=Service.objects, widget=NumberInput)
    trip = ModelChoiceFilter(queryset=Trip.objects, widget=NumberInput)
    source = ModelChoiceFilter(queryset=DataSource.objects, widget=NumberInput)
    datetime = DateTimeFilter()
    date = DateFilter()


class StopFilter(FilterSet):
    service = ModelChoiceFilter(
        queryset=Service.objects, widget=NumberInput, label="Service"
    )

    class Meta:
        model = StopPoint
        fields = {
            "atco_code": ["exact", "iexact"],
            "naptan_code": ["exact", "iexact"],
            "stop_type": ["exact"],
            "modified_at": ["gte"],
            "active": ["exact"],
        }


class ServiceFilter(FilterSet):
    operator = CharFilter()
    search = CharFilter(method="search_filter", label="Search")
    stops = ModelChoiceFilter(queryset=StopPoint.objects, widget=TextInput)

    def search_filter(self, queryset, name, value):
        query = SearchQuery(value, search_type="websearch", config="english")
        rank = SearchRank(F("search_vector"), query)
        query = Q(search_vector=query)
        queryset = queryset.annotate(rank=rank).filter(query).order_by("-rank")
        return queryset

    class Meta:
        model = Service
        fields = {
            "public_use": ["exact"],
            "mode": ["exact", "in"],
            "slug": ["exact", "in"],
            "modified_at": ["gte"],
        }


class OperatorFilter(FilterSet):
    class Meta:
        model = Operator
        fields = {
            "name": ["icontains", "exact"],
            "slug": ["exact"],
            "vehicle_mode": ["exact"],
            "region": ["exact"],
        }


class TripFilter(FilterSet):
    operator = ModelChoiceFilter(queryset=Operator.objects, widget=TextInput)
    service = ModelChoiceFilter(
        queryset=Service.objects, field_name="route__service", widget=NumberInput
    )
    date = DateFilter(
        method="filter_by_date", label="Date", widget=DateInput(attrs={"type": "date"})
    )

    class Meta:
        model = Trip
        fields = ("ticket_machine_code", "vehicle_journey_code", "block")

    def filter_by_date(self, queryset, name, value):
        return queryset.active_on(value)


class LiveryFilter(FilterSet):
    vehicle__operator = CharFilter(label="Operator", distinct=True)

    class Meta:
        model = Livery
        fields = {
            "name": ["icontains"],
            "published": ["exact"],
            "id": ["exact", "in"],
        }


class VehicleTypeFilter(FilterSet):
    vehicle__operator = CharFilter(label="Operator", distinct=True)

    class Meta:
        model = VehicleType
        fields = {
            "id": ["exact", "in"],
            "name": ["icontains"],
        }
