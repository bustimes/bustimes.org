import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from itertools import pairwise

import requests
import folium
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import (
    Count,
    Prefetch,
    prefetch_related_objects,
    F,
    Q,
    FilteredRelation,
)
from django.db.models.functions import Coalesce
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import JsonLexer, XmlLexer
from rest_framework.renderers import JSONRenderer
from shapely.geometry import LineString, Point
from shapely.ops import substring

from api.serializers import TripSerializer
from api.views import TripViewSet
from busstops.models import (
    DataSource,
    Operator,
    Service,
    StopArea,
    StopPoint,
)
from departures import avl, gtfsr, live
from vehicles.forms import DateForm
from vehicles.models import Vehicle, VehicleJourney, VehicleLocation
from vehicles.rtpi import add_progress_and_delay
from vehicles.utils import redis_client

from .download_utils import download
from .models import Route, StopTime, Trip, RouteLink
from .utils import get_other_trips_in_block


class ServiceDebugView(DetailView):
    model = Service
    template_name = "service_debug.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        trips = (
            Trip.objects.select_related("garage")
            .prefetch_related(
                "calendar__calendardate_set",
                "calendar__calendarbankholiday_set__bank_holiday",
            )
            .order_by("calendar", "inbound", "start")
        )

        routes = (
            self.object.route_set.select_related("source")
            .prefetch_related(Prefetch("trip_set", queryset=trips))
            .order_by("service_code", "revision_number", "start_date", "line_name")
        )

        for route in routes:
            previous_trip = None

            for trip in route.trip_set.all():
                if (
                    previous_trip is None
                    or trip.calendar_id != previous_trip.calendar_id
                ):
                    trip.rowspan = 1
                    previous_trip = trip
                else:
                    previous_trip.rowspan += 1

        context["routes"] = routes

        context["stopusages"] = self.object.stopusage_set.select_related(
            "stop__locality"
        )

        context["breadcrumb"] = [self.object]

        return context


@require_GET
def route_link_view(request, pk):
    route_link = get_object_or_404(RouteLink, pk=pk)

    start = [route_link.from_stop.latlong.y, route_link.from_stop.latlong.x]
    end = [route_link.to_stop.latlong.y, route_link.to_stop.latlong.x]

    m = folium.Map()
    m.fit_bounds([start, end])

    folium.Marker(
        location=start,
        tooltip=f"from {route_link.from_stop}",
    ).add_to(m)

    folium.Marker(
        location=end,
        tooltip=f"to {route_link.to_stop}",
    ).add_to(m)

    folium.vector_layers.PolyLine([[(y, x) for (x, y) in route_link.geometry]]).add_to(
        m
    )

    return HttpResponse(m.get_root().render())


