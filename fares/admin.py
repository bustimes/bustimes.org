from django.contrib import admin
from django.db.models import Value
from django.db.models.aggregates import StringAgg

from buses.admin_utils import M2MThroughMixin

from .models import (
    DataSet,
    DistanceMatrixElement,
    FareTable,
    FareZone,
    Price,
    Tariff,
    UserProfile,
)


@admin.register(DataSet)
class DataSetAdmin(M2MThroughMixin, admin.ModelAdmin):
    list_display = ("__str__", "description", "noc", "datetime")
    list_filter = ("published",)
    autocomplete_fields = ("operators",)
    search_fields = ("name", "description")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                noc=StringAgg("operators", Value(", "), distinct=True)
            )
        return queryset

    def noc(self, obj):
        return obj.noc


@admin.register(Tariff)
class TariffAdmin(M2MThroughMixin, admin.ModelAdmin):
    autocomplete_fields = ("operators", "services")
    list_filter = (("operators", admin.RelatedOnlyFieldListFilter),)
    raw_id_fields = ("source", "user_profile", "access_zones")


@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    raw_id_fields = (
        "time_interval",
        "user_profile",
        "sales_offer_package",
        "tariff",
    )
    list_display = ("amount",)


@admin.register(FareTable)
class FareTableAdmin(admin.ModelAdmin):
    list_display = ("__str__", "description")
    list_filter = ("tariff__source",)
    raw_id_fields = ("user_profile", "sales_offer_package", "tariff")


@admin.register(DistanceMatrixElement)
class DistanceMatrixElementAdmin(admin.ModelAdmin):
    raw_id_fields = ("price", "start_zone", "end_zone", "tariff")


@admin.register(FareZone)
class FareZoneAdmin(admin.ModelAdmin):
    autocomplete_fields = ("stops",)


admin.site.register(UserProfile)
