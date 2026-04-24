import datetime
import json
import logging
from itertools import pairwise, groupby
from urllib.parse import unquote
from functools import partial
from http import HTTPStatus
import subprocess
import xmltodict
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.gis.geos import GEOSException
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, BadRequest
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import Case, F, Max, OuterRef, Q, When, Value
from django.db.models.aggregates import StringAgg
from django.db.models.functions import Coalesce, Now
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import get_conditional_response, set_response_etag
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe
from django.utils.decorators import method_decorator
from django.views.generic.detail import DetailView
import numpy as np
from haversine import Unit, haversine, haversine_vector
from redis.exceptions import ConnectionError
from sql_util.utils import Exists, SubqueryMax, SubqueryMin

from accounts.models import User
from busstops.models import (
    SERVICE_ORDER_REGEX,
    Operator,
    OperatorGroup,
    Service,
    StopUsage,
)
from busstops.utils import get_bounding_box
from bustimes.models import Garage, Route, StopTime
from bustimes.utils import contiguous_stoptimes_only, get_other_trips_in_block
from photos.forms import PhotoForm
from photos.utils import add_flickr_photo

from . import filters, forms
from .management.commands import import_bod_avl
from .models import (
    Livery,
    SiriSubscription,
    Vehicle,
    VehicleJourney,
    VehicleLocation,
    VehicleRevision,
    VehicleRevisionFeature,
)
from .rtpi import add_progress_and_delay
from .tasks import handle_siri_post
from .utils import apply_revision, get_revision, redis_client  # calculate_bearing,


def get_redirect_view(*args, **kwargs):
    def redirect_view(request):
        return redirect(*args, **kwargs)

    return redirect_view


class Vehicles:
    """for linking to an operator's /vehicles page (fleet list) in a breadcrumb list"""

    def __init__(self, vehicle=None, operator=None):
        self.vehicle = vehicle
        self.operator = operator or vehicle.operator

    def __str__(self):
        return "Vehicles"

    def get_absolute_url(self):
        url = reverse("operator_vehicles", args=(self.operator.slug,))
        if self.vehicle:
            url = f"{url}#{self.vehicle.slug}"
        return url


@require_safe
def vehicles(request):
    """index of recently AVL-enabled operators, etc"""

    operators = Operator.objects.filter(
        Exists("vehicle", filter=Q(withdrawn=False))
    ).only("name", "slug")

    new_operators = operators.annotate(
        min=SubqueryMin("vehicle__id"),
    ).order_by("-min")[:36]

    operator_journeys = VehicleJourney.objects.filter(
        latest_vehicle__operator=OuterRef("noc")
    )

    day_ago = timezone.now() - datetime.timedelta(days=1)
    status = (
        operators.filter(
            Exists(operator_journeys),
            ~Exists(operator_journeys.filter(datetime__gte=day_ago)),
        )
        .annotate(
            last_seen=SubqueryMax("vehicle__latest_journey__datetime"),
        )
        .order_by("-last_seen")
    )

    return render(
        request,
        "vehicles.html",
        {
            "status": list(status),
            "new_operators": list(new_operators),
            "operators": list(operators),
        },
    )


@cache_control(max_age=3600)
def liveries_css(request, version=0):
    styles = []
    liveries = Livery.objects.filter(published=True).order_by("left_css")
    for _, liveries in groupby(liveries, lambda livery: livery.right_css):
        liveries = list(liveries)
        styles += liveries[0].get_styles([livery.id for livery in liveries])
    styles = "".join(styles)
    completed_process = subprocess.run(
        ["lightningcss", "--minify"], input=styles.encode(), capture_output=True
    )
    styles = completed_process.stdout
    return HttpResponse(styles, content_type="text/css")


features_string_agg = StringAgg(
    "features__name", Value(", "), order_by=["features__name"], default=""
)


def get_vehicle_order(vehicle) -> tuple[str, int, str]:
    if vehicle.notes == "Spare ticket machine":
        return ("", vehicle.fleet_number or 99999, vehicle.code)

    if vehicle.fleet_number:
        return ("", vehicle.fleet_number)

    # age-based ordering
    if not vehicle.fleet_code and len(reg := vehicle.reg) == 7 and reg[-3:].isalpha():
        if reg[:2].isalpha() and reg[2:4].isdigit():
            year = int(reg[2:4])
            if year > 50:
                return ("Z", (year - 50) * 2 + 1, "")  # year 64 (september 2014) - 29
            return ("Z", year * 2, "")  # year 14 (march 2014) - 28

        if reg[1:4].isdigit():
            return reg[0], int(reg[1:4]), reg[-3:]

    prefix, number, suffix = SERVICE_ORDER_REGEX.match(
        vehicle.fleet_code or vehicle.code
    ).groups()
    number = int(number) if number else 0
    if " " in prefix:  # McGill's
        return (suffix, number, prefix)
    return (prefix, number, suffix)


