from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.contrib.gis.db.models import GeometryField
from django.db.models import CharField, Exists, F, Func, OuterRef, Value
from django.db.models.aggregates import StringAgg
from django.db.models.functions import Cast, Now
from django.forms import ModelForm, Textarea
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from sql_util.utils import SubqueryCount

from buses.admin_utils import M2MThroughMixin

from .models import (
    BankHoliday,
    BankHolidayDate,
    Calendar,
    CalendarBankHoliday,
    CalendarDate,
    Garage,
    Note,
    Route,
    RouteLink,
    StopTime,
    TimetableDataSource,
    Trip,
    Version,
)


def log_change(request, queryset, fields):
    admin.models.LogEntry.objects.log_actions(
        user_id=request.user.pk,
        queryset=queryset,
        action_flag=admin.models.CHANGE,
        change_message=[{"changed": {"fields": fields}}],
    )


class TripInline(admin.TabularInline):
    model = Trip
    show_change_link = True
    raw_id_fields = (
        "destination",
        "notes",
        "calendar",
        "garage",
        "vehicle_type",
        "operator",
        "next_trip",
    )
    fields = ("start", "end", "destination", "inbound", "calendar")


class StopTimeInline(admin.TabularInline):
    model = StopTime
    autocomplete_fields = ("stop",)


class VersionInline(admin.TabularInline):
    model = Version


class TimetableDataSourceAdminForm(ModelForm):
    class Meta:
        widgets = {"notes": Textarea()}


@admin.register(TimetableDataSource)
class TimetableDataSourceAdmin(M2MThroughMixin, admin.ModelAdmin):
    autocomplete_fields = ("operators",)
    list_display = (
        "id",
        "name",
        "url",
        "nocs",
        "active",
        "complete",
        "region_id",
        "sources",
        "modified_at",
    )
    list_filter = ("modified_at", "active", "complete")
    search_fields = ("url", "name", "search")
    actions = ("activate", "deactivate")
    inlines = (VersionInline,)
    readonly_fields = ("modified_at",)
    form = TimetableDataSourceAdminForm

    def nocs(self, obj):
        return obj.nocs

    def sources(self, obj):
        url = reverse("admin:busstops_datasource_changelist")
        return format_html('<a href="{}?source__id__exact={}">Sources</a>', url, obj.id)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                nocs=StringAgg("operators", Value(", "), distinct=True)
            )
        return queryset

    def activate(self, request, queryset):
        count = queryset.order_by().update(active=True, modified_at=Now())
        log_change(request, queryset, ["active"])
        self.message_user(request, f"Activated {count}")

    def deactivate(self, request, queryset):
        count = queryset.order_by().update(active=False, modified_at=Now())
        log_change(request, queryset, ["active"])
        self.message_user(request, f"Deactivated {count}")


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "code", "start_date", "end_date")
    list_filter = (("source", admin.RelatedOnlyFieldListFilter),)
    raw_id_fields = ("source", "service", "registration")
    search_fields = ("code",)
    # inlines = [TripInline]


@admin.register(Trip)
class TripAdmin(M2MThroughMixin, admin.ModelAdmin):
    list_filter = (("calendar", admin.EmptyFieldListFilter),)
    raw_id_fields = ("route", *TripInline.raw_id_fields)
    list_display = (
        "__str__",
        "vehicle_journey_code",
        "ticket_machine_code",
        "end",
        "headsign",
        "inbound",
        "block",
        "operator_id",
    )
    # inlines = [StopTimeInline]


class CalendarDateInline(admin.TabularInline):
    model = CalendarDate


class CalendarBankHolidayInline(admin.TabularInline):
    model = CalendarBankHoliday
    select_related = ("bank_holiday",)


@admin.register(CalendarDate)
class CalendarDateAdmin(admin.ModelAdmin):
    list_display = ("__str__", "start_date", "end_date")
    list_filter = (
        "start_date",
        "end_date",
        ("summary", admin.EmptyFieldListFilter),
    )
    raw_id_fields = ("calendar",)


@admin.register(Calendar)
class CalendarAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__", "summary")
    inlines = (CalendarDateInline, CalendarBankHolidayInline)
    list_filter = (("trip", admin.EmptyFieldListFilter),)
    readonly_fields = ("routes",)
    save_as = True

    def routes(self, obj):
        routes = Route.objects.filter(
            Exists(Trip.objects.filter(calendar=obj, route=OuterRef("pk")))
        )
        routes = (
            (reverse("admin:bustimes_route_change", args=(route.id,)), route)
            for route in routes
        )
        return mark_safe(
            "<br>".join(f'<a href="{url}">{route}</a>' for url, route in routes)
        )


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("code", "text")
    search_fields = ("code", "text")


@admin.register(Garage)
class GarageAdmin(GISModelAdmin):
    search_fields = ("code", "name")
    list_display = ("code", "name", "operators")
    list_filter = (
        ("vehicle", admin.EmptyFieldListFilter),
        ("trip", admin.EmptyFieldListFilter),
        ("vehicle__operator", admin.RelatedOnlyFieldListFilter),
    )
    raw_id_fields = ("operator",)

    def operators(self, obj):
        return obj.operators

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                operators=StringAgg("vehicle__operator", Value(", "), distinct=True)
            )
        return queryset


class BankHolidayDateInline(admin.StackedInline):
    model = BankHolidayDate


@admin.register(BankHoliday)
class BankHolidayAdmin(admin.ModelAdmin):
    inlines = (BankHolidayDateInline,)
    list_display = ("name", "dates", "calendars")

    def dates(self, obj):
        return obj.dates

    def calendars(self, obj):
        return obj.calendars

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                dates=StringAgg(
                    Cast("bankholidaydate__date", output_field=CharField()), Value(", ")
                ),
                calendars=SubqueryCount("calendarbankholiday"),
            )
        return queryset


class StartPoint(Func):
    function = "ST_StartPoint"
    output_field = GeometryField()


class EndPoint(Func):
    function = "ST_EndPoint"
    output_field = GeometryField()


class DodgyRouteLinkFilter(admin.SimpleListFilter):
    title = "Dodgy"
    parameter_name = "dodgy"

    def lookups(self, request, model_admin):
        return (
            ("from_stop", "start point is far away"),
            ("to_stop", "end point is far away"),
        )

    def queryset(self, request, queryset):
        if self.value() == "from_stop":
            queryset = queryset.annotate(startpoint=StartPoint(F("geometry"))).exclude(
                startpoint__dwithin=(F("from_stop__latlong"), 0.15)
            )
        elif self.value() == "to_stop":
            queryset = queryset.annotate(endpoint=EndPoint(F("geometry"))).exclude(
                endpoint__dwithin=(F("to_stop__latlong"), 0.15)
            )
        return queryset


@admin.register(RouteLink)
class RouteLinkAdmin(GISModelAdmin):
    raw_id_fields = ("from_stop", "to_stop", "service")
    list_display = ("from_stop", "to_stop", "service")

    list_filter = (DodgyRouteLinkFilter,)