def snap(request, trip_id=None, journey_id=None):
    if trip_id:
        journey = None
        trip = get_object_or_404(
            Trip.objects.filter(route__service__isnull=False), pk=trip_id
        )
    else:
        journey = get_object_or_404(
            VehicleJourney.objects.filter(service__isnull=False, trip__isnull=False),
            pk=journey_id,
        )
        trip = journey.trip

    session = requests.Session()
    session.params.update({"api_key": settings.STADIA_MAPS_API_KEY})
    url = "https://api.stadiamaps.com/map_match/v1"

    stop_times = trip.stoptime_set.filter(stop__latlong__isnull=False).select_related(
        "stop"
    )

    if journey:
        locations = redis_client.lrange(journey.get_redis_key(), 0, -1)

        locations = [
            VehicleLocation.decode_appendage(location) for location in locations
        ]
        locations.sort(key=lambda location: location["datetime"])
        points = [
            {
                "lon": location["coordinates"][0],
                "lat": location["coordinates"][1],
                "time": location["datetime"].timestamp(),
            }
            for location in locations
        ]

    # if not trip - calculate using time and first loca
    else:
        points = [
            {
                "lat": stop_time.stop.latlong.y,
                "lon": stop_time.stop.latlong.x,
                "time": stop_time.arrival_or_departure().total_seconds(),
            }
            for stop_time in stop_times
        ]
    response = session.post(url, json={"costing": "bus", "shape": points}).json()

    route_links = []

    import polyline

    if "trip" in response:
        leg = response["trip"]["legs"][0]
        shape = LineString(
            [(lon, lat) for lat, lon in polyline.decode(leg["shape"], precision=6)]
        )

        start_dist = None

        for from_st, to_st in pairwise(stop_times):
            from_point = Point(from_st.stop.latlong.coords)
            to_point = Point(to_st.stop.latlong.coords)

            if start_dist is None:
                start_dist = shape.project(from_point)

            # project onto remaining shape only
            remaining = substring(shape, start_dist, shape.length)
            if remaining.geom_type != "LineString":
                start_dist = None
                continue
            end_dist_on_remaining = remaining.project(to_point)
            end_dist = start_dist + end_dist_on_remaining

            # skip if either stop is too far from the matched route (~0.1km at UK latitudes)
            if (
                from_point.distance(shape.interpolate(start_dist)) > 0.001
                or to_point.distance(remaining.interpolate(end_dist_on_remaining))
                > 0.001
            ):
                start_dist = None
                continue

            line_substring = substring(shape, start_dist, end_dist)
            start_dist = end_dist

            if type(line_substring) is not LineString:
                continue

            route_link = RouteLink.objects.filter(
                service_id=trip.route.service_id,
                from_stop=from_st.stop,
                to_stop=to_st.stop,
            ).first() or RouteLink(
                service_id=trip.route.service_id,
                from_stop=from_st.stop,
                to_stop=to_st.stop,
            )
            route_link.geometry = line_substring.wkt
            route_links.append(route_link)
            route_link.save()

    return render(
        request,
        "snap.html",
        {
            "object": trip,
            "breadcrumb": [trip],
            "response": response,
            "route_links": route_links,
        },
    )


def maybe_download_file(local_path, s3_key):
    if not local_path.exists():
        import boto3

        if not local_path.parent.exists():
            local_path.parent.mkdir(parents=True)
        client = boto3.client("s3", endpoint_url="https://ams3.digitaloceanspaces.com")
        client.download_file(
            Bucket="bustimes-data", Key=s3_key, Filename=str(local_path)
        )


class SourceListView(ListView):
    model = DataSource
    queryset = (
        DataSource.objects.filter(route__service__isnull=False)
        .annotate(
            routes=Count("route"),
        )
        .order_by("url")
    )


class SourceDetailView(DetailView):
    model = DataSource

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["routes"] = (
            self.object.route_set.filter(service__isnull=False)
            .order_by("service_code", "line_name", "start_date", "revision_number")
            .annotate(
                trips=Count("trip"),
            )
            .select_related("service")
        )

        context["breadcrumb"] = [
            {"get_line_name_and_brand": "Sources", "get_absolute_url": "/sources"}
        ]

        return context