@require_safe
def operator_vehicles(request, slug=None, group_slug=None):
    """fleet list"""

    operators = Operator.objects.select_related("region", "group")
    if group_slug:
        try:
            group = OperatorGroup.objects.get(slug=group_slug)
        except OperatorGroup.DoesNotExist:
            # cool URIs don't change
            group = get_object_or_404(OperatorGroup, name=group_slug)
        operators = group.operator_set.in_bulk()
        vehicles = Vehicle.objects.filter(operator__group=group)
    elif slug:
        group = None
        try:
            operator = operators.get(slug=slug.lower())
        except Operator.DoesNotExist:
            operator = get_object_or_404(
                operators, operatorcode__code=slug, operatorcode__source__name="slug"
            )
        vehicles = operator.vehicle_set

    if "withdrawn" not in request.GET:
        vehicles = vehicles.filter(withdrawn=False)

    vehicles = vehicles.order_by("fleet_number", "fleet_code", "reg", "code")

    if group_slug:
        context = {"object": group}
    else:
        vehicles = vehicles.annotate(feature_names=features_string_agg)
        vehicles = vehicles.annotate(
            pending_edits=Exists("vehiclerevision", filter=Q(pending=True))
        )
        vehicles = vehicles.select_related("latest_journey")

        context = {
            "object": operator,
            "breadcrumb": [operator.group or operator.region, operator],
        }

    vehicles = vehicles.annotate(
        livery_name=Case(When(livery__show_name=True, then="livery__name")),
        vehicle_type_name=F("vehicle_type__name"),
        garage_name=Case(
            When(garage__name="", then="garage__code"),
            default="garage__name",
        ),
    )

    if not vehicles:
        raise Http404

    vehicles = sorted(vehicles, key=get_vehicle_order)
    if not group and operator.noc in settings.ALLOW_VEHICLE_NOTES_OPERATORS:
        vehicles = sorted(vehicles, key=lambda v: v.notes)

    if group:
        paginator = Paginator(vehicles, 1000)
        page = request.GET.get("page")
        vehicles = paginator.get_page(page)

        for v in vehicles:
            v.operator = operators[v.operator_id]
            v.operator_name = v.operator.name.removeprefix(f"{group} ")

        context["paginator"] = paginator
    else:
        paginator = None

        context["features_column"] = any(vehicle.feature_names for vehicle in vehicles)

    columns = set(key for vehicle in vehicles if vehicle.data for key in vehicle.data)
    for vehicle in vehicles:
        vehicle.column_values = [
            vehicle.data and vehicle.data_get(key) or "" for key in columns
        ]
    context["columns"] = columns

    if not group:
        now = timezone.localtime()

        # midnight or 12 hours ago, whichever happened first
        if now.hour >= 12:
            today = now - datetime.timedelta(hours=now.hour, minutes=now.minute)
            today = today.replace(second=0, microsecond=0)
        else:
            today = now - datetime.timedelta(hours=12)

        context["today"] = today

        for vehicle in vehicles:
            if vehicle.latest_journey:
                when = vehicle.latest_journey.datetime
                vehicle.last_seen = {
                    "service": vehicle.latest_journey.route_name,
                    "when": when,
                    "today": when >= today,
                }

        context["map"] = any(
            hasattr(vehicle, "last_seen") and vehicle.last_seen["today"]
            for vehicle in vehicles
        )

    garage_names = set(
        vehicle.garage_name for vehicle in vehicles if vehicle.garage_name
    )

    context = {
        **context,
        "parent": group,
        "vehicles": vehicles,
        "branding_column": any(vehicle.branding for vehicle in vehicles),
        "name_column": any(vehicle.name for vehicle in vehicles),
        "notes_column": any(
            vehicle.notes and not vehicle.is_spare_ticket_machine()
            for vehicle in vehicles
        ),
        "garage_column": len(garage_names) > 1,
    }

    return render(request, "operator_vehicles.html", context)


@require_safe
def operator_map(request, slug):
    operator = get_object_or_404(Operator.objects.select_related("region"), slug=slug)

    return render(
        request,
        "operator_map.html",
        {
            "object": operator,
            "operator": operator,
            "breadcrumb": [operator.region, operator],
        },
    )


def operator_debug(request, slug):
    operator = get_object_or_404(Operator, slug=slug)

    services = operator.service_set.filter(current=True)

    services = services.annotate(
        current_routes=Exists(
            Route.objects.filter(
                Q(end_date=None) | Q(end_date__gte=Now()), service=OuterRef("id")
            )
        )
    )

    pipe = redis_client.pipeline(transaction=False)
    for service in services:
        pipe.exists(f"service{service.id}vehicles")
    tracking = pipe.execute()

    for service, service_tracking in zip(services, tracking):
        service.last_tracked = service_tracking

    return render(
        request,
        "operator_debug.html",
        {
            "object": operator,
            "breadcrumb": [operator],
            "services": services,
        },
    )


