from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import transaction
from django.db.models import CharField, Exists, F, OuterRef, Q, Value
from django.db.models.aggregates import StringAgg
from django.db.models.functions import Cast, Now, TruncDate
from django.forms import ModelForm, Textarea
from django.urls import reverse
from django.utils.html import format_html
from sql_util.utils import SubqueryCount

from bustimes.admin import log_change
from bustimes.models import Route, RouteLink

from . import models


@admin.register(models.AdminArea)
class AdminAreaAdmin(admin.ModelAdmin):
    list_display = ("name", "id", "atco_code", "region_id")
    list_filter = ("region_id",)
    search_fields = ("atco_code",)


class StopCodeInline(admin.TabularInline):
    model = models.StopCode
    raw_id_fields = ("source",)


@admin.register(models.StopPoint)
class StopPointAdmin(GISModelAdmin):
    list_display = (
        "atco_code",
        "naptan_code",
        "locality",
        "admin_area",
        "common_name",
        "modified_at",
        "created_at",
    )
    list_select_related = ("locality", "admin_area")
    list_filter = (
        ("source", admin.RelatedOnlyFieldListFilter),
        "modified_at",
        "created_at",
        "active",
        "stop_type",
        "service__region",
        "admin_area",
    )
    raw_id_fields = (
        "source",
        "parents",
        "stop_area",
        "locality",
        "admin_area",
    )
    search_fields = ("atco_code",)
    ordering = ("atco_code",)
    inlines = (StopCodeInline,)
    show_full_result_count = False
    readonly_fields = ("search_vector",)

    def get_search_results(self, request, queryset, search_term):
        if not search_term:
            return super().get_search_results(request, queryset, search_term)

        query = SearchQuery(search_term, search_type="websearch", config="english")
        rank = SearchRank(F("locality__search_vector"), query)
        query = Q(locality__search_vector=query)
        if " " not in search_term:
            query |= Q(atco_code=search_term)
        queryset = queryset.annotate(rank=rank).filter(query).order_by("-rank")
        return queryset, False


@admin.register(models.StopCode)
class StopCodeAdmin(admin.ModelAdmin):
    list_display = ("stop", "code", "source")
    raw_id_fields = ("stop",)


@admin.register(models.StopArea)
class StopAreaAdmin(GISModelAdmin):
    raw_id_fields = ("admin_area", "parent")
    list_filter = ("stop_area_type",)


class OperatorCodeInline(admin.TabularInline):
    model = models.OperatorCode
    raw_id_fields = ("source",)


class OperatorAdminForm(ModelForm):
    class Meta:
        widgets = {
            "address": Textarea,
            "twitter": Textarea,
        }


class DuplicateOperatorFilter(admin.SimpleListFilter):
    title = "duplicate"
    parameter_name = "duplicate"

    def lookups(self, request, model_admin):
        return ((1, "Yes"),)

    def queryset(self, request, queryset):
        if self.value():
            exists = Exists(
                models.Operator.objects.filter(
                    ~Q(pk=OuterRef("pk")),
                    name=OuterRef("name"),
                )
            )
            queryset = queryset.filter(exists)

        return queryset


@admin.register(models.Operator)
class OperatorAdmin(admin.ModelAdmin):
    form = OperatorAdminForm
    list_display = (
        "name",
        "slug",
        "operator_codes",
        "noc",
        "vehicle_mode",
        "region_id",
        "services",
        "vehicles",
    )
    list_filter = (
        "modified_at",
        DuplicateOperatorFilter,
        "region",
        "vehicle_mode",
        "payment_methods",
        "group",
    )
    search_fields = ("noc", "name")
    raw_id_fields = ("region", "regions", "siblings", "colour", "source")
    inlines = (OperatorCodeInline,)
    readonly_fields = ("search_vector", "modified_at")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("licences", "payment_methods")

    def get_exclude(self, request, obj=None):
        if obj:
            return [
                field
                for field in ("twitter", "address", "email", "phone", "qualifier_name")
                if not getattr(obj, field)
            ]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                services=SubqueryCount("service", filter=Q(service__current=True)),
                vehicles=SubqueryCount("vehicle"),
            ).prefetch_related("operatorcode_set")
        return queryset

    @admin.display(ordering="services")
    def services(self, obj):
        url = reverse("admin:busstops_service_changelist")
        return format_html(
            '<a href="{}?operator__noc__exact={}">{}</a>', url, obj.noc, obj.services
        )

    @admin.display(ordering="vehicles")
    def vehicles(self, obj):
        url = reverse("admin:vehicles_vehicle_changelist")
        return format_html(
            '<a href="{}?operator__noc__exact={}">{}</a>', url, obj.noc, obj.vehicles
        )

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(
            request, queryset, search_term
        )

        if request.path.endswith("/autocomplete/"):
            queryset = queryset.filter(
                Q(noc=search_term)
                | Exists(
                    models.Service.objects.filter(operator=OuterRef("pk"), current=True)
                )
            )

        return queryset, use_distinct

    @staticmethod
    def payment(obj):
        return ", ".join(str(code) for code in obj.payment_methods.all())

    @staticmethod
    def operator_codes(obj):
        return ", ".join(str(code) for code in obj.operatorcode_set.all())


