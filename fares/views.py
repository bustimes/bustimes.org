import re

from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.generic.detail import DetailView

from busstops.models import Service

from .forms import FaresForm
from .models import DataSet, FareRule, FareTable, Tariff


def index(request):
    datasets = DataSet.objects.order_by("-datetime")

    return render(
        request,
        "fares_index.html",
        {
            "datasets": datasets,
        },
    )


class DataSetDetailView(DetailView):
    model = DataSet

    def get_context_data(self, *args, **kwargs):
        context_data = super().get_context_data(*args, **kwargs)
        context_data["breadcrumb"] = self.object.operators.all()

        if self.request.GET:
            form = FaresForm(self.object.tariff_set.all(), self.request.GET)
            if form.is_valid():
                context_data["results"] = form.get_results()
        else:
            form = FaresForm(self.object.tariff_set.all())

        context_data["form"] = form

        tariffs = list(
            self.object.tariff_set.select_related("user_profile").prefetch_related(
                "services",
                Prefetch("faretable_set", FareTable.objects.only("tariff_id", "name")),
            )
        )
        for tariff in tariffs:
            tariff.service = min(
                tariff.services.all(), key=Service.get_order, default=None
            )
        tariffs.sort(
            key=lambda tariff: (
                (0, tariff.service.get_order(), tariff.service.slug)
                if tariff.service
                else (1,),
                [
                    int(part) if part.isdigit() else part
                    for part in re.split(r"(\d+)", tariff.filename)
                ],
                tariff.name,
            )
        )
        context_data["tariffs"] = tariffs

        return context_data


class TariffDetailView(DetailView):
    model = Tariff
    queryset = model.objects.prefetch_related(
        "faretable_set__row_set__cell_set__price",
        "faretable_set__column_set",
        "faretable_set__user_profile",
        "faretable_set__sales_offer_package",
        "price_set__time_interval",
        "price_set__sales_offer_package",
    )

    def get_context_data(self, *args, **kwargs):
        context_data = super().get_context_data(*args, **kwargs)
        context_data["breadcrumb"] = [self.object.source]

        if self.request.GET:
            form = FaresForm([self.object], self.request.GET)
            if form.is_valid():
                context_data["results"] = form.get_results()
        else:
            form = FaresForm([self.object])

        context_data["form"] = form

        return context_data


class FareTableDetailView(DetailView):
    model = FareTable
    queryset = model.objects.prefetch_related(
        "row_set__cell_set__price",
        "column_set",
    )


def service_fares(request, slug):
    service = get_object_or_404(Service, slug=slug)

    fare_rules = FareRule.objects.filter(service=service).select_related(
        "fare", "origin", "destination"
    )

    tariffs = Tariff.objects.filter(services=service).order_by("name", "valid_between")

    tariffs = tariffs.prefetch_related(
        "faretable_set__row_set__cell_set__price",
        "faretable_set__column_set",
        "faretable_set__user_profile",
        "faretable_set__sales_offer_package",
        "faretable_set__preassigned_fare_product",
    )

    if not (fare_rules or tariffs):
        raise Http404

    return render(
        request,
        "service_fares.html",
        {
            "breadcrumb": [service],
            "service": service,
            "fare_rules": fare_rules,
            "tariffs": tariffs,
        },
    )