def respond_conditionally(request, response):
    if not response.has_header("ETag"):
        set_response_etag(response)

    etag = response.get("ETag")
    return get_conditional_response(
        request,
        etag=etag,
        response=response,
    )


@require_safe
def vehicles_json(request) -> JsonResponse:
    try:
        bounds = get_bounding_box(request)
    except KeyError:
        bounds = None
    except (GEOSException, ValueError):
        raise BadRequest

    vehicle_ids = None
    set_names = None
    service_ids = None
    operator_ids = None

    if bounds is not None:
        # ids of vehicles within box
        xmin, ymin, xmax, ymax = bounds.extent

        try:
            # convert to kilometres (only for Redis to convert back to degrees)
            width = haversine((ymin, xmax), (ymin, xmin))
            height = haversine((ymin, xmax), (ymax, xmax))
        except ValueError:
            raise BadRequest

        vehicle_ids = redis_client.geosearch(
            "vehicle_location_locations",
            longitude=str((xmax + xmin) / 2),
            latitude=str((ymax + ymin) / 2),
            unit="km",
            width=str(width),
            height=str(height),
        )

    elif "service" in request.GET:
        try:
            service_ids = [
                int(service_id) for service_id in request.GET["service"].split(",")
            ]
        except ValueError:
            raise BadRequest
        set_names = [f"service{service_id}vehicles" for service_id in service_ids]
    elif "operator" in request.GET:
        operator_ids = request.GET["operator"].split(",")
        set_names = [f"operator{operator_id}vehicles" for operator_id in operator_ids]
    elif "id" in request.GET:
        # specified vehicle ids
        vehicle_ids = request.GET["id"].split(",")
    else:
        # ids of all vehicles
        vehicle_ids = redis_client.zrange("vehicle_location_locations", 0, -1)

    if set_names:
        vehicle_ids = list(redis_client.sunion(set_names))

    vehicle_ids = [int(vehicle_id) for vehicle_id in vehicle_ids]

    vehicle_ids.sort()  # for etag stableness

    vehicle_locations = redis_client.mget(
        [f"vehicle{vehicle_id}" for vehicle_id in vehicle_ids]
    )
    vehicle_locations = [
        json.loads(item) if item else item for item in vehicle_locations
    ]

    # remove expired items from 'vehicle_location_locations'
    to_remove = [
        vehicle_id
        for vehicle_id, item in zip(vehicle_ids, vehicle_locations)
        if not item
    ]

    if to_remove:
        redis_client.zrem("vehicle_location_locations", *to_remove)

    journeys = cache.get_many(
        [f"journey{item['journey_id']}" for item in vehicle_locations if item]
    )

    # get vehicles from the database IF they have unexpired locations AND weren't in the cache
    try:
        vehicles = (
            Vehicle.objects.select_related("vehicle_type")
            .annotate(
                feature_names=features_string_agg,
                service_line_name=F("latest_journey__trip__route__line_name"),
                service_slug=F("latest_journey__service__slug"),
                colour=F("livery__colour"),
            )
            .defer("data", "latest_journey_data")
        ).in_bulk(
            [
                vehicle_id
                for vehicle_id, item in zip(vehicle_ids, vehicle_locations)
                if item and f"journey{item['journey_id']}" not in journeys
            ]
        )
    except OperationalError:
        vehicles = {}

    locations = []

    trip = request.GET.get("trip")
    if trip:
        trip = int(trip)

    journeys_to_cache_later = {}

    for vehicle_id, item in zip(vehicle_ids, vehicle_locations):
        if item:
            journey_cache_key = f"journey{item['journey_id']}"

            if journey_cache_key in journeys:
                item.update(journeys[journey_cache_key])
            elif vehicles:
                try:
                    vehicle = vehicles[vehicle_id]
                except KeyError:
                    continue  # vehicle was deleted?
                else:
                    journey = {"vehicle": vehicle.get_json()}
                    if vehicle.service_slug:
                        journey["service"] = {
                            "url": f"/services/{vehicle.service_slug}",
                            "line_name": vehicle.service_line_name
                            or item.get("service")
                            and item["service"]["line_name"],
                        }
                    if vehicle.latest_journey_id == item["journey_id"]:
                        journeys_to_cache_later[journey_cache_key] = journey
                    else:
                        logging.warning(
                            f"{vehicle=} {vehicle.latest_journey_id=} {item['journey_id']=}"
                        )
                    item.update(journey)

            if (
                "progress" not in item
                and "trip_id" in item
                and (len(vehicle_ids) == 1 or trip and item["trip_id"] == trip)
            ):
                add_progress_and_delay(item)

        if (
            service_ids
            and (not item or item.get("service_id") not in service_ids)
            or operator_ids
            and not item
        ):
            for set_name in set_names:
                redis_client.srem(set_name, vehicle_id)
        elif item:
            locations.append(item)

    if journeys_to_cache_later:
        cache.set_many(journeys_to_cache_later, 3600)  # an hour

    response = JsonResponse(locations, safe=False)

    return respond_conditionally(request, response)