class ServiceCodeInline(admin.TabularInline):
    model = models.ServiceCode


class RouteInline(admin.TabularInline):
    model = Route
    show_change_link = True
    fields = ("source", "code", "service_code")
    raw_id_fields = ("source",)


class FromServiceLinkInline(admin.TabularInline):
    model = models.ServiceLink
    fk_name = "from_service"
    autocomplete_fields = ("to_service",)


class ToServiceLinkInline(FromServiceLinkInline):
    fk_name = "to_service"
    autocomplete_fields = ("from_service",)


class SplitServiceFilter(DuplicateOperatorFilter):
    title = "split"
    parameter_name = "split"

    def queryset(self, request, queryset):
        if self.value():
            exists = Exists(
                Route.objects.filter(
                    Exists(
                        Route.objects.filter(
                            ~Q(service=OuterRef("service")),
                            source=OuterRef("source"),
                            service_code=OuterRef("service_code"),
                        )
                    ),
                    ~Q(service_code=""),
                    service=OuterRef("id"),
                )
            )
            queryset = queryset.filter(exists)

        return queryset


class DuplicateLineNameFilter(DuplicateOperatorFilter):
    title = "duplicate line name"
    parameter_name = "duplicate_line_name"

    def queryset(self, request, queryset):
        if self.value():
            exists = Exists(
                models.Service.objects.filter(
                    ~Q(id=OuterRef("id")),
                    line_name__iexact=OuterRef("line_name"),
                    source=OuterRef("source"),
                    # current=True,
                )
            )
            queryset = queryset.filter(exists)

        return queryset


