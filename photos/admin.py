from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from buses.admin_utils import M2MThroughMixin

from .models import Photo


@admin.register(Photo)
class PhotoAdmin(M2MThroughMixin, GISModelAdmin):
    raw_id_fields = ("vehicles", "livery", "vehicle_type", "service", "user")
    list_display = ("__str__", "credit", "url", "bbox")
    list_filter = ("license",)