def get_dates(vehicle=None, service=None):
    if not vehicle:
        # the database query for a service is too slow
        return

    journeys = vehicle.vehiclejourney_set

    dates = (
        journeys.filter(date__isnull=False)
        .values_list("date", flat=True)
        .order_by("date")
        .distinct()
    )

    return list(dates)


def journeys_list(request, journeys, service=None, vehicle=None) -> dict:
    """list of VehicleJourneys (and dates) for a service or vehicle"""

    if vehicle and vehicle.latest_journey:
        last_date = vehicle.latest_journey.date
        dates = cache.get_or_set(
            f"vehicle{vehicle.id}dates{last_date}",
            partial(get_dates, vehicle=vehicle),
            timeout=86400,
        )
    else:
        dates = get_dates(vehicle=vehicle, service=service)

    context = {}

    form = forms.DateForm(request.GET)
    if form.is_valid():
        date = form.cleaned_data["date"]
    else:
        date = None

    if not date and dates is None:
        if vehicle and vehicle.latest_journey:
            date = last_date
        else:
            date = journeys.aggregate(max_date=Max("date"))["max_date"]

    if dates:
        context["dates"] = dates
        if not date:
            date = context["dates"][-1]

    if date:
        context["date"] = date

        journeys = journeys.filter(date=date).select_related("trip").order_by("id")

        if dates:
            if date not in dates:
                dates.append(date)
                dates.sort()

        context["journeys"] = journeys

    elif service:
        raise Http404

    if not date or not journeys:
        return context

    context["journeys"] = journeys = list(journeys)

    # annotate journeys with whether each one has some location history in redis
    # (in order to show the "Map" link or not)
    if redis_client:
        try:
            pipe = redis_client.pipeline(transaction=False)
            for journey in journeys:
                pipe.exists(journey.get_redis_key())

            locations = pipe.execute()
        except (ConnectionError, AttributeError):
            pass
        else:
            for journey, location in zip(journeys, locations):
                journey.locations = bool(location)

    # "Track this bus" button
    if vehicle and vehicle.latest_journey_id:
        if redis_client and redis_client.get(f"vehicle{vehicle.id}"):
            context["tracking"] = f"#journeys/{vehicle.latest_journey_id}"

        # predict next workings
        if vehicle.latest_journey_id == journeys[-1].pk:
            trips = [journey.trip for journey in journeys if journey.trip]
            if trips:
                last_trip = trips[-1]
                if last_trip.block and all(
                    trip.block == last_trip.block for trip in trips[-3:-1]
                ):
                    context["predictions"] = (
                        get_other_trips_in_block(
                            last_trip,
                            date,
                        )
                        .filter(
                            start__gte=last_trip.end,
                        )
                        .annotate(
                            destination_name=Coalesce(
                                "headsign",
                                "destination__locality__name",
                                "destination__common_name",
                            ),
                            line_name=F("route__line_name"),
                        )
                    )
                    for a, b in pairwise(context["predictions"]):
                        if a.end > b.start:
                            del context["predictions"]
                            break

    return context


@require_safe
def service_vehicles_history(request, slug=None, noc=None, line_name=None):
    if slug:
        service: Service = get_object_or_404(
            Service.objects.with_line_names(), slug=slug
        )
        operator = service.operator.first()
        journeys = service.vehiclejourney_set
    else:
        service = None
        operator = get_object_or_404(Operator, noc=noc)
        journeys = VehicleJourney.objects.filter(
            service=None, route_name=line_name, vehicle__operator=operator
        )

    context = journeys_list(
        request, journeys.select_related("vehicle"), service=service
    )

    if not context:
        raise Http404

    if service:
        context["garages"] = Garage.objects.filter(
            trip__route__service=service
        ).distinct()
        context["title"] = f"Vehicles \u2013 {service.get_line_name_and_brand()}"
    else:
        context["title"] = f"Vehicles \u2013 {line_name}"

    return render(
        request,
        "vehicles/vehicle_detail.html",
        {
            **context,
            "breadcrumb": [operator, service],
            "object": service or line_name,
        },
    )