class HasCurrentRouteFilter(admin.SimpleListFilter):
    title = "has current route"
    parameter_name = "has_current_route"

    def lookups(self, request, model_admin):
        return (
            ("1", "Yes"),
            ("0", "No"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            exists = Exists(
                Route.objects.filter(
                    Q(end_date=None) | Q(end_date__gte=TruncDate(Now())),
                    service=OuterRef("id"),
                )
            )
            if value == "1":
                queryset = queryset.filter(exists)
            elif value == "0":
                queryset = queryset.filter(~exists)

        return queryset


@admin.register(models.Service)
class ServiceAdmin(GISModelAdmin):
    list_display = (
        "id",
        "line_name",
        "description",
        "service_codes",
        "mode",
        "region_id",
        "routes",
        "current",
        "timetable_wrong",
        "public_use",
        "colour",
        "line_brand",
        "modified_at",
    )
    list_filter = (
        SplitServiceFilter,
        DuplicateLineNameFilter,
        HasCurrentRouteFilter,
        "current",
        "timetable_wrong",
        "public_use",
        "tracking",
        "mode",
        "region",
        ("source", admin.RelatedOnlyFieldListFilter),
        ("operator", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = ("service_code", "line_name", "line_brand", "description")
    raw_id_fields = ("operator", "stops", "colour", "source")
    inlines = (
        ServiceCodeInline,
        RouteInline,
        FromServiceLinkInline,
        ToServiceLinkInline,
    )
    readonly_fields = ("search_vector", "modified_at")
    list_editable = ("colour", "line_brand")
    list_select_related = ("colour",)
    actions = ("current_false", "public_use_true", "merge", "unmerge")

    @admin.display(ordering="routes")
    def routes(self, obj):
        return obj.routes

    @admin.display(ordering="service_codes")
    def service_codes(self, obj):
        return obj.service_codes

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(routes=SubqueryCount("route"))

            queryset = queryset.annotate(
                service_codes=StringAgg("route__service_code", Value(" "))
            )

        return queryset

    def get_search_results(self, request, queryset, search_term):
        if search_term and request.path.endswith("/autocomplete/"):
            queryset = queryset.filter(current=True)

            query = SearchQuery(search_term, search_type="websearch", config="english")
            rank = SearchRank(F("search_vector"), query)
            queryset = (
                queryset.annotate(rank=rank)
                .filter(Q(search_vector=query) | Q(service_code=search_term))
                .order_by("-rank")
            )
            return queryset, False

        return super().get_search_results(request, queryset, search_term)

    def current_false(self, request, queryset):
        result = queryset.order_by().update(current=False, modified_at=Now())
        log_change(request, queryset, ["current"])
        self.message_user(request, f"{result}")

    def public_use_true(self, request, queryset):
        result = queryset.order_by().update(public_use=True, modified_at=Now())
        log_change(request, queryset, ["public_use"])
        self.message_user(request, f"{result}")

    @transaction.atomic
    def merge(self, request, queryset):
        first = queryset[0]
        others = queryset[1:]

        first.current = True

        for other in others:
            if other.current:
                other.route_set.update(service=first)
                first.operator.add(*other.operator.all())
            else:
                # route.service is no longer ON DELETE CASCADE
                other.route_set.all().delete()
            other.vehiclejourney_set.update(service=first)
            other.servicecode_set.filter(
                ~Exists(
                    models.ServiceCode.objects.filter(
                        scheme=OuterRef("scheme"), code=OuterRef("code"), service=first
                    )
                )
            ).update(service=first)

            other.routelink_set.filter(
                ~Exists(
                    RouteLink.objects.filter(
                        from_stop=OuterRef("from_stop"),
                        to_stop=OuterRef("to_stop"),
                        service=first,
                    )
                )
            ).update(service=first)

            if not first.servicecode_set.filter(
                code=other.slug, scheme="slug"
            ).exists():
                models.ServiceCode.objects.create(
                    code=other.slug, service=first, scheme="slug"
                )

            if (
                other.service_code
                and other.service_code != first.service_code
                and not first.servicecode_set.filter(
                    code=other.service_code, scheme="ServiceCode"
                ).exists()
            ):
                models.ServiceCode.objects.create(
                    code=other.service_code, service=first, scheme="ServiceCode"
                )

            other.delete()

        first.do_stop_usages()
        first.update_geometry()
        first.save(force_update=True)

        first.update_description()
        first.update_search_vector()

        self.message_user(request, f"merged {others} into {first}")

    def unmerge(self, request, queryset):
        for service in queryset:
            with transaction.atomic():
                services_by_line_name = {service.line_name: service.id}
                service_id = service.id  # for use later
                operators = service.operator.all()
                routes = service.route_set.all()
                journeys = service.vehiclejourney_set.all()
                bool(journeys)  # force evaluation
                service_codes = service.servicecode_set.all()
                bool(service_codes)  # force evaluation
                for route in routes:
                    if route.line_name not in services_by_line_name:
                        service.id = None
                        service.line_name = route.line_name
                        service.description = route.description
                        service.search_vector = None
                        service.slug = ""
                        service.save(force_insert=True)
                        service.operator.set(operators)
                        services_by_line_name[route.line_name] = service.id
                    route.service_id = services_by_line_name[route.line_name]
                    route.save(update_fields=["service_id"])

                for service in models.Service.objects.filter(
                    id__in=services_by_line_name.values()
                ):
                    service.do_stop_usages()
                    service.update_geometry()
                    service.update_search_vector()
                    if service.id != service_id:
                        journeys.filter(
                            Q(trip__route__service=service)
                            | Q(route_name__iexact=service.line_name)
                        ).update(service=service)
                        service_codes.filter(
                            code__istartswith=f"{service.line_name}-"
                        ).update(service=service)


@admin.register(models.ServiceLink)
class ServiceLinkAdmin(admin.ModelAdmin):
    save_as = True
    list_display = (
        "from_service",
        "from_service__current",
        "to_service",
        "to_service__current",
        "how",
    )
    list_filter = (
        "from_service__current",
        "to_service__current",
        "from_service__source",
        "to_service__source",
    )
    autocomplete_fields = ("from_service", "to_service")

    @staticmethod
    def from_service__current(obj):
        return obj.from_service.current

    @staticmethod
    def to_service__current(obj):
        return obj.to_service.current


@admin.register(models.Locality)
class LocalityAdmin(GISModelAdmin):
    list_display = ("id", "name", "slug", "modified_at", "created_at")
    search_fields = ("id", "name")
    raw_id_fields = ("adjacent", "parent")
    list_filter = ("modified_at", "created_at", "admin_area__region", "admin_area")
    readonly_fields = ("search_vector",)


@admin.register(models.OperatorCode)
class OperatorCodeAdmin(admin.ModelAdmin):
    save_as = True
    list_display = ("id", "operator", "source", "code")
    list_filter = (("source", admin.RelatedOnlyFieldListFilter),)
    search_fields = ("code",)
    raw_id_fields = ("operator",)


@admin.register(models.ServiceCode)
class ServiceCodeAdmin(admin.ModelAdmin):
    list_display = ("id", "service", "scheme", "code")
    list_filter = (
        "scheme",
        "service__current",
        ("service__operator", admin.RelatedOnlyFieldListFilter),
        "service__stops__admin_area",
    )
    search_fields = ("code", "service__line_name", "service__description")
    autocomplete_fields = ("service",)


@admin.register(models.ServiceColour)
class ServiceColourAdmin(admin.ModelAdmin):
    list_display = ("preview", "foreground", "background", "services")
    search_fields = ("name",)
    list_filter = (("service__operator", admin.EmptyFieldListFilter),)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                services=SubqueryCount("service", filter=Q(current=True))
            )
        return queryset

    @admin.display(ordering="services")
    def services(self, obj):
        return obj.services


@admin.register(models.DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    search_fields = ("name", "url")
    list_display = (
        "name",
        "description",
        "url",
        "sha1",
        "datetime",
        "settings",
        "routes",
        "services",
        "source",
        # "journeys",
    )
    list_filter = (
        ("route", admin.EmptyFieldListFilter),
        ("service", admin.EmptyFieldListFilter),
        ("vehiclejourney", admin.EmptyFieldListFilter),
    )
    actions = ("delete_routes", "remove_datetimes")
    show_full_result_count = False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                routes=SubqueryCount("route", filter=~Q(service=None)),
                services=SubqueryCount("service", filter=Q(current=True)),
                # journeys=Exists(VehicleJourney.objects.filter(source=OuterRef("id"))),
            ).prefetch_related("operatorcode_set")
        return queryset

    @admin.display(ordering="routes")
    def routes(self, obj):
        url = reverse("admin:bustimes_route_changelist")
        return format_html(
            '<a href="{}?source__id__exact={}">{}</a>', url, obj.id, obj.routes
        )

    @admin.display(ordering="services")
    def services(self, obj):
        url = reverse("admin:busstops_service_changelist")
        return format_html(
            '<a href="{}?source__id__exact={}">{}</a>', url, obj.id, obj.services
        )

    # @admin.display(ordering="journeys")
    # def journeys(self, obj):
    #     url = reverse("admin:vehicles_vehiclejourney_changelist")
    #     return format_html(
    #         '<a href="{}?source__id__exact={}">{}</a>', url, obj.id, obj.journeys
    #     )

    def delete_routes(self, request, queryset):
        result = Route.objects.filter(source__in=queryset).update(service=None)
        self.message_user(request, result)

    def remove_datetimes(self, request, queryset):
        result = queryset.order_by().update(datetime=None, sha1="")
        log_change(request, queryset, ["datetime", "sha1"])
        self.message_user(request, result)


@admin.register(models.SIRISource)
class SIRISourceAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "requestor_ref", "areas", "is_poorly")
    autocomplete_fields = ("operators", "admin_areas")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                areas=StringAgg(
                    Cast("admin_areas__atco_code", output_field=CharField()),
                    Value(", "),
                )
            )
        return queryset

    @staticmethod
    def areas(obj):
        return obj.areas


class PaymentMethodOperatorInline(admin.TabularInline):
    model = models.PaymentMethod.operator_set.through
    autocomplete_fields = ("operator",)


class PaymentMethodServiceInline(admin.TabularInline):
    model = models.PaymentMethod.service_set.through
    autocomplete_fields = ("service",)


@admin.register(models.PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "operators")
    inlines = (PaymentMethodOperatorInline, PaymentMethodServiceInline)
    search_fields = ("name",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                operators=StringAgg("operator", Value(", "), distinct=True)
            )
        return queryset

    @staticmethod
    def operators(obj):
        return obj.operators


admin.site.register(models.Region)
admin.site.register(models.District)
admin.site.register(models.OperatorGroup)
