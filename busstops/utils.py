from datetime import datetime
from functools import cache
from typing import NamedTuple

from django.contrib.gis.gdal import CoordTransform, SpatialReference
from django.contrib.gis.geos import Polygon
from django.utils.timezone import make_aware


def get_bounding_box(request):
    return Polygon.from_bbox(
        [request.GET[key] for key in ("xmin", "ymin", "xmax", "ymax")]
    )


@cache
def get_coord_transform(source_srid: int, target_srid: int = 4326) -> CoordTransform:
    return CoordTransform(SpatialReference(source_srid), SpatialReference(target_srid))


def get_datetime(string):
    """return a timezone-aware datetime object
    from a string like 2021-07-05T12:01:57
    (the value of a CreationDateTime or ModificationDateTime attribute)
    """

    if string:
        dt = datetime.fromisoformat(string)
        if not dt.tzinfo:
            return make_aware(dt)
        return dt


class Crumb(NamedTuple):
    name: str
    url: str

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return self.url
