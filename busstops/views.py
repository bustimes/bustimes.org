"""View definitions."""

import csv
import datetime
import os
import logging
from http import HTTPStatus
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.postgres.aggregates import ArrayAgg, BoolOr
from itertools import groupby
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank
from django.contrib.sitemaps import Sitemap
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import F, OuterRef, Prefetch, Q, When, Case, Value
from django.db.models.functions import Coalesce, Now
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.cache import patch_response_headers
from django.utils.functional import SimpleLazyObject
from django.views.csrf import csrf_failure as django_csrf_failure
from django.views.decorators.cache import cache_control
from django.views.decorators.http import last_modified
from django.views.generic.detail import DetailView
from redis.exceptions import ConnectionError
from sql_util.utils import Exists, SubqueryMax, SubqueryMin
from ukpostcodeutils import validation

from bustimes.models import Trip, StopTime
from departures import live
from disruptions.models import Consequence, Situation
from fares.models import FareTable
from vehicles.models import Vehicle
from vehicles.utils import redis_client
from vosa.models import Registration

from . import forms
from .models import (
    AdminArea,
    DataSource,
    District,
    Locality,
    Operator,
    PaymentMethod,
    Region,
    Service,
    ServiceColour,
    StopArea,
    StopPoint,
)
from .utils import get_bounding_box

operator_has_current_services = Exists("service", filter=Q(service__current=True))
operator_has_current_services_or_vehicles = operator_has_current_services | Exists(
    "vehicle", filter=Q(withdrawn=False, latest_journey__isnull=False)
)


def get_colours(services):
    colours = set(service.colour_id for service in services if service.colour_id)
    if colours:
        return ServiceColour.objects.filter(id__in=colours)


def version(request):
    if commit_hash := os.environ.get("COMMIT_HASH"):
        return HttpResponse(
            f"""<a href="https://github.com/jclgoodwin/bustimes.org/commit/{commit_hash}">{commit_hash}</a>""",
        )
    return HttpResponse(
        os.environ.get("KAMAL_CONTAINER_NAME"), content_type="text/plain"
    )


def flixbus_affiliate_link(**kwargs) -> str:
    query = {"awinmid": 110896, "awinaffid": 242611, **kwargs}
    return f"https://www.awin1.com/cread.php?{urlencode(query)}"


def flibco_affiliate_link(
    ued="https://www.flibco.com/en/shuttle/bus-coach-london-stansted-airport", **kwargs
):
    return flixbus_affiliate_link(awinmid=53945, ued=ued, **kwargs)


def index(request):
    def stats():
        return {
            "buses": redis_client and redis_client.zcard("vehicle_location_locations"),
            "stops": StopPoint.objects.filter(active=True).count(),
            "routes": Service.objects.filter(current=True).count(),
            "operators": Service.operator.through.objects.filter(service__current=True)
            .distinct("operator")
            .count(),
        }

    context = {"stats": stats, "popular_services": cache.get("popular_services")}

    if context["popular_services"]:
        context["colours"] = get_colours(context["popular_services"])

    return render(request, "index.html", context)


def not_found(request, exception):
    """Custom 404 handler view"""

    context = {}

    if request.resolver_match:
        if request.resolver_match.url_name == "service_detail" and exception.args:
            code = request.resolver_match.kwargs["slug"]
            service_code_parts = code.split("-")

            if len(service_code_parts) >= 4:
                suggestion = None
                services = Service.objects.filter(current=True).only("slug")

                # e.g. from '17-N4-_-y08-1' to '17-N4-_-y08':
                suggestion = services.filter(
                    service_code__icontains="_" + "-".join(service_code_parts[:4]),
                ).first()

                # e.g. from '46-holt-circular-1' to '46-holt-circular-2':
                if not suggestion and code.lower():
                    if service_code_parts[-1].isdigit():
                        slug = "-".join(service_code_parts[:-1])
                    else:
                        slug = "-".join(service_code_parts)
                    suggestion = services.filter(slug__startswith=slug).first()

                if suggestion:
                    return redirect(suggestion)

        elif request.resolver_match.url_name == "stoppoint_detail":
            try:
                return redirect(
                    StopPoint.objects.get(
                        naptan_code__iexact=request.resolver_match.kwargs["pk"]
                    )
                )
            except StopPoint.DoesNotExist:
                pass

        context["exception"] = exception
    elif request.path[:6] == "/STOP/":
        return redirect(f"/stops/{request.path[6:]}")
    elif len(request.path) > 1 and request.path.endswith("/"):
        try:
            resolver_match = resolve(request.path[:-1])
            return resolver_match.func(request, **resolver_match.kwargs)
        except Http404:
            pass

    # anonymise request (cos response may be cached)
    request.user = AnonymousUser

    context["ad"] = False
    response = render(request, "404.html", context)
    response.status_code = HTTPStatus.NOT_FOUND

    if not request.resolver_match:
        # no matching url pattern, cache for an hour
        patch_response_headers(response, cache_timeout=3600)

    return response


def csrf_failure(request, reason=""):
    logging.warning("CSRF failure: %s", reason)
    if (
        request.resolver_match
        and request.resolver_match.url_name == "login"
        and request.user.is_authenticated
    ):
        return HttpResponseRedirect(
            request.POST.get("next") or settings.LOGIN_REDIRECT_URL
        )
    return django_csrf_failure(request, reason)


