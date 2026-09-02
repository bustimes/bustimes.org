from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill, ResizeToFit
from imagekit.specs import ImageSpec

from .processors import SmartCrop


class SmartSpec(ImageSpec):
    """An image spec that crops around the vehicle, if we've detected one.

    Including the bounding box in the processors means the cache file name
    changes when the box does, so a corrected box takes effect immediately.
    """

    format = "JPEG"
    options = {"quality": 60}

    aspect = None
    padding = 0.1
    resize = None

    @property
    def processors(self):
        instance = getattr(self.source, "instance", None)
        bbox = getattr(instance, "bbox", None)
        if bbox:
            # the resize is still needed, but has nothing left to crop
            return [SmartCrop(bbox, self.aspect, self.padding), self.resize]
        return [self.resize]


class OpenGraph(SmartSpec):
    aspect = 1200 / 630
    padding = 0.12
    resize = ResizeToFill(1200, 630)


class Thumbnail(SmartSpec):
    """Keeps the photo's own aspect ratio, just moves in a bit closer"""

    padding = 0.15
    resize = ResizeToFit(320, upscale=False)


class Photo(models.Model):
    image = models.ImageField()
    width = models.PositiveSmallIntegerField(null=True, blank=True)
    height = models.PositiveSmallIntegerField(null=True, blank=True)

    # [x1, y1, x2, y2] as fractions of the image size - see photos.detect
    bbox = ArrayField(models.FloatField(), size=4, null=True, blank=True)

    image_1200_630 = ImageSpecField(source="image", spec=OpenGraph)
    image_1200 = ImageSpecField(
        source="image",
        processors=[ResizeToFit(1200, upscale=False)],
        format="JPEG",
        options={"quality": 60},
    )
    image_320 = ImageSpecField(source="image", spec=Thumbnail)
    credit = models.CharField(max_length=255, blank=True)
    caption = models.CharField(max_length=255, blank=True)
    url = models.URLField(blank=True, verbose_name="URL")
    license = models.CharField(null=True, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)
    location = models.PointField(null=True, blank=True)
    metadata = models.JSONField(blank=True, default=dict)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, models.SET_NULL, null=True, blank=True
    )

    vehicles = models.ManyToManyField(
        "vehicles.Vehicle", blank=True, through="PhotoVehicle"
    )

    livery = models.ForeignKey(
        "vehicles.Livery", models.SET_NULL, null=True, blank=True
    )
    vehicle_type = models.ForeignKey(
        "vehicles.VehicleType", models.SET_NULL, null=True, blank=True
    )
    service = models.ForeignKey(
        "busstops.Service", models.SET_NULL, null=True, blank=True
    )

    def __str__(self):
        return self.caption

    def get_absolute_url(self):
        if vehicle := self.vehicles.first():
            return vehicle.get_absolute_url()
        return ""


class PhotoVehicle(models.Model):
    # Photo is deleted by Python; the ON DELETE CASCADE comes from the migration
    photo = models.ForeignKey(Photo, models.DO_NOTHING, related_name="photovehicle+")
    vehicle = models.ForeignKey(
        "vehicles.Vehicle", models.DB_CASCADE, related_name="photovehicle+"
    )

    class Meta:
        db_table = "photos_photo_vehicles"
        unique_together = ("photo", "vehicle")
