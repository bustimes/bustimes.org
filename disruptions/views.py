import json

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from buses.utils import format_json, format_xml
from busstops.models import Service, StopPoint

from .models import Situation


def situations_index(request):
    situations = (
        Situation.objects.filter(current=True)
        .prefetch_related(
            Prefetch("consequence_set", to_attr="consequences"),
            "link_set",
            "validityperiod_set",
        )
        .order_by("id")
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
        try:
            context["xml"] = format_xml(situation.data)
        except SyntaxError:
            context["xml"] = format_json(json.loads(situation.data))

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


def old_disruptions(request):
    bods_situations = Situation.objects.filter(source__name="Bus Open Data")

    updated_at = bods_situations.order_by("-modified_at").first().modified_at

    hiddens = list(
        bods_situations.filter(current=False)
        .prefetch_related("link_set")
        .order_by("created_at")
    )

    if request.user.is_staff:
        log_entries = (
            LogEntry.objects.filter(
                content_type=ContentType.objects.get_for_model(Situation),
                object_id__in=[str(situation.id) for situation in hiddens],
                change_message__icontains='"current"',
            )
            .select_related("user")
            .order_by("action_time")
        )
        log_entries = {entry.object_id: entry for entry in log_entries}
        for situation in hiddens:
            situation.hidden = log_entries.get(str(situation.id))

    # suspects = (
    #     bods_situations.filter(current=True, link__isnull=False)
    #     .prefetch_related("link_set").order_by("modified_at")
    # )

    return render(
        request,
        "old_disruptions.html",
        {
            "situations": hiddens,
            "show_hidden_by": request.user.is_staff,
            # "suspects": suspects,
            "updated_at": updated_at,
        },
    )
