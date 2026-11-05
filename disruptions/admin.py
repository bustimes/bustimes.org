from django.contrib import admin
from sql_util.utils import SubqueryCount

from buses.admin_utils import M2MThroughMixin
from bustimes.admin import log_change

from .models import AffectedJourney, Consequence, Link, Situation, ValidityPeriod


class ConsequenceInline(M2MThroughMixin, admin.StackedInline):
    model = Consequence
    autocomplete_fields = ("stops", "services", "operators", "admin_areas")
    readonly_fields = ("data",)
    show_change_link = True


class ValidityPeriodInline(admin.TabularInline):
    model = ValidityPeriod


class LinkInline(admin.TabularInline):
    model = Link


class AffectedJourneyInline(admin.TabularInline):
    model = AffectedJourney
    raw_id_fields = ("trip",)


@admin.register(AffectedJourney)
class AffectedJourneyAdmin(admin.ModelAdmin):
    raw_id_fields = ("trip", "situation")
    list_filter = (
        "condition",
        ("trip__operator", admin.RelatedOnlyFieldListFilter),
    )


@admin.register(Situation)
class SituationAdmin(admin.ModelAdmin):
    inlines = (
        ValidityPeriodInline,
        LinkInline,
        ConsequenceInline,
        AffectedJourneyInline,
    )
    list_display = (
        "__str__",
        "reason",
        "participant_ref",
        "source",
        "current",
        "periods",
        "services",
        "stops",
        "created_at",
        "modified_at",
    )
    list_filter = (
        "current",
        "source",
        "participant_ref",
        "reason",
        ("consequence__admin_areas", admin.RelatedOnlyFieldListFilter),
        "created_at",
        "modified_at",
    )
    actions = ("current_true", "current_false")
    readonly_fields = (
        "created_at",
        "situation_number",
        "reason",
        "participant_ref",
        "data",
    )

    @admin.display(ordering="periods")
    def periods(self, obj):
        return obj.periods

    @admin.display(ordering="services")
    def services(self, obj):
        return obj.services

    @admin.display(ordering="stops")
    def stops(self, obj):
        return obj.stops

    def current_true(self, request, queryset):
        log_change(request, queryset, ["current"])
        result = queryset.order_by().update(current=True)
        self.message_user(request, f"{result}")

    def current_false(self, request, queryset):
        log_change(request, queryset, ["current"])
        result = queryset.order_by().update(current=False)
        self.message_user(request, f"{result}")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "changelist" in request.resolver_match.view_name:
            queryset = queryset.annotate(
                periods=SubqueryCount("validityperiod"),
                services=SubqueryCount("consequence__services"),
                stops=SubqueryCount("consequence__stops"),
            )
        return queryset