class VehicleDetailView(DetailView):
    model = Vehicle
    queryset = model.objects.select_related(
        "operator", "operator__region", "vehicle_type", "livery", "latest_journey"
    ).prefetch_related("features", "photo_set")

    def get_object(self, **kwargs):
        try:
            return super().get_object(**kwargs)
        except Http404:
            if slug := self.kwargs.get("slug"):
                return get_object_or_404(
                    self.queryset, vehiclecode__code=slug, vehiclecode__scheme="slug"
                )
            raise

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        journeys = self.object.vehiclejourney_set.select_related("service")
        journeys = journeys.annotate(
            line_name=Coalesce("trip__route__line_name", "route_name")
        )

        context = {
            **context,
            **journeys_list(self.request, journeys, vehicle=self.object),
        }
        del journeys

        if self.object.reg:
            # for search engine purposes, use reg without space:
            context["title"] = self.object.reg
            if self.object.fleet_code:
                context["title"] = self.object.fleet_code + " - " + context["title"]
        else:
            context["title"] = str(self.object)

        if "journeys" in context:
            garages = set(
                journey.trip.garage_id
                for journey in context["journeys"]
                if journey.trip and journey.trip.garage_id
            )
            if len(garages) == 1:
                context["garage"] = Garage.objects.get(id=garages.pop())

        if self.object.withdrawn and self.object.reg:
            context["potential_duplicates"] = Vehicle.objects.filter(
                ~Q(id=self.object.id), reg__iexact=self.object.reg
            )

        if self.object.operator:
            context["breadcrumb"] = [
                self.object.operator,
                Vehicles(vehicle=self.object),
            ]

            context["previous"] = self.object.get_previous()
            context["next"] = self.object.get_next()

        if self.request.user.has_perm("photos.add_photo"):
            context["form"] = PhotoForm()

        return context

    def render_to_response(self, context):
        response = super().render_to_response(context)

        if self.object.withdrawn and "potential_duplicates" in context:
            if not all(
                vehicle.withdrawn for vehicle in context["potential_duplicates"]
            ):
                response.status_code = HTTPStatus.NOT_FOUND

        return response

    @method_decorator(permission_required("photos.add_photo", raise_exception=True))
    def post(self, *args, **kwargs):
        form = PhotoForm(self.request.POST)
        vehicle = self.get_object()
        if form.is_valid():
            try:
                add_flickr_photo(form.cleaned_data["url"], vehicle, self.request)
            except IndexError:
                pass

        return self.get(*args, **kwargs)


def check_user(request):
    if request.user.trusted is False:
        raise PermissionDenied


revision_display_related_fields = (
    "from_type",
    "to_type",
    "from_operator",
    "to_operator",
    "from_livery",
    "to_livery",
)


@login_required
def edit_vehicle(request, **kwargs):
    check_user(request)

    vehicle = get_object_or_404(
        Vehicle.objects.select_related(
            "vehicle_type", "livery", "operator", "latest_journey"
        ),
        **kwargs,
    )

    if not request.user.is_superuser and not vehicle.is_editable():
        raise PermissionDenied

    form_data = request.POST or None

    if not request.user.has_perm("vehicles.add_vehiclerevision"):
        form = forms.RulesForm(form_data)
        if form.is_valid():
            request.user.user_permissions.add(
                Permission.objects.get(codename="add_vehiclerevision")
            )
            form_data = None
        else:
            return render(
                request, "rules.html", {"breadcrumb": [vehicle], "form": form}
            )

    if (
        vehicle.operator_id
        and (
            User.operators.through.objects.filter(operator=vehicle.operator_id)
            .exclude(user=request.user)
            .exists()
        )
        and not request.user.operators.filter(noc=vehicle.operator_id).exists()
    ):
        raise PermissionDenied(
            f'Editing {vehicle.operator} vehicles is restricted to "local experts"'
        )

    context = {
        "previous": vehicle.get_previous(),
        "next": vehicle.get_next(),
    }

    revision = None

    try:
        context["vehicle_unique_id"] = vehicle.latest_journey_data["Extensions"][
            "VehicleJourney"
        ]["VehicleUniqueId"]
    except (KeyError, TypeError):
        pass

    form = forms.EditVehicleForm(
        form_data,
        vehicle=vehicle,
        user=request.user,
        sibling_vehicles=(context["previous"], context["next"]),
    )

    context["livery"] = vehicle.livery

    if form_data:
        if form.has_changed() is False or form.changed_data == ["summary"]:
            form.add_error(None, "You haven't changed anything")

        if form.is_valid():
            data = {key: form.cleaned_data[key] for key in form.changed_data}

            revision, features = get_revision(vehicle, data)

            revision.user = request.user
            revision.created_at = timezone.now()
            revision.pending = True
            try:
                with transaction.atomic():
                    revision.save()
                    VehicleRevisionFeature.objects.bulk_create(features)

                    if request.user.trusted:
                        apply_revision(revision, features)
                        revision.pending = False
                        revision.save(update_fields=["pending"])

                    context["revision"] = revision
                    form = None

            except IntegrityError as e:
                error = "There's already a pending edit for that"
                if "unique_pending_livery" in e.args[0]:
                    form.add_error("colours", error)
                elif "unique_pending_type" in e.args[0]:
                    form.add_error("vehicle_type", error)
                elif "unique_pending_operator" in e.args[0]:
                    form.add_error("operator", error)
                elif "vehicle_operator_and_code" in e.args[0]:
                    error = f"{form.cleaned_data['operator']} already has a vehicle with the code {vehicle.code}"
                    form.add_error("operator", error)
                else:
                    raise

        if form:
            context["livery"] = form.cleaned_data.get("colours")

    if form:
        context["pending_edits"] = (
            vehicle.vehiclerevision_set.filter(
                Q(pending=True) | Q(created_at__gte=Now() - datetime.timedelta(days=7))
            )
            .select_related(*revision_display_related_fields)
            .prefetch_related("vehiclerevisionfeature_set__feature")
        )

    if vehicle.operator:
        context["breadcrumb"] = [vehicle.operator, Vehicles(vehicle=vehicle), vehicle]
    else:
        context["breadcrumb"] = [vehicle]

    response = render(
        request,
        "edit_vehicle.html",
        {
            **context,
            "form": form,
            "object": vehicle,
            "vehicle": vehicle,
        },
    )

    # for the ImgBB upload widget
    response["Cross-Origin-Opener-Policy"] = "unsafe-none"

    return response