@require_GET
@login_required
def route_xml(request, source, code=""):
    """A way of viewing the TransXChange document* behind a route,
    for debugging purposes

    Ideally should work by downloading the file from bustimes.org's archive on
    S3 (or an S3-compatible object storage service), rather than repeatedly
    downloading from the original source.

    * in theory also works for ATCO-CIF. but not GTFS
    """

    source = get_object_or_404(DataSource, id=source)

    if not source.datetime:
        raise Http404

    if source.is_tnds():
        filename = Path(source.url).name
        path = settings.DATA_DIR / "TNDS" / filename
        maybe_download_file(path, source.get_s3_path())
        with zipfile.ZipFile(path) as archive:
            if code:
                if code.endswith(".zip"):
                    archive = zipfile.ZipFile(archive.open(code))
                    code = ""

                elif ".zip/" in code:
                    sub_archive, code = code.split("/", 1)
                    archive = zipfile.ZipFile(archive.open(sub_archive))

            if code:
                try:
                    return FileResponse(archive.open(code), content_type="text/plain")
                except KeyError as e:
                    raise Http404(e)
            return HttpResponse(
                "\n".join(archive.namelist()), content_type="text/plain"
            )

    content_type = "application/xml"

    if "stagecoach" in source.url:
        path = settings.DATA_DIR / source.url.split("/")[-1]
        if not path.exists():
            if not path.parent.exists():
                path.parent.mkdir()
            download(path, source.url)
    elif code != source.name:
        url = source.url
        if source.url.startswith("https://opendata.ticketer.com/uk/"):
            path = source.url.split("/")[4]
            path = settings.DATA_DIR / "ticketer" / f"{path}.zip"
        elif source.url.startswith(
            "https://data.bus-data.dft.gov.uk/timetable/dataset/"
        ):
            path = settings.DATA_DIR / "bod" / str(source.id)
        elif "data.discoverpassenger" in source.url and "/" in code:
            path, code = code.split("/", 1)
            url = f"https://s3-eu-west-1.amazonaws.com/passenger-sources/{path.split('_')[0]}/txc/{path}"
            path = settings.DATA_DIR / path
        elif source.url.startswith("https://www.opendatani.gov.uk/"):
            path = settings.DATA_DIR / f"{source.id}.zip"
            url = None
            content_type = "text/plain"
        else:
            raise Http404
        if not path.exists():
            if not path.parent.exists():
                path.parent.mkdir(parents=True)
            download(path, url)
    elif "/" in code:
        path = code.split("/")[0]  # archive name
        code = code[len(path) + 1 :]
        path = settings.DATA_DIR / path
    else:
        path = None

    if path:
        if code:
            with zipfile.ZipFile(path) as archive:
                return FileResponse(archive.open(code), content_type=content_type)
    else:
        path = settings.DATA_DIR / code

    try:
        with zipfile.ZipFile(path) as archive:
            return HttpResponse(
                "\n".join(archive.namelist()), content_type="text/plain"
            )
    except zipfile.BadZipFile:
        pass

    # FileResponse automatically closes the file
    return FileResponse(open(path, "rb"), content_type=content_type)


def stop_time_json(stop_time, date) -> dict:
    trip = stop_time.trip
    destination = trip.destination
    route = trip.route

    arrival = stop_time.arrival
    departure = stop_time.departure
    if arrival is not None:
        arrival = stop_time.arrival_datetime(date)
    if departure is not None:
        departure = stop_time.departure_datetime(date)

    operators = []
    if trip.operator:
        operators.append(
            {
                "id": trip.operator.noc,
                "name": trip.operator.name,
                "vehicle_mode": trip.operator.vehicle_mode,
            }
        )

    return {
        "stop_time": stop_time,
        "id": stop_time.id,
        "trip_id": stop_time.trip_id,
        "service": {
            "line_name": route.line_name,
            "operators": operators,
        },
        "destination": destination
        and {
            "atco_code": destination.atco_code,
            "name": destination.get_qualified_name(),
            "locality": destination.locality and str(destination.locality),
        },
        "aimed_arrival_time": arrival,
        "aimed_departure_time": departure,
    }


