from django.contrib import admin
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html
from sql_util.utils import SubqueryCount

from buses.admin_utils import M2MThroughMixin
from bustimes.admin import log_change

from .models import Invitation, OperatorUser, User


def get_count(obj, attribute, approved):
    return format_html(
        '<a href="{}?user={}&{}">{}</a>',
        reverse("admin:vehicles_vehiclerevision_changelist"),
        obj.id,
        approved,
        getattr(obj, attribute, None),
    )


class OperatorUserInline(admin.TabularInline):
    model = OperatorUser
    raw_id_fields = ("operator",)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    raw_id_fields = ("user_permissions",)
    actions = ("trust", "distrust")
    search_fields = ("username", "email")
    readonly_fields = ("revisions", "disapproved", "pending")
    list_display = (
        "id",
        "username",
        "email",
        "last_login",
        "is_active",
        "score",
        "trusted",
        *readonly_fields,
    )
    list_display_links = ("id", "username")
    inlines = (OperatorUserInline,)
    list_filter = (
        "trusted",
        "is_staff",
        "groups",
        ("user_permissions", admin.RelatedOnlyFieldListFilter),
    )

    @admin.display(ordering="revisions")
    def revisions(self, obj):
        return get_count(obj, "revisions", "")

    @admin.display(ordering="disapproved")
    def disapproved(self, obj):
        return get_count(obj, "disapproved", "disapproved=True")

    @admin.display(ordering="pending")
    def pending(self, obj):
        return get_count(obj, "pending", "pending=True")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.resolver_match.view_name in (
            "admin:accounts_user_changelist",
            "admin:accounts_user_change",
        ):
            queryset = queryset.annotate(
                disapproved=SubqueryCount(
                    "vehiclerevision", filter=Q(disapproved=True)
                ),
                pending=SubqueryCount("vehiclerevision", filter=Q(pending=True)),
                revisions=SubqueryCount("vehiclerevision"),
            )
        return queryset

    def trust(self, request, queryset):
        count = queryset.order_by().update(trusted=True)
        log_change(request, queryset, ["trusted"])
        self.message_user(request, f"Trusted {count} users")

    def distrust(self, request, queryset):
        count = queryset.order_by().update(trusted=False)
        log_change(request, queryset, ["trusted"])
        self.message_user(request, f"Disusted {count} users")


@admin.register(OperatorUser)
class OperatorUserAdmin(admin.ModelAdmin):
    raw_id_fields = ("user", "operator")


@admin.register(Invitation)
class InvitationAdmin(M2MThroughMixin, admin.ModelAdmin):
    raw_id_fields = ("operators",)