@require_POST
@login_required
@transaction.atomic
def vehicle_revision_action(request, revision_id, action):
    revision = get_object_or_404(
        VehicleRevision.objects.select_related(
            *revision_display_related_fields, "vehicle"
        )
        .filter(Q(pending=True) | Q(approved_by=request.user))
        .select_for_update(of=["self"]),
        id=revision_id,
    )

    if action == "disapprove" and request.user.id == revision.user_id:
        revision.delete()  # cancel one's own edit
        return HttpResponse("")
    elif not request.user.trusted:
        raise PermissionDenied

    revision.disapproved_reason = unquote(request.headers.get("HX-Prompt", ""))
    revision.approved_by = request.user
    revision.approved_at = Now()

    if action == "apply":
        apply_revision(revision)
        revision.pending = False
        revision.disapproved = False
    elif action == "disapprove":
        revision.pending = False
        revision.disapproved = True

    revision.save()

    return render(request, "vehicle_revision.html", {"revision": revision})


@require_safe
def vehicle_edits(request):
    revisions = (
        VehicleRevision.objects.select_related(
            *revision_display_related_fields, "user", "vehicle"
        )
        .prefetch_related("vehiclerevisionfeature_set__feature")
        .order_by("-id")
    )

    f = filters.VehicleRevisionFilter(
        request.GET or {"status": "approved"}, queryset=revisions
    )
    if request.user.is_anonymous or not (
        request.user.trusted
        or request.user.is_superuser
        or request.GET.get("user") == str(request.user.id)
    ):
        f.filters["status"].field.choices = [("approved", "approved")]

    if f.is_valid():
        paginator = Paginator(f.qs, 100)
        page = paginator.get_page(request.GET.get("page"))
    else:
        page = None

    return render(
        request,
        "vehicle_edits.html",
        {
            "filter": f,
            "revisions": page,
        },
    )


class VehicleJourneyDetailView(DetailView):
    model = VehicleJourney


