from django.contrib import admin

from buses.admin_utils import M2MThroughMixin

from .models import Photo


@admin.register(Photo)
class PhotoAdmin(M2MThroughMixin, admin.ModelAdmin):
    raw_id_fields = ("vehicles", "livery", "vehicle_type", "service", "user")
    list_display = ("__str__", "credit", "url", "bbox")