@cache_control(max_age=3600)
def robots_txt(request):
    "robots.txt"

    if request.get_host() == "bustimes.org":  # live site
        content = """User-agent: *
Disallow: /trips/
Disallow: /api/
Disallow: /accounts/
Disallow: /fares/
Disallow: /vehicles/tfl/
Disallow: /vehicles/*?date=*
Disallow: /stops/*?date=*
Disallow: /services/*?date=*
Disallow: /services/*/*
Disallow: /sources
Disallow: /*/debug
Disallow: /*/edit

User-agent: ImagesiftBot
Disallow: /

User-agent: AdsBot-Google
User-agent: Mediapartners-Google
User-agent: Google-Display-Ads-Bot
User-agent: AmazonAdBot
User-agent: ias_crawler
User-agent: ias_wombles
User-agent: proximic
User-agent: TTD-Content
User-agent: Quantcastbot
User-agent: CriteoBot/0.1
User-agent: InteractiveAdvertisingBureau
User-agent: Leikibot
User-agent: GrapeshotCrawler
User-agent: adidxbot
User-agent: Yahoo Ad Monitoring
User-agent: peer39_crawler
User-agent: Connatix/FeedCrawler
User-agent: Connatix/WebCrawler
User-agent: bidswitchbot
User-agent: Verity
User-agent: GumGumAdsTxtCrawler/1.0
User-agent: IAB-Tech-Lab
User-agent: TheCoolCompany-Crawler
User-agent: SPM-AdsTxt-Verifier
User-agent: OpenXAdsTxtCrawler
User-agent: PubMatic Crawler Bot
User-agent: Seedtag-Node-Request
User-agent: adstxt/1.0
User-agent: Jounce
User-agent: Kargo Ads.txt Crawler
Disallow:

Sitemap: https://bustimes.org/sitemap.xml
"""
    else:  # staging site/other
        content = """User-agent: Mediapartners-Google
User-agent: AdsBot-Google
Disallow:

User-agent: *
Disallow: /
"""

    return HttpResponse(content, content_type="text/plain")


def contact(request):
    """Contact page with form"""
    submitted = False
    if request.method == "POST":
        form = forms.ContactForm(request.POST, request=request)
        if form.is_valid():
            subject = form.cleaned_data["message"][:50].splitlines()[0]

            body = [
                form.cleaned_data["message"],
                form.cleaned_data["referrer"],
                request.headers.get("user-agent", ""),
            ]
            if request.user.is_authenticated:
                body.append(f"https://bustimes.org{request.user.get_absolute_url()}")
            body = "\n\n".join(body)

            message = EmailMessage(
                subject,
                body,
                '"{}" <contactform@bustimes.org>'.format(form.cleaned_data["name"]),
                ["contact@bustimes.org"],
                reply_to=[form.cleaned_data["email"]],
            )
            message.send()
            submitted = True
    else:
        referrer = request.headers.get("referer")
        initial = {
            "referrer": referrer,
            "message": request.GET.get("message"),
        }
        if request.user.is_authenticated:
            initial["email"] = request.user.email
        form = forms.ContactForm(initial=initial)
    return render(request, "contact.html", {"form": form, "submitted": submitted})


def status(request):
    context = {
        "sources": DataSource.objects.filter(
            name__in=["National Operator Codes", "NPTG", "NaPTAN", "Irish NaPTAN"]
        ),
        "bod_avl_status": {},
    }

    for key in ("bod_avl", "Transport_for_Wales", "Bus_Open_Data", "Todd's_Travel"):
        key = f"{key}_status"
        if status := cache.get(key):
            context["bod_avl_status"][key] = status

    context["statuses"] = cache.get_many(
        [
            "Realtime_Transport_Operators_status",
            "Irish_Citylink_status",
            "Translink_status",
            "Stagecoach_status",
            "Ember_status",
            "TfE_status",
            "jersey_status",
        ]
    ).items()

    return render(
        request,
        "status.html",
        context,
    )


