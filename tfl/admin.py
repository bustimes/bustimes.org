from django.contrib import admin

from . import models


@admin.register(models.BaseVersion)
class BaseVersionAdmin(admin.ModelAdmin):
    list_display = ["version", "valid_from", "valid_to"]


@admin.register(models.Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "agency", "base_version"]
    list_filter = ["base_version"]
    search_fields = ["code", "name"]
