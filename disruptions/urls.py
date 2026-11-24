from django.urls import path

from . import views

urlpatterns = [
    path("situations", views.situations_index),
    path("situations/<int:id>", views.situation, name="situation"),
    path("reports/old_disruptions", views.old_disruptions),
]