@cache_control(max_age=3600)
def stops_mvt(request, z, x, y):
    """Mapbox Vector Tile endpoint for bus stops, replacing stops_json eventually."""
    if z < 10:
        return HttpResponse(b"", content_type="application/x-protobuf")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ST_AsMVT(tile, 'stops', 4096, 'geom')
            FROM (
                SELECT
                    ST_AsMVTGeom(
                        ST_Transform(sp.latlong, 3857),
                        ST_TileEnvelope(%s, %s, %s),
                        4096, 64, true
                    ) AS geom,
                    '/stops/' || sp.atco_code AS url,
                    sp.indicator,
                    sp.common_name,
                    COALESCE(l.name, '') AS locality_name,
                    COALESCE(
                        sp.heading,
                        CASE sp.bearing
                            WHEN 'N'  THEN 0
                            WHEN 'NE' THEN 45
                            WHEN 'E'  THEN 90
                            WHEN 'SE' THEN 135
                            WHEN 'S'  THEN 180
                            WHEN 'SW' THEN 225
                            WHEN 'W'  THEN 270
                            WHEN 'NW' THEN 315
                        END
                    ) AS bearing,
                    CASE
                        WHEN sp.indicator != '' AND LENGTH(sp.indicator) < 3
                             AND sp.indicator != LOWER(sp.indicator)
                            THEN sp.indicator
                        WHEN sp.indicator != ''
                             AND array_length(regexp_split_to_array(sp.indicator, '\\s+'), 1) = 2
                             AND LENGTH((regexp_split_to_array(sp.indicator, '\\s+'))[2]) < 3
                             AND LOWER((regexp_split_to_array(sp.indicator, '\\s+'))[1])
                                 IN ('stop','bay','stand','stance','gate','platform')
                            THEN (regexp_split_to_array(sp.indicator, '\\s+'))[2]
                        WHEN POSITION(' ' IN sp.common_name) > 0
                             AND LENGTH(regexp_replace(sp.common_name, '^.* ', '')) < 3
                             AND (   regexp_replace(sp.common_name, '^.* ', '') ~ '^[0-9]+$'
                                  OR regexp_replace(sp.common_name, '^.* ', '') ~ '^[A-Z]+$')
                            THEN regexp_replace(sp.common_name, '^.* ', '')
                        ELSE NULL
                    END AS icon,
                    (
                        SELECT STRING_AGG(DISTINCT su.line_name, ',' ORDER BY su.line_name)
                        FROM busstops_stopusage su
                        JOIN busstops_service svc ON su.service_id = svc.id
                        WHERE su.stop_id = sp.atco_code AND svc.current = true
                    ) AS line_names
                FROM busstops_stoppoint sp
                LEFT JOIN busstops_locality l ON sp.locality_id = l.id
                WHERE
                    sp.latlong && ST_Transform(ST_TileEnvelope(%s, %s, %s), 4326)
                    AND EXISTS (
                        SELECT 1
                        FROM busstops_stopusage su
                        JOIN busstops_service svc ON su.service_id = svc.id
                        WHERE su.stop_id = sp.atco_code AND svc.current = true
                    )
            ) AS tile
            WHERE geom IS NOT NULL
            """,
            [z, x, y, z, x, y],
        )
        row = cursor.fetchone()
    mvt_data = bytes(row[0]) if row and row[0] else b""
    return HttpResponse(mvt_data, content_type="application/x-protobuf")


def stats(request):
    return JsonResponse(cache.get("vehicle-tracking-stats", []), safe=False)


def timetable_source_stats(request):
    return JsonResponse(cache.get("timetable-source-stats", []), safe=False)


@cache_control(max_age=3600)
def stops_json(request):
    """JSON endpoint accessed by the JavaScript map,
    listing the active StopPoints within a rectangle,
    in standard GeoJSON format
    """
    try:
        bounding_box = get_bounding_box(request)
    except (KeyError, ValueError):
        return HttpResponseBadRequest()

    if bounding_box.area > 0.15:
        return HttpResponseBadRequest()

    results = (
        StopPoint.objects.filter(
            latlong__bboverlaps=bounding_box, service__current=True
        )
        .annotate(line_names=stop_line_names)
        .select_related("locality")
        .defer("locality__latlong")
    )

    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": stop.latlong.coords,
                    },
                    "properties": {
                        "name": stop.get_qualified_name(),
                        "indicator": stop.indicator,
                        "icon": stop.get_icon(),
                        "bearing": stop.get_heading(),
                        "url": stop.get_absolute_url(),
                        "services": stop.get_line_names(),
                        "stop_type": stop.stop_type,
                        "bus_stop_type": stop.bus_stop_type,
                    },
                }
                for stop in results
            ],
        }
    )


class UppercasePrimaryKeyMixin:
    """Normalises the primary key argument to uppercase"""

    def get_object(self, queryset=None):
        """Given a pk argument like 'ea' or 'sndr',
        convert it to 'EA' or 'SNDR',
        then otherwise behaves like ordinary get_object
        """
        primary_key = self.kwargs.get("pk")
        if (
            primary_key is not None
            and "-" not in primary_key
            and not primary_key.isupper()
        ):
            self.kwargs["pk"] = primary_key.upper()
        return super().get_object(queryset)


class RegionDetailView(UppercasePrimaryKeyMixin, DetailView):
    """A single region and the administrative areas in it"""

    model = Region

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["areas"] = self.object.adminarea_set.exclude(name="")
        if len(context["areas"]) == 1:
            context["districts"] = (
                context["areas"][0]
                .district_set.filter(locality__stoppoint__active=True)
                .distinct()
            )
            del context["areas"]

        context["operators"] = Operator.objects.filter(
            operator_has_current_services_or_vehicles,
            Q(region=self.object)
            | Q(
                noc__in=Operator.regions.through.objects.filter(
                    region=self.object
                ).values("operator")
            ),
        ).only("slug", "name")

        if len(context["operators"]) == 1:
            context["services"] = sorted(
                context["operators"][0]
                .service_set.filter(current=True)
                .defer("geometry"),
                key=Service.get_order,
            )
            context["colours"] = get_colours(context["services"])

        return context


class AdminAreaDetailView(DetailView):
    """A single administrative area,
    and the districts, localities (or stops) in it
    """

    model = AdminArea
    queryset = model.objects.select_related("region")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        stops = StopPoint.objects.filter(
            Exists("service", filter=Q(service__current=True))
        )

        # Districts in this administrative area
        context["districts"] = self.object.district_set.filter(
            Exists(stops.filter(locality__district=OuterRef("pk")))
        )

        context["operators"] = Operator.objects.filter(
            Exists(
                Service.objects.filter(
                    current=True,
                    operator=OuterRef("pk"),
                    stops__admin_area=self.object,
                )
                .only("id")
                .order_by()
            )
        ).only("slug", "name")

        # Districtless localities in this administrative area
        context["localities"] = self.object.locality_set.filter(
            Exists(stops.filter(locality=OuterRef("pk")))
            | Exists(stops.filter(locality__parent=OuterRef("pk"))),
            district=None,
            parent=None,
        ).defer("latlong")

        if not (context["localities"] or context["districts"]):
            services = Service.objects.filter(current=True).defer(
                "geometry", "search_vector"
            )
            services = services.filter(
                Exists(
                    StopPoint.objects.filter(
                        service=OuterRef("pk"), admin_area=self.object
                    )
                )
            )
            context["services"] = sorted(services, key=Service.get_order)
            context["modes"] = {
                service.mode for service in context["services"] if service.mode
            }
        context["breadcrumb"] = [self.object.region]
        return context

    def render_to_response(self, context):
        if (
            "services" not in context
            and len(context["districts"]) + len(context["localities"]) == 1
        ):
            if not context["localities"]:
                return redirect(context["districts"][0])
            return redirect(context["localities"][0])
        return super().render_to_response(context)


class DistrictDetailView(DetailView):
    """A single district, and the localities in it"""

    model = District
    queryset = model.objects.select_related("admin_area", "admin_area__region")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        stops = StopPoint.objects.filter(active=True)
        context["localities"] = self.object.locality_set.filter(
            Exists(stops.filter(locality=OuterRef("pk")))
            | Exists(stops.filter(locality__parent=OuterRef("pk"))),
        ).defer("latlong")

        context["breadcrumb"] = [self.object.admin_area.region, self.object.admin_area]

        return context

    def render_to_response(self, context):
        if len(context["localities"]) == 1:
            return redirect(context["localities"][0])
        return super().render_to_response(context)


class LocalityDetailView(UppercasePrimaryKeyMixin, DetailView):
    """A single locality, its children (if any), and the stops in it"""

    model = Locality
    queryset = model.objects.select_related(
        "admin_area", "admin_area__region", "district", "parent"
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        stops = StopPoint.objects.filter(active=True)

        has_stops = Exists(stops.filter(locality=OuterRef("pk")))
        has_stops |= Exists(stops.filter(locality__parent=OuterRef("pk")))

        context["localities"] = self.object.locality_set.filter(has_stops).defer(
            "latlong"
        )

        context["adjacent"] = Locality.objects.filter(
            has_stops, adjacent=self.object
        ).defer("latlong")

        context["stops"] = (
            self.object.stoppoint_set.filter(
                service__current=True,
            )
            .annotate(line_names=stop_line_names)
            .order_by("common_name", "indicator")
            .defer("latlong")
        )

        if not (context["localities"] or context["stops"]):
            raise Http404(
                f"Sorry, it looks like no services currently stop at {self.object}"
            )

        if context["stops"]:
            stops = [stop.pk for stop in context["stops"]]
            context["services"] = sorted(
                Service.objects.filter(
                    stops__in=stops,
                    current=True,
                )
                .annotate(operators=operator_names, line_names=stop_line_names)
                .defer("geometry", "search_vector"),
                key=Service.get_order,
            )
            context["modes"] = {
                service.mode for service in context["services"] if service.mode
            }
            context["colours"] = get_colours(context["services"])
        context["breadcrumb"] = [
            crumb
            for crumb in [
                self.object.admin_area.region,
                self.object.admin_area,
                self.object.district,
                self.object.parent,
            ]
            if crumb is not None
        ]

        return context


def get_departures_context(stop, services, form_data, stops=None) -> dict:
    context = {}
    when = None
    form = forms.DeparturesForm(form_data)
    if form.is_valid():
        date = form.cleaned_data["date"]
        time = form.cleaned_data["time"]
        if time is None:
            time = datetime.time()  # 00:00
        when = datetime.datetime.combine(date, time)
    context["when"] = when
    departures = live.get_departures(stop, services, when)
    context.update(departures)

    if context["departures"]:
        context["has_live"] = any(item.get("live") for item in context["departures"])
        context["has_scheduled"] = any(
            item.get("time") for item in context["departures"]
        )
    if context["when"]:
        if len(context["departures"]) < 12:
            context["next_page"] = {
                "date": context["when"].date() + datetime.timedelta(days=1),
                "time": None,
            }
        elif last_time := context["departures"][-1].get("time"):
            context["next_page"] = {
                "date": last_time.date(),
                "time": last_time.time().strftime("%H:%M"),
            }
    else:
        context["when"] = context["now"] = timezone.localtime()

    context["object"] = stop

    # for stop area departures - indicate child stop/platform number
    if stops:
        context["stoparea"] = stop
        for stop in stops:
            if " " in stop.indicator:
                prefix = stop.indicator.split(" ")[0].title()
                context["indicator_prefix"] = prefix  # Stand, Stance, Stop
                break
        if "breadcrumb" in context and stop.locality:
            context["breadcrumb"] += [stop.locality.parent, stop.locality]

        stops_dict = {stop.pk: stop for stop in stops}

        for item in context["departures"]:
            item["stop_time"].stop = stops_dict[item["stop_time"].stop_id]

    return context


stop_line_names = ArrayAgg(
    "stopusage__line_name", distinct=True, ordering="stopusage__line_name", default=None
)
operator_names = ArrayAgg("operator__name", distinct=True, default=None)


class StopPointDetailView(DetailView):
    """A stop, other stops in the same area, and the services servicing it"""

    model = StopPoint
    queryset = model.objects.select_related(
        "admin_area",
        "admin_area__region",
        "locality",
        "locality__parent",
        "locality__district",
        "stop_area",
    )
    queryset = queryset.defer("locality__latlong", "locality__parent__latlong")

    def get_object(self, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        return get_object_or_404(queryset, pk__iexact=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        services = (
            self.object.service_set.filter(current=True)
            .annotate(line_names=stop_line_names, operators=operator_names)
            .defer("geometry", "search_vector")
        )
        context["services"] = sorted(services, key=Service.get_order)

        context["breadcrumb"] = [
            self.object.get_region(),
            self.object.admin_area,
            self.object.locality and self.object.locality.district,
            self.object.locality and self.object.locality.parent,
            self.object.locality,
        ]

        if not (self.object.active or context["services"]):
            return context

        context.update(get_departures_context(self.object, services, self.request.GET))

        text = ", ".join(
            part
            for part in (
                "on " + self.object.street if self.object.street else None,
                "near " + self.object.crossing if self.object.crossing else None,
                "near " + self.object.landmark if self.object.landmark else None,
            )
            if part is not None
        )
        if text:
            context["text"] = f"{text[0].upper()}{text[1:]}"

        context["modes"] = {
            service.mode for service in context["services"] if service.mode
        }
        context["colours"] = get_colours(context["services"])

        nearby = (
            StopPoint.objects.filter(active=True)
            .order_by("common_name", "indicator")
            .filter(service__current=True)
        )

        if self.object.stop_area_id is not None:
            nearby = nearby.filter(stop_area=self.object.stop_area_id)
        elif self.object.locality or self.object.admin_area:
            nearby = nearby.filter(common_name=self.object.common_name)
            if self.object.locality:
                nearby = nearby.filter(locality=self.object.locality)
            else:
                nearby = nearby.filter(admin_area=self.object.admin_area)
                if self.object.town:
                    nearby = nearby.filter(town=self.object.town)
        else:
            nearby = None

        if nearby is not None:
            context["nearby"] = (
                nearby.exclude(pk=self.object.pk)
                .filter(service__current=True)
                .annotate(line_names=stop_line_names)
                .defer("latlong")
            )

            if (
                self.object.stop_area
                and self.object.stop_area.name
                and self.object.stop_area.stop_area_type != "GPBS"
            ):
                # stop area (if it is not an on-street pair)
                context["breadcrumb"].append(self.object.stop_area)

        consequences = Consequence.objects.filter(stops=self.object)
        context["situations"] = (
            Situation.objects.filter(
                publication_window__contains=Now(),
                consequence__stops=self.object,
                current=True,
            )
            .distinct()
            .order_by("publication_window")
            .prefetch_related(
                Prefetch(
                    "consequence_set", queryset=consequences, to_attr="consequences"
                ),
                "link_set",
                "validityperiod_set",
            )
        )

        return context

    def render_to_response(self, context):
        response = super().render_to_response(context)
        if not (self.object.active or context["services"]):
            response.status_code = HTTPStatus.NOT_FOUND
            patch_response_headers(response)
        return response


class StopAreaDetailView(DetailView):
    model = StopArea
    queryset = model.objects.select_related(
        "admin_area",
        "admin_area__region",
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        stops = (
            self.object.stoppoint_set.filter(service__current=True)
            .annotate(line_names=stop_line_names)
            .order_by("common_name", "indicator")
        )
        context["children"] = stops

        services = Service.objects.filter(
            current=True, stops__stop_area=self.object
        ).annotate(line_names=stop_line_names, operators=operator_names)
        context.update(
            get_departures_context(self.object, services, self.request.GET, stops)
        )

        context["breadcrumb"] = [
            self.object.admin_area.region,
            self.object.admin_area,
            self.object.parent,
        ]

        return context


def stop_departures(request, atco_code):
    stop = get_object_or_404(StopPoint, atco_code=atco_code)

    services = stop.service_set.filter(current=True).annotate(operators=operator_names)

    context = get_departures_context(stop, services, request.GET)

    return render(request, "departures.html", context)


def stop_area_departures(request, pk):
    stop_area = get_object_or_404(StopArea, pk=pk)

    services = Service.objects.filter(current=True, stops__stop_area=stop_area)

    stops = stop_area.stoppoint_set.filter(service__current=True)

    context = get_departures_context(stop_area, services, request.GET, stops)

    return render(request, "departures.html", context)


class OperatorDetailView(DetailView):
    "An operator and the services it operates"

    model = Operator
    queryset = model.objects.select_related("region").prefetch_related("licences")

    def get_object(self):
        try:
            return super().get_object()
        except Http404:
            if "slug" in self.kwargs:
                try:
                    return get_object_or_404(
                        self.queryset,
                        operatorcode__code=self.kwargs["slug"],
                        operatorcode__source__name="slug",
                    )
                except Http404:
                    self.kwargs["pk"] = self.kwargs["slug"].upper()
                    return super().get_object()
            raise

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["situations"] = (
            Situation.objects.filter(
                publication_window__contains=Now(),
                consequence__operators=self.object,
                current=True,
            )
            .order_by("publication_window")
            .distinct()
            .prefetch_related(
                Prefetch(
                    "consequence_set",
                    queryset=Consequence.objects.filter(operators=self.object),
                    to_attr="consequences",
                ),
                "link_set",
                "validityperiod_set",
            )
        )

        # services list:

        services = (
            self.object.service_set.with_line_names()
            .filter(current=True)
            .defer("geometry", "search_vector")
        )
        services = services.annotate(start_date=SubqueryMin("route__start_date"))

        if self.object.name == "National Express":
            services = services.annotate(
                group=Case(
                    When(
                        route__code__contains="_Events-",
                        then=Value("Festival & event travel"),
                    ),
                    default=Value(""),
                )
            )

        context["services"] = sorted(services, key=Service.get_order)

        if context["services"]:
            # for 'from {date}' for future services:
            context["today"] = timezone.localdate()

            context["colours"] = get_colours(context["services"])

        context["breadcrumb"] = [self.object.region]

        # this is a bit of a faff,
        # just to avoid doing separate queries
        # for National Operator Codes and MyTrip
        operator_codes = self.object.operatorcode_set.annotate(
            source_name=F("source__name")
        )

        # tickets tab:
        if any(code.source_name == "MyTrip" for code in operator_codes):
            context["tickets_link"] = reverse(
                "operator_tickets", kwargs={"slug": self.object.slug}
            )
        elif self.object.name == "FlixBus":
            context["tickets_link"] = flixbus_affiliate_link(
                clickref="ot",
                ued="https://www.flixbus.co.uk/bus-routes/london-london-stansted-airport",
            )
        elif self.object.name == "Flibco":
            context["tickets_link"] = flibco_affiliate_link(clickref="ot")
        elif self.object.name == "National Express":
            context["tickets_link"] = (
                "https://nationalexpress.prf.hn/click/camref:1011ljPYw"
            )

        context["nocs"] = [
            code.code
            for code in operator_codes
            if code.source_name == "National Operator Codes"
        ]

        # vehicles tab:

        context["vehicles"] = self.object.vehicle_set.filter(
            withdrawn=False, latest_journey__isnull=False
        ).exists()
        if redis_client and (
            context["vehicles"] or any(s.tracking for s in context["services"])
        ):
            try:
                context["map"] = redis_client.exists(
                    f"operator{self.object.noc}vehicles"
                )
            except ConnectionError:
                pass

        return context

    def render_to_response(self, context):
        status_code = None

        if not context["services"] and not context["vehicles"]:
            alternative = Operator.objects.filter(
                operator_has_current_services,
                name=self.object.name,
            ).first()
            if alternative:
                return redirect(alternative)
            # no services or vehicles - render a 404 page that looks like a normal page
            context["ad"] = False
            status_code = HTTPStatus.NOT_FOUND

        response = super().render_to_response(context)
        if status_code:
            response.status_code = status_code
        return response


class ServiceDetailView(DetailView):
    "A service and the stops it stops at"

    model = Service
    queryset = (
        model.objects.with_line_names()
        .select_related("region", "source", "colour")
        .annotate(actual_public_use=Coalesce("public_use", BoolOr("route__public_use")))
        .prefetch_related("operator")
        .defer("search_vector")
    )

    def get_object(self):
        services = Service.objects

        try:
            service = super().get_object()
        except Http404 as e:
            slug = self.kwargs["slug"]

            service = services.filter(service_code=slug).first()

            if not service:
                service = services.filter(
                    servicecode__scheme="slug", servicecode__code=slug
                ).first()

            if not service:
                service = services.filter(
                    servicecode__scheme="ServiceCode", servicecode__code=slug
                ).first()

            if not service:
                raise e

        if not service.current:
            alternative = None

            services = services.only("slug", "current").filter(current=True)
            operators = service.operator.all()

            if service.line_name:
                if operators:
                    alternative = services.filter(
                        line_name__iexact=service.line_name,
                        operator__in=operators,
                        stops__service=service,
                    ).first()
                if not alternative:
                    alternative = services.filter(
                        line_name__iexact=service.line_name,
                        stops__service=service,
                    ).first()
                if not alternative and operators:
                    alternative = services.filter(
                        line_name__iexact=service.line_name,
                        operator__in=service.operator.all(),
                    ).first()

            if not alternative and service.description:
                alternative = services.filter(description=service.description).first()

            if not alternative and operators:
                alternative = operators[0]

            if alternative:
                return alternative

            raise Http404()

        return service

    def get_fare_tables(self):
        fare_tables = (
            FareTable.objects.filter(
                tariff__services=self.object,
                tariff__source__published=True,
            )
            .select_related("tariff", "user_profile", "sales_offer_package")
            .order_by("tariff")
        )
        if fare_tables:
            for table in fare_tables:
                table.tariff.name = (
                    table.tariff.name.removesuffix(" fares")
                    .replace(" Conc ", " Concession ")
                    .replace(" YP ", " Young Person ")
                    .replace(" Ch ", " Child ")
                    .replace("_", " ")
                    .replace(" AD ", " Adult ")
                )

            if not all(
                table.user_profile == fare_tables[0].user_profile
                for table in fare_tables[1:]
            ):
                for table in fare_tables:
                    table.tariff.name = f"{table.tariff.name} - {table.user_profile} {table.tariff.trip_type}"
            if not all(
                table.sales_offer_package == fare_tables[0].sales_offer_package
                for table in fare_tables[1:]
            ):
                for table in fare_tables:
                    table.tariff.name = (
                        f"{table.tariff.name} - {table.sales_offer_package}"
                    )

            if not all(
                table.tariff.name == fare_tables[0].tariff.name
                for table in fare_tables[1:]
            ):
                parts = fare_tables[0].tariff.name.split()
                while all(
                    table.tariff.name.startswith(f"{parts[0]} ")
                    for table in fare_tables
                ):
                    for table in fare_tables:
                        table.tariff.name = table.tariff.name.removeprefix(
                            f"{parts[0]} "
                        )
                    parts = parts[1:]
            return fare_tables

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if (
            type(self.object) is not self.model
            or self.object.slug != self.kwargs["slug"]
        ):
            return {"redirect_to": self.object}

        operators = self.object.operator.all()
        context["operators"] = operators

        context["related"] = self.object.get_similar_services()
        if context["related"]:
            context["colours"] = get_colours(
                [
                    service
                    for service in context["related"]
                    if service.colour_id != self.object.colour_id
                ]
            )

        # timetable

        date = None

        if not self.object.timetable_wrong:
            form = forms.TimetableForm(
                self.request.GET or None,
                service=self.object,
                related=context["related"],
            )

            context["timetable"] = form.get_timetable(self.object)

            context["form"] = form

            if form.is_valid():
                date = form.cleaned_data.get("date")

                # date in past - redirect to today?
                if (
                    date
                    and not (
                        context["timetable"].calendars
                        and context["timetable"].calendar_ids
                    )
                    and date < timezone.localdate()
                    and not form.cleaned_data["detailed"]
                ):
                    return {"redirect_to": self.object}

            context["registrations"] = Registration.objects.filter(
                Exists(self.object.route_set.filter(registration=OuterRef("id")))
            )

        if self.object.tracking and self.object.vehiclejourney_set.exists():
            context["vehicles"] = True
            if redis_client:
                context["tracking_count"] = redis_client.scard(
                    f"service{self.object.id}vehicles"
                )

        # disruptions

        consequences = Consequence.objects.filter(
            Q(services=self.object) | (Q(operators__in=operators, services=None))
        )
        context["situations"] = (
            Situation.objects.filter(
                Exists(consequences.filter(situation=OuterRef("id"))),
                publication_window__contains=Now(),
                current=True,
            )
            .order_by("publication_window")
            .prefetch_related(
                Prefetch(
                    "consequence_set",
                    queryset=consequences.prefetch_related("stops"),
                    to_attr="consequences",
                ),
                "link_set",
                "validityperiod_set",
            )
            .defer("data")
        )
        # stop_situations = {}
        # for situation in context["situations"]:
        #     for consequence in situation.consequences:
        #         for stop in consequence.stops.all():
        #             stop_situations[stop.atco_code] = situation

        context["stopusages"] = self.object.stopusage_set.select_related(
            "stop__locality"
        ).defer("stop__latlong", "stop__locality__latlong")
        context["has_minor_stops"] = SimpleLazyObject(
            lambda: (
                not all(stop_usage.timing_point for stop_usage in context["stopusages"])
            )
        )

        #     if len(stop_situations) < len(context["stopusages"]):
        #         for stop_usage in context["stopusages"]:
        #             if stop_usage.stop_id in stop_situations:
        #                 if (
        #                     stop_situations[stop_usage.stop_id].summary
        #                     == "Does not stop here"
        #                 ):
        #                     stop_usage.suspended = True
        #                 else:
        #                     stop_usage.situation = True

        try:
            context["breadcrumb"] = [
                Region.objects.filter(adminarea__stoppoint__service=self.object)
                .distinct()
                .get()
            ]
        except (Region.DoesNotExist, Region.MultipleObjectsReturned):
            context["breadcrumb"] = [self.object.region]

        context["links"] = []

        if operators:
            operator = operators[0]
            context["breadcrumb"].append(operator)
            context["payment_methods"] = []

            if operator.operatorcode_set.filter(source__name="MyTrip").exists():
                context["app"] = {
                    "url": reverse("operator_tickets", kwargs={"slug": operator.slug}),
                    "name": "MyTrip app",
                }
            for method in PaymentMethod.objects.filter(
                Exists(
                    Service.payment_methods.through.objects.filter(
                        payment_method=OuterRef("id"),
                        service=self.object,
                        accepted=True,
                    )
                )
                | Exists(
                    Operator.payment_methods.through.objects.filter(
                        paymentmethod=OuterRef("id"),
                        operator=operator,
                    )
                ),
                ~Exists(
                    Service.payment_methods.through.objects.filter(
                        payment_method=OuterRef("id"),
                        service=self.object,
                        accepted=False,
                    )
                ),
            ):
                if "app" in method.name and method.url:
                    context["app"] = method
                else:
                    context["payment_methods"].append(method)
            for operator in operators:
                if operator.name == "National Express":
                    context["tickets_link"] = (
                        f"https://nationalexpress.prf.hn/click/camref:1011ljPYw/pubref:{self.object.line_name}"
                    )
                    context["links"].append(
                        {
                            "url": context["tickets_link"],
                            "text": "Buy tickets at National Express",
                        }
                    )
                    break
                elif operator.name == "Flibco":
                    context["tickets_link"] = flibco_affiliate_link()
                    context["links"].append(
                        {
                            "url": context["tickets_link"],
                            "text": "Buy tickets at Flibco",
                        }
                    )
                    break
                elif (
                    operator.name == "FlixBus"
                    or self.object.service_code == "PF0000508:488"
                ):
                    query = {"clickref": self.object.line_name}
                    if context["breadcrumb"][0].name == "Scotland":
                        query["ued"] = "https://www.flixbus.co.uk/scotland"
                    elif self.object.service_code == "PF0000508:488":  # Green Line 757
                        query["ued"] = (
                            "https://www.flixbus.co.uk/coach/london-luton-airport"
                        )
                    context["tickets_link"] = flixbus_affiliate_link(**query)
                    context["links"].append(
                        {
                            "url": context["tickets_link"],
                            "text": "Buy tickets at FlixBus",
                        }
                    )
                    break
        context["fare_tables"] = self.get_fare_tables()

        for url, text in self.object.get_traveline_links(date):
            context["links"].append({"url": url, "text": text})

        return context

    def render_to_response(self, context):
        if "redirect_to" in context:
            return redirect(
                context["redirect_to"],
                permanent=(type(context["redirect_to"]) is self.model),
            )

        if not (settings.TEST or "debug_toolbar" in self.request.GET):
            template = get_template("busstops/service_detail.html")
            generator = template.template.generate(
                **context, ad=True, request=self.request
            )
            return StreamingHttpResponse(
                generator,
                content_type="text/html",
            )

        return super().render_to_response(context)


def service_timetable(request, service_id):
    services = Service.objects.with_line_names().defer("geometry", "search_vector")
    service = get_object_or_404(services, id=service_id)
    related_options = service.get_similar_services()
    form = forms.TimetableForm(request.GET, service=service, related=related_options)

    context = {
        "object": service,
        "timetable": form.get_timetable(service),
        "related": related_options,
        "form": form,
    }

    return render(request, "timetable.html", context)


def service_timetable_csv(request, service_id):
    services = Service.objects.with_line_names().defer("geometry", "search_vector")
    service = get_object_or_404(services, id=service_id)
    form = forms.TimetableForm(request.GET, service=service, related=None)

    response = HttpResponse(
        content_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={service.slug}.csv"},
    )
    writer = csv.writer(response)

    for grouping in form.get_timetable(service).render().groupings:
        writer.writerow(
            ["stop", "NaPTAN code", "ATCO code"]
            + [trip.route.line_name for trip in grouping.trips]
        )
        for row in grouping.rows:
            if type(row.stop) is StopPoint:
                stop = [
                    row.stop.get_qualified_name(),
                    row.stop.naptan_code,
                    row.stop.atco_code,
                ]
            else:
                stop = [
                    str(row.stop),
                    "",
                    "",
                ]
            writer.writerow(stop + row.times)
        writer.writerow(())
    return response


def service_last_modified(request, service_id):
    service = get_object_or_404(
        Service.objects.only("geometry", "line_name", "service_code", "modified_at"),
        id=service_id,
    )
    request.service = service
    return service.modified_at


@last_modified(service_last_modified)
def service_map_data(request, service_id):
    service = request.service
    stops = service.stops.filter(
        #     ~Exists(
        #         Situation.objects.filter(
        #             summary="Does not stop here",
        #             consequence__stops=OuterRef("pk"),
        #             consequence__services=service,
        #         )
        #     ),
        latlong__isnull=False,
    ).annotate(line_names=stop_line_names)
    stops = stops.distinct().order_by().select_related("locality").in_bulk()
    data = {
        "stops": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": stop.latlong.coords,
                    },
                    "properties": {
                        "name": stop.get_qualified_name(),
                        "bearing": stop.get_heading(),
                        "url": stop.get_absolute_url(),
                        "services": stop.line_names,
                    },
                }
                for stop in stops.values()
            ],
        },
    }

    if service.geometry and service.geometry.geom_type[-10:] == "LineString":
        data["geometry"] = {
            "type": service.geometry.geom_type,
            "coordinates": service.geometry.coords,
        }
    else:
        route_links = {
            (route_link.from_stop_id, route_link.to_stop_id): route_link
            for route_link in service.routelink_set.all()
        }

        # in theory the use of `distinct` might exclude some stops, but it's worth it
        trips = Trip.objects.filter(route__service=service).distinct(
            "destination", "journey_pattern", "inbound"
        )
        stop_times = (
            StopTime.objects.filter(trip__in=trips)
            .order_by("trip_id", "id")
            .values_list("trip_id", "stop_id")
        )
        # build pairs of consecutive stops
        pairs = set()

        for _, group in groupby(stop_times, key=lambda x: x[0]):  # group by trip_id
            previous_stop_id = None
            for _, stop_id in group:
                if previous_stop_id:
                    pairs.add((previous_stop_id, stop_id))
                previous_stop_id = stop_id

        line_string = []
        multi_line_string = [line_string]

        previous_pair = None

        for pair in pairs:
            line_string = []
            multi_line_string.append(line_string)

            origin, destination = pair
            if previous_pair and line_string and previous_pair[1] != origin:
                line_string = []
                multi_line_string.append(line_string)
            if pair in route_links:
                line_string += route_links[pair].geometry.coords
            elif origin in stops and destination in stops:
                origin = stops[origin]
                destination = stops[destination]
                if origin.latlong and destination.latlong:
                    line_string += [
                        origin.latlong.coords,
                        destination.latlong.coords,
                    ]

            previous_pair = pair

        data["geometry"] = {"type": "MultiLineString", "coordinates": multi_line_string}

    return JsonResponse(data)


class OperatorSitemap(Sitemap):
    protocol = "https"

    def items(self):
        return (
            Operator.objects.filter(operator_has_current_services_or_vehicles)
            .annotate(
                lastmod=Coalesce(
                    SubqueryMax("service__modified_at", filter=Q(current=True)),
                    "modified_at",
                )
            )
            .only("slug")
            .order_by("noc")
        )

    def lastmod(self, obj):
        return obj.lastmod


class ServiceSitemap(Sitemap):
    protocol = "https"

    def items(self):
        return Service.objects.filter(current=True).only("slug", "modified_at")

    def lastmod(self, obj):
        return obj.modified_at


def search(request):
    form = forms.SearchForm(request.GET)

    context = {
        "form": form,
    }

    if form.is_valid():
        query_text = form.cleaned_data["q"]
        context["query"] = query_text

        postcode = "".join(query_text.split()).upper()
        if validation.is_valid_postcode(postcode):
            res = requests.get(
                "https://api.postcodes.io/postcodes/" + postcode, timeout=1
            )
        elif validation.is_valid_partial_postcode(postcode):
            res = requests.get(
                "https://api.postcodes.io/outcodes/" + postcode, timeout=1
            )
        else:
            postcode = None

        if postcode and res.ok:
            result = res.json()["result"]

            if result["longitude"] is not None:
                point = Point(result["longitude"], result["latitude"], srid=4326)

                context["postcode"] = {
                    "postcode": result.get("postcode") or result.get("outcode"),
                    "latlong": point,
                    "localities": (
                        Locality.objects.filter(latlong__bboverlaps=point.buffer(0.05))
                        .filter(
                            Q(stoppoint__active=True)
                            | Q(locality__stoppoint__active=True)
                        )
                        .distinct()
                        .annotate(distance=Distance("latlong", point))
                        .order_by("distance")
                        .defer("latlong")[:20]
                    ),
                }

        if "postcode" not in context or not result.get("postcode"):
            query = SearchQuery(query_text, search_type="websearch", config="english")
            rank = SearchRank(F("search_vector"), query)

            localities = Locality.objects.filter(
                Exists("stoppoint") | Exists("locality")
            )
            operators = Operator.objects.filter(
                operator_has_current_services_or_vehicles
            )
            services = Service.objects.with_line_names().filter(current=True)

            services = services.annotate(operators=operator_names)

            for key, queryset in (
                ("localities", localities),
                ("operators", operators),
                ("services", services),
            ):
                if key == "services" and len(query_text) <= 3:
                    # search for line names like "A" which the full text search would ignore
                    queryset = queryset.filter(
                        Q(search_vector=query) | Q(route__line_name__iexact=query_text)
                    )
                else:
                    queryset = queryset.filter(search_vector=query)

                queryset = queryset.annotate(rank=rank).order_by("-rank")

                if key == "operators" or key == "localities":
                    queryset = queryset.annotate(
                        headline=SearchHeadline("name", query, config="english")
                    )
                elif key == "services":
                    queryset = queryset.annotate(
                        headline=SearchHeadline("description", query, config="english")
                    )
                context[key] = Paginator(queryset, 20).get_page(request.GET.get("page"))

            vehicles = Vehicle.objects.select_related("operator")
            query_text = query_text.replace(" ", "")
            if len(query_text) >= 4:
                if query_text.isdigit():
                    context["vehicles"] = vehicles.filter(fleet_code__iexact=query_text)
                elif not query_text.isalpha():
                    context["vehicles"] = vehicles.filter(
                        Q(reg__iexact=query_text)
                        | Q(**{"data__Previous reg__icontains": query_text})
                    )

            if context.get("services"):
                context["colours"] = get_colours(context["services"])

    return render(request, "search.html", context)


def journey(request):
    origin = request.GET.get("from")
    from_q = request.GET.get("from_q")
    destination = request.GET.get("to")
    to_q = request.GET.get("to_q")

    if origin:
        origin = get_object_or_404(Locality, slug=origin)
    if from_q:
        query = SearchQuery(from_q)
        rank = SearchRank(F("search_vector"), query)
        from_options = (
            Locality.objects.filter(search_vector=query)
            .annotate(rank=rank)
            .order_by("-rank")
        )
        if len(from_options) == 1:
            origin = from_options[0]
            from_options = None
        elif origin not in from_options:
            origin = None
    else:
        from_options = None

    if destination:
        destination = get_object_or_404(Locality, slug=destination)
    if to_q:
        query = SearchQuery(to_q)
        rank = SearchRank(F("search_vector"), query)
        to_options = (
            Locality.objects.filter(search_vector=query)
            .annotate(rank=rank)
            .order_by("-rank")
        )
        if len(to_options) == 1:
            destination = to_options[0]
            to_options = None
        elif destination not in to_options:
            destination = None
    else:
        to_options = None

    journeys = None
    # if origin and destination:
    #     journeys = Journey.objects.filter(
    #         stopusageusage__stop__locality=origin
    #     ).filter(stopusageusage__stop__locality=destination)
    # else:
    #     journeys = None

    return render(
        request,
        "journey.html",
        {
            "from": origin,
            "from_q": from_q or origin or "",
            "from_options": from_options,
            "to": destination,
            "to_q": to_q or destination or "",
            "to_options": to_options,
            "journeys": journeys,
        },
    )