@require_safe
def journey_json(request, pk, vehicle_id=None, service_id=None):
    journey = get_object_or_404(
        VehicleJourney.objects.select_related("trip", "vehicle"), pk=pk
    )

    data = {
        "vehicle_id": journey.vehicle_id,
        "service_id": journey.service_id,
        "trip_id": journey.trip_id,
        "datetime": timezone.localtime(journey.datetime),
        "route_name": journey.route_name,
        "code": journey.code,
        "destination": journey.destination,
        "direction": journey.direction,
        "current": journey.vehicle and journey.id == journey.vehicle.latest_journey_id,
    }

    if redis_client:
        locations = redis_client and redis_client.lrange(journey.get_redis_key(), 0, -1)
    else:
        locations = None

    if locations:
        locations = [
            VehicleLocation.decode_appendage(location) for location in locations
        ]
        locations.sort(key=lambda location: location["datetime"])

        data["locations"] = []

        stationary = False
        previous = None
        previous_coords = None
        for location in locations:
            coords = location["coordinates"]

            if previous_coords:
                dx = coords[0] - previous_coords[0]
                dy = coords[1] - previous_coords[1]
                if dx * dx + dy * dy < 2.5e-7:  # 0.0005 degrees squared
                    stationary = True
                elif stationary:
                    # mark end of stationary period
                    data["locations"].append(previous)
                    stationary = False

            if not stationary:
                data["locations"].append(location)

                previous_coords = coords

            previous = location

        if stationary:  # add last location
            data["locations"].append(location)

        del locations

    # if not trip - calculate using time and first location?
    # if not trip:
    #     Trip

    if journey.trip:
        data["stops"] = []
        # previous_latlong = None

        trips = journey.trip.get_trips()
        if trips == [journey.trip]:
            stoptimes = trips[0].stoptime_set.select_related("stop__locality")
        else:
            stoptimes = (
                StopTime.objects.filter(trip__in=trips)
                .order_by("trip__start", "id")
                .select_related("stop__locality")
            )
            stoptimes = contiguous_stoptimes_only(stoptimes, journey.trip.id)

        for stoptime in stoptimes:
            stop = stoptime.stop
            # if stop := stoptime.stop:
            #     if stop.latlong:
            #         if previous_latlong:
            #             heading = calculate_bearing(previous_latlong, stop.latlong)
            #         else:
            #             heading = None
            #         previous_latlong = stop.latlong
            data["stops"].append(
                {
                    "id": stoptime.id,
                    "atco_code": stoptime.stop_id,
                    "name": (
                        stop.get_name_for_timetable() if stop else stoptime.stop_code
                    ),
                    "aimed_arrival_time": stoptime.arrival_time(),
                    "aimed_departure_time": stoptime.departure_time(),
                    "minor": stoptime.is_minor(),
                    "heading": stop and stop.get_heading(),
                    "coordinates": stop and stop.latlong and stop.latlong.coords,
                }
            )
    elif journey.service_id:
        stop_usages = StopUsage.objects.filter(
            service_id=journey.service_id
        ).select_related("stop__locality")
        data["stops"] = [
            {
                "id": su.id,
                "atco_code": su.stop_id,
                "name": su.stop.get_name_for_timetable(),
                "heading": su.stop.get_heading(),
                "coordinates": su.stop.latlong and su.stop.latlong.coords,
                "minor": not su.timing_point,
                "inbound": su.inbound,
                "line_name": su.line_name.upper(),
            }
            for i, su in enumerate(stop_usages)
        ]
        del stop_usages

    if data.get("stops") and data.get("locations"):
        # filter by line name
        if "line_name" in data["stops"][0]:
            line_name = journey.route_name.upper()
            if any(stop["line_name"] == line_name for stop in data["stops"]):
                data["stops"] = [
                    stop for stop in data["stops"] if stop["line_name"] == line_name
                ]

        # only stops with coordinates
        stops = [stop for stop in data["stops"] if stop["coordinates"]]

        if stops:
            stop_coords = [stop["coordinates"][::-1] for stop in stops]
            vehicle_coords = [
                location["coordinates"][::-1] for location in data["locations"]
            ]
            # pre-build stop headings array for azimuth filtering; NaN = unknown
            stop_headings = np.array(
                [s["heading"] if s["heading"] is not None else np.nan for s in stops],
                dtype=float,
            )
            try:
                haversine_vector_results = haversine_vector(
                    stop_coords,
                    vehicle_coords,
                    Unit.METERS,
                    comb=True,
                )
            except ValueError as e:
                logging.exception(e)
            else:
                for distances, location in zip(
                    haversine_vector_results, data["locations"]
                ):
                    vehicle_heading = location.get("direction")
                    if vehicle_heading is not None:
                        # mask stops whose heading differs by ≥ 90° from vehicle
                        # heading_diff in [0, 180]; NaN headings are always kept
                        heading_diff = np.abs(
                            ((stop_headings - vehicle_heading) + 180) % 360 - 180
                        )
                        aligned = np.isnan(heading_diff) | (heading_diff < 90)
                        if aligned.any():
                            idx = int(np.argmin(np.where(aligned, distances, np.inf)))
                        else:
                            idx = int(np.argmin(distances))
                    else:
                        idx = int(np.argmin(distances))

                    if distances[idx] < 100:
                        stops[idx]["actual_departure_time"] = location["datetime"]

            # work out which direction we're going in
            inbound = datetime.timedelta()
            outbound = datetime.timedelta()
            previous = None

            for stop in stops:
                if "inbound" in stop and "actual_departure_time" in stop:
                    if previous and previous["inbound"] == stop["inbound"]:
                        difference = (
                            stop["actual_departure_time"]
                            - previous["actual_departure_time"]
                        )
                        if stop["inbound"]:
                            inbound += difference
                        else:
                            outbound += difference

                    previous = stop

            # whichever sum-of-differences is bigger is the direction of travel
            if inbound > outbound:
                data["stops"] = [stop for stop in data["stops"] if stop["inbound"]]
            elif inbound < outbound:
                data["stops"] = [stop for stop in data["stops"] if not stop["inbound"]]

    next_previous_filter = {"date": journey.date}
    if service_id:
        next_previous_filter["service_id"] = service_id
        data["vehicle"] = str(journey.vehicle)
    else:
        next_previous_filter["vehicle_id"] = journey.vehicle_id

    try:
        next_journey = journey.get_next_by_datetime(**next_previous_filter)
    except VehicleJourney.DoesNotExist:
        pass
    else:
        data["next"] = {
            "id": next_journey.id,
            "datetime": timezone.localtime(next_journey.datetime),
        }

    try:
        previous_journey = journey.get_previous_by_datetime(**next_previous_filter)
    except VehicleJourney.DoesNotExist:
        pass
    else:
        data["previous"] = {
            "id": previous_journey.id,
            "datetime": timezone.localtime(previous_journey.datetime),
        }

    return JsonResponse(data)


