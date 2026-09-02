from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from buses.utils import format_xml
from busstops.models import Service, StopPoint

from .models import Situation


def situations_index(request):
    situations = Situation.objects.filter(current=True).prefetch_related(
        Prefetch("consequence_set", to_attr="consequences"),
        "link_set",
        "validityperiod_set",
    )

    return render(
        request,
        "situations_index.html",
        {
            "situations": situations,
        },
    )


def situation(request, id):
    situation = get_object_or_404(
        Situation.objects.prefetch_related(
            Prefetch("consequence_set", to_attr="consequences"),
        ),
        id=id,
    )

    context = {}

    if situation.data:
        context["css"], context["xml"] = format_xml(situation.data)

    context["stops"] = StopPoint.objects.filter(consequence__situation=situation)
    context["services"] = Service.objects.filter(consequence__situation=situation)

    return render(
        request,
        "situation_detail.html",
        {
            **context,
            "situation": situation,
            "situations": [situation],
        },
    )
