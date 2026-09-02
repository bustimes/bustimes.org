from django.urls import path, re_path

from . import views

urlpatterns = [
    path(
        "operators/<slug:slug>/blocks",
        views.operator_blocks,
        name="operator_blocks",
    ),
    path("services/<slug>/debug", views.ServiceDebugView.as_view()),
    path("sources", views.SourceListView.as_view()),
    path("sources/<int:pk>", views.SourceDetailView.as_view(), name="source_detail"),
    re_path(
        r"^sources/(?P<source>\d+)/routes/(?P<code>.*)",
        views.route_xml,
        name="route_xml",
    ),
    path("stops/<atco_code>/times.json", views.stop_times_json),
    path("stops/<atco_code>/debug", views.stop_debug),
    path("vehicles/tfl/<reg>", views.tfl_vehicle, name="tfl_vehicle"),
    path("trips/<int:pk>", views.TripDetailView.as_view(), name="trip_detail"),
    path("trips/<int:pk>/block", views.trip_block, name="block_detail"),
    path("trip_updates", views.trip_updates),
    path("trip_updates/<slug:feed_name>.json", views.trip_updates_json),
    path(
        "routelinks/<int:pk>",
        views.route_link_view,
        name="routelink_detail",
    ),
    path("upload", views.upload_gtfs),
]
