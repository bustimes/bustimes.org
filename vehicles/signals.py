import sentry_sdk
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import utils
from .models import Livery, Vehicle, VehicleRevision


@receiver(post_save, sender=Livery)
def liveries_cache_update(sender, instance, **kwargs):
    cache.set("liveries_css_version", int(instance.updated_at.timestamp()), None)


@receiver(post_save, sender=Vehicle)
def vehicle_cache_update(sender, instance, created, **kwargs):
    if not created and instance.latest_journey_id:
        cache.delete(f"journey{instance.latest_journey_id}")


def pending_vehicle_edits_gauge():
    sentry_sdk.metrics.gauge(
        "pending_vehicle_edits",
        VehicleRevision.objects.filter(pending=True, disapproved=False).count(),
    )


@receiver(post_save, sender=VehicleRevision)
@receiver(post_delete, sender=VehicleRevision)
def vehicle_revision_changed(sender, instance, **kwargs):
    transaction.on_commit(pending_vehicle_edits_gauge)


def delete_latest_location(vehicle_id):
    # so importers stop using a cached Vehicle (see ImportLiveVehiclesCommand.handle_items)
    if utils.redis_client:
        utils.redis_client.delete(f"vehicle{vehicle_id}")


@receiver(post_delete, sender=Vehicle)
def vehicle_deleted(sender, instance, **kwargs):
    vehicle_id = instance.id  # (set to None after deletion)
    transaction.on_commit(lambda: delete_latest_location(vehicle_id))
