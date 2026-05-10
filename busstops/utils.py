from django.contrib.gis.geos import Polygon
from datetime import datetime
from django.utils.timezone import make_aware


def get_bounding_box(request):
    try:
        return Polygon.from_bbox(
            [
                float(request.GET.get("xmin", 0)),
                float(request.GET.get("ymin", 0)),
                float(request.GET.get("xmax", 0)),
                float(request.GET.get("ymax", 0)),
            ]
        )
    except (TypeError, ValueError):
        return None


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