@require_safe
def latest_journey_debug(request, **kwargs):
    vehicle = get_object_or_404(Vehicle, **kwargs, latest_journey_data__isnull=False)

    # redact possible personal information
    try:
        del vehicle.latest_journey_data["Extensions"]["VehicleJourney"]["DriverRef"]
    except (KeyError, TypeError):
        pass

    return JsonResponse(vehicle.latest_journey_data, safe=False)


def debug(request):
    form = forms.DebuggerForm(request.POST or None)
    result = None
    if form.is_valid():
        data = form.cleaned_data["data"]
        try:
            item = json.loads(data)
        except ValueError as e:
            form.add_error("data", e)
        else:
            vehicle = None
            journey = None
            connection.force_debug_cursor = True
            try:
                with transaction.atomic():
                    command = import_bod_avl.Command()
                    command.do_source()
                    vehicle, created = command.get_vehicle(item)
                    journey = command.get_journey(item, vehicle)
                    if not journey.datetime:
                        journey.datetime = command.get_datetime(item)
                    raise Exception
            except Exception:
                pass
            connection.force_debug_cursor = False

            result = {
                "vehicle": vehicle,
                "journey": journey,
                "queries": connection.queries,
            }

    return render(request, "vehicles/debug.html", {"form": form, "result": result})


@csrf_exempt
def siri_post(request, uuid):
    subscription = get_object_or_404(SiriSubscription, uuid=uuid)
    last_post_key = subscription.get_status_key().replace("_status", "_last_post")

    if request.method == "GET":
        last_post = cache.get(last_post_key)
        return HttpResponse(
            last_post["body"], content_type=last_post["headers"]["content-type"]
        )

    body = request.body.decode()
    data = xmltodict.parse(body, force_list=["VehicleActivity"])

    handle_siri_post(uuid, data)

    cache.set(last_post_key, {"headers": request.headers, "body": body}, None)

    return HttpResponse(
        xmltodict.unparse(
            {
                "Siri": {
                    "@xmlns": "http://www.siri.org.uk/siri",
                    "@version": "2.0",
                    "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                    "@xsi:schemaLocation": "http://www.siri.org.uk/siri http://www.siri.org.uk/schema/2.0/xsd/siri.xsd",
                    "DataReceivedAcknowledgement": {
                        "ResponseTimestamp": timezone.now().isoformat(),
                        "ConsumerRef": subscription.requestor_ref,
                        "Status": True,
                    },
                }
            }
        ),
        content_type="application/xml",
    )


@csrf_exempt
@require_POST
def overland(request, uuid):
    subscription = get_object_or_404(SiriSubscription, uuid=uuid)

    data = json.loads(request.body)

    for item in data["locations"][-1:]:
        when = item["properties"]["timestamp"]
        device_id = item["properties"]["device_id"]
        operator, vehicle, line_name, journey_ref = device_id.split(":")
        lon, lat = item["geometry"]["coordinates"]
        activity = {
            "RecordedAtTime": when,
            "MonitoredVehicleJourney": {
                "OperatorRef": operator,
                "VehicleRef": vehicle,
                "PublishedLineName": line_name,
                "VehicleJourneyRef": journey_ref,
                "VehicleLocation": {
                    "Longitude": lon,
                    "Latitude": lat,
                },
            },
        }

        handle_siri_post(
            uuid,
            {
                "Siri": {
                    "ServiceDelivery": {
                        "ResponseTimestamp": when,
                        "VehicleMonitoringDelivery": {
                            "VehicleActivity": [activity],
                        },
                    }
                }
            },
        )

    cache.set(
        subscription.get_status_key().replace("_status", "_last_post"),
        {"headers": request.headers, "body": request.body.decode()},
        None,
    )

    # https://github.com/aaronpk/Overland-iOS#api
    return JsonResponse({"result": "ok"})