@require_GET
def stop_times_json(request, atco_code):
    stop = get_object_or_404(StopPoint, atco_code__iexact=atco_code)
    times = []

    if "when" in request.GET:
        try:
            when = datetime.fromisoformat(request.GET["when"])
        except ValueError:
            return HttpResponseBadRequest(
                "'when' isn't in the right format (should be an ISO 8601 datetime)"
            )
        current_timezone = timezone.get_current_timezone()
        when = when.astimezone(current_timezone)
        now = False
    else:
        when = timezone.localtime()
        now = True
    services = stop.service_set.filter(current=True, timetable_wrong=False).defer(
        "geometry", "search_vector"
    )

    by_trip = None
    if now:
        vehicle_locations = avl.get_tracking(stop, services)
        if vehicle_locations:
            by_trip = {
                item["trip_id"]: item for item in vehicle_locations if "trip_id" in item
            }

    try:
        limit = int(request.GET["limit"])
    except KeyError:
        limit = 10
    except ValueError:
        return HttpResponseBadRequest(
            "'limit' isn't in the right format (an integer or nothing)"
        )

    routes = Route.objects.filter(service__in=services).select_related("source")

    departures = live.TimetableDepartures(stop, services, None, routes, by_trip)
    time_since_midnight = timedelta(
        hours=when.hour,
        minutes=when.minute,
        seconds=when.second,
    )

    # any journeys that started yesterday
    yesterday_date = (when - timedelta(1)).date()
    yesterday_time = time_since_midnight + timedelta(1)
    stop_times = departures.get_times(yesterday_date, yesterday_time)

    for stop_time in stop_times.select_related(
        "trip__destination__locality", "trip__route__service", "trip__operator"
    )[:limit]:
        times.append(stop_time_json(stop_time, yesterday_date))

    today = when.date()

    # journeys that started today
    # possibly late-running
    if by_trip:
        stop_times = departures.get_times(today, time_since_midnight, by_trip)
        for stop_time in stop_times.select_related(
            "trip__destination__locality", "trip__route__service", "trip__operator"
        )[:limit]:
            times.append(stop_time_json(stop_time, today))

    stop_times = departures.get_times(today, time_since_midnight)
    for stop_time in stop_times.select_related(
        "trip__destination__locality", "trip__route__service", "trip__operator"
    )[:limit]:
        times.append(stop_time_json(stop_time, today))

    if by_trip:
        prefetch_related_objects(
            [time["stop_time"].trip for time in times if time["trip_id"] in by_trip],
            Prefetch(
                "stoptime_set",
                StopTime.objects.select_related("stop").filter(
                    stop__latlong__isnull=False
                ),
            ),
        )

        for time in times:
            if time["trip_id"] in by_trip:
                item = by_trip[time["trip_id"]]

                if (time["aimed_arrival_time"] or time["aimed_departure_time"]) < when:
                    time["overdue"] = True

                if "delay" not in item or (
                    "overdue" in time and "progress" not in item
                ):
                    add_progress_and_delay(item, time["stop_time"])

                if "delay" not in item:
                    continue

                progress = item.get("progress")

                if (
                    "overdue" not in time
                    or progress
                    and (
                        progress["id"] < time["id"]
                        or progress["id"] == time["id"]
                        and progress["progress"] == 0
                    )
                ):
                    delay = timedelta(seconds=item["delay"])
                    time["delay"] = delay
                    if delay < timedelta() and progress and progress["sequence"] == 0:
                        delay = timedelta()
                    if time["aimed_departure_time"]:
                        time["expected_departure_time"] = (
                            time["aimed_departure_time"] + delay
                        )
                    if time["aimed_arrival_time"]:
                        time["expected_arrival_time"] = (
                            time["aimed_arrival_time"] + delay
                        )
                    else:
                        time["expected_arrival_time"] = time["expected_departure_time"]

    times = [time for time in times if "delay" in time or ("overdue" not in time)]
    for time in times:
        del time["stop_time"]

    return JsonResponse({"times": times})


@require_GET
@staff_member_required
def stop_debug(request, atco_code: str):
    stop = get_object_or_404(
        StopPoint.objects.select_related("locality"), atco_code=atco_code
    )

    responses = []

    formatter = HtmlFormatter()
    css = formatter.get_style_defs()

    for key, response in cache.get_many(
        [
            f"TflDepartures:{stop.pk}",
            f"SiriSmDepartures:{stop.pk}",
        ]
    ).items():
        response_text = response.text
        # syntax-highlight and pretty-print XML and JSON responses
        try:
            # XML
            ET.register_namespace("", "http://www.siri.org.uk/siri")
            xml = ET.XML(response.text)
            ET.indent(xml)
            response_text = ET.tostring(xml).decode()
            response_text = mark_safe(highlight(response_text, XmlLexer(), formatter))
        except ET.ParseError:
            # JSON
            response_text = json.dumps(response.json(), indent=2)
            response_text = mark_safe(highlight(response_text, JsonLexer(), formatter))
        responses.append(
            {"url": response.url, "text": response_text, "headers": response.headers}
        )

    return render(
        request,
        "stoppoint_debug.html",
        {
            "object": stop,
            "breadcrumb": [stop.locality, stop],
            "responses": responses,
            "css": css,
        },
    )


