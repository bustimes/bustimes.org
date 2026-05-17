from django.urls import path
from django.views.generic.base import TemplateView

from . import views

urlpatterns = [
    path(
        "groups/<group_slug>/vehicles", views.operator_vehicles, name="group_vehicles"
    ),
    path(
        "operators/<slug:slug>/vehicles",
        views.operator_vehicles,
        name="operator_vehicles",
    ),
    path("operators/<slug>/map", views.operator_map, name="operator_map"),
    path("operators/<slug:slug>/debug", views.operator_debug),
    path("services/<noc>:<line_name>/vehicles", views.service_vehicles_history),
    path(
        "services/<slug>/vehicles",
        views.service_vehicles_history,
        name="service_vehicles",
    ),
    path("vehicles", views.vehicles),
    path("vehicles.json", views.vehicles_json),
    path("vehicles/debug", views.debug),
    path("vehicles/history", views.vehicle_edits),
    path("vehicles/edits", views.vehicle_edits),
    path(
        "vehicles/revisions/<int:revision_id>/<action>",
        views.vehicle_revision_action,
        name="vehicle_revision_action",
    ),
    path("vehicles/<int:pk>", views.VehicleDetailView.as_view()),
    path(
        "vehicles/<slug:slug>", views.VehicleDetailView.as_view(), name="vehicle_detail"
    ),
    path("vehicles/<int:id>/edit", views.edit_vehicle),
    path("vehicles/<slug>/edit", views.edit_vehicle, name="vehicle_edit"),
    path(
        "vehicles/<int:id>/debug",
        views.latest_journey_debug,
        name="latest_journey_debug",
    ),
    path("vehicles/<slug>/debug", views.latest_journey_debug),
    path("journeys/<int:pk>", views.VehicleJourneyDetailView.as_view()),
    path("journeys/<int:pk>.json", views.journey_json),
    path(
        "vehicles/<int:vehicle_id>/journeys/<int:pk>.json",
        views.journey_json,
        name="vehicle_journey",
    ),
    path(
        "services/<int:service_id>/journeys/<int:pk>.json",
        views.journey_json,
        name="service_journey",
    ),
    path("liveries.<int:version>.css", views.liveries_css),
    path("rules", TemplateView.as_view(template_name="rules.html")),
    path("map", TemplateView.as_view(template_name="map.html"), name="map"),
    path("maps", views.get_redirect_view("map", permanent=True)),
    path("map/old", TemplateView.as_view(template_name="map_classic.html")),
    path("firehose", TemplateView.as_view(template_name="firehose_map.html")),
    path("siri/<uuid:uuid>", views.siri_post, name="siri_post"),
    path("overland/<uuid:uuid>", views.overland),
]