class TripDetailView(DetailView):
    model = Trip
    queryset = (
        model.objects.select_related(
            "route__service", "operator", "route__source", "calendar"
        )
        .defer("route__service__search_vector")
        .prefetch_related("notes")
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        route = self.object.route

        if self.object.operator:
            operators = [self.object.operator]
        elif route and route.service:
            operators = list(self.object.route.service.operator.all())
        else:
            operators = []

        context["breadcrumb"] = operators

        if route and route.service:
            route.service.line_name = route.line_name
            context["breadcrumb"] += [route.service]

        stops = list(TripViewSet.get_stops(self.object))

        if stops:
            if stops[0].stop:
                context["origin"] = stops[0].stop.locality
            if stops[-1].stop:
                context["destination"] = stops[-1].stop.locality

            if route and (
                route.source.name == "Realtime Transport Operators"
                or route.source.name == "Ember"
            ):
                feed_name = "ember" if route.source.name == "Ember" else "ntaie"
                trip_update = gtfsr.get_trip_update(self.object, feed_name)
                if trip_update:
                    context["trip_update"] = trip_update
                    gtfsr.apply_trip_update(stops, trip_update)

            else:
                # no real-time data - cache for an hour
                context["max_age"] = 3600

        context["stops"] = stops
        self.object.stops = stops
        trip_serializer = TripSerializer(self.object)
        self.object.destination_name = self.object.headsign
        stops_json = JSONRenderer().render(trip_serializer.data)

        context["stops_json"] = mark_safe(stops_json.decode())

        return context

    def render_to_response(self, context):
        response = super().render_to_response(context)

        if "max_age" in context:
            response["CDN-Cache-Control"] = (
                f"public, max-age={context['max_age']}, stale-if-error={context['max_age']}"
            )

        return response


@require_GET
def trip_block(request, pk: int):
    trip = get_object_or_404(Trip, pk=pk)

    if not trip.block:
        raise Http404

    form = DateForm(request.GET)
    if form.is_valid():
        date = form.cleaned_data["date"]
    else:
        date = timezone.localdate()

    trips = get_other_trips_in_block(trip, date)

    trips = trips.annotate(
        destination_name=Coalesce(
            "headsign",
            "destination__locality__name",
            "destination__common_name",
        ),
    ).select_related("route")

    if trips := list(trips):
        prefetch_related_objects(
            trips,
            Prefetch(
                "vehiclejourney_set",
                VehicleJourney.objects.filter(
                    date=date,
                ).select_related("vehicle"),
                to_attr="vehicle_journeys",
            ),
        )

    return render(
        request,
        "bustimes/block_detail.html",
        {
            "object": trip.block,
            "breadcrumb": [trip.operator],
            "form": form,
            "date": date,
            "trips": trips,
            "trip": trip,
        },
    )


def tfl_vehicle_arrivals(reg: str):
    reg = reg.upper()

    cache_key = f"TflVehicle:{reg}"

    if (cached := cache.get(cache_key)) is not None:
        return cached

    response = requests.get(
        f"https://api.tfl.gov.uk/Vehicle/{reg}/Arrivals", params=settings.TFL, timeout=8
    )
    if response.ok:
        data = response.json()
        cache.set(cache_key, data, 60)
        return data


@require_GET
def tfl_vehicle(request, reg: str):
    reg = reg.upper()

    vehicles = Vehicle.objects.select_related("latest_journey")
    vehicle = vehicles.filter(
        code=reg, vehiclecode__code=f"TFLO:{reg}", vehiclecode__scheme="BODS"
    ).first()

    data = tfl_vehicle_arrivals(reg)

    if not data:
        if vehicle:
            if vehicle.latest_journey and vehicle.latest_journey.trip_id:
                return redirect(vehicle.latest_journey.trip)
            return redirect(vehicle)
        raise Http404

    line_name = data[0]["lineName"]

    try:
        service = Service.objects.get(
            line_name__iexact=line_name, current=True, source__name="L"
        )
    except (Service.DoesNotExist, Service.MultipleObjectsReturned):
        service = None

    atco_codes = []
    for item in data:
        atco_code = item["naptanId"]
        # try "03700168" as well as "3700168"
        if atco_code[:3] == "370" and atco_code.isdigit():
            atco_codes.append(f"0{atco_code}")
        atco_codes.append(atco_code)

    if service:
        try:
            operator = service.operator.get()
        except (Operator.DoesNotExist, Operator.MultipleObjectsReturned):
            operator = None

        stops = StopPoint.objects.annotate(
            stopusages=FilteredRelation(
                "stopusage", condition=Q(stopusage__service=service)
            ),
            sequence=F("stopusages__order"),
        ).in_bulk(atco_codes)

        # sort by sequence, cos sometimes the arrival predictions are out of order
        prev_sequence = prev_trip_sequence = 0
        prev_destination = None
        for item in data:
            if item.get("destinationName") != prev_destination:
                prev_trip_sequence = prev_sequence

            atco_code = item["naptanId"]

            if stop := (stops.get(atco_code) or stops.get(f"0{atco_code}")):
                item["sequence"] = (getattr(stop, "sequence") or 0) + prev_trip_sequence
            else:
                item["sequence"] = prev_sequence

            prev_destination = item.get("destinationName")
            prev_sequence = item["sequence"]
        data.sort(key=lambda item: item.get("sequence", 0))
    else:
        stops = StopPoint.objects.in_bulk(atco_codes)

    if not stops:
        stops = StopArea.objects.in_bulk(atco_codes)

    route_links = {
        (link.from_stop_id, link.to_stop_id): link
        for link in (service.routelink_set.all() if service else ())
    }

    times = []
    prev_stop = None
    for i, item in enumerate(data):
        expected_arrival = timezone.localtime(
            datetime.fromisoformat(item["expectedArrival"])
        )
        expected_arrival = round(expected_arrival.timestamp() / 60) * 60
        expected_arrival = datetime.fromtimestamp(expected_arrival)
        time = {
            "id": i,
            "stop": {
                "name": item["stationName"],
            },
            "expected_arrival_time": str(expected_arrival.time())[:5],
        }
        atco_code = item["naptanId"]

        if stop := (stops.get(atco_code) or stops.get(f"0{atco_code}")):
            if type(stop) is StopPoint:
                time["stop"]["atco_code"] = stop.atco_code
                time["stop"]["bearing"] = stop.get_heading()

                if prev_stop:
                    route_link = route_links.get((prev_stop.atco_code, stop.atco_code))
                    if route_link:
                        time["track"] = route_link.geometry.coords
                prev_stop = stop

            if stop.latlong:
                time["stop"]["location"] = stop.latlong.coords

        if item["platformName"] and item["platformName"] != "null":
            time["stop"]["icon"] = item["platformName"]

        times.append(time)

    stops_data = {"times": times}
    if service:
        stops_data["service"] = {
            # "id": service.id,
            "line_name": service.line_name,
            "slug": service.slug,
        }
        if operator:
            stops_data["operator"] = {
                "noc": operator.noc,
                "name": operator.name,
                "slug": operator.slug,
            }

    return render(
        request,
        "tfl_vehicle.html",
        {
            "breadcrumb": [service],
            "data": data,
            "object": vehicle,
            "stops_data": stops_data,
        },
    )


trip_updates_sources = {
    "ember": {
        "source_name": "Ember",
    },
    "ntaie": {
        "source_name": "Realtime Transport Operators",
    },
}


@require_GET
def trip_updates_json(request, feed_name: str):
    if feed_name in trip_updates_sources:
        if feed := cache.get(f"{feed_name}_trip_updates"):
            return JsonResponse(feed)

    raise Http404


@require_GET
def trip_updates(request):
    feed_name = request.GET.get("feed_name", "ntaie")

    if feed_name not in trip_updates_sources:
        raise Http404

    source = DataSource.objects.get(name=trip_updates_sources[feed_name]["source_name"])

    trip_updates = gtfsr.get_trip_updates(feed_name)

    journey_codes = trip_updates.keys()
    trips = Trip.objects.filter(
        route__source=source, ticket_machine_code__in=journey_codes
    )
    operators = Operator.objects.filter(
        service__route__in=set(trip.route_id for trip in trips)
    ).distinct()
    trips = {trip.ticket_machine_code: trip for trip in trips}

    trip_updates = [
        (entity, trips.get(trip_id)) for trip_id, entity in trip_updates.items()
    ]

    return render(
        request,
        "trip_updates.html",
        {
            "trips": len(trips),
            "operators": operators,
            "trip_updates": trip_updates,
        },
    )
