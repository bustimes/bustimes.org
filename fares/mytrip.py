import json
from http import HTTPStatus

import requests
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.safestring import mark_safe

from busstops.models import DataSource, Operator, OperatorCode


class Tickets:
    def __init__(self, operator):
        self.operator = operator

    def __str__(self):
        return "Tickets"

    def get_absolute_url(self):
        return f"{self.operator.get_absolute_url()}/tickets"


def get_source():
    return get_object_or_404(DataSource, name="MyTrip")


def get_response(source, code):
    response = requests.get(
        f"{source.url}/{code}",
        headers={"x-api-key": source.settings["x-api-key"]},
        timeout=3,
    )
    if response.status_code == HTTPStatus.NOT_FOUND:
        raise Http404
    response.raise_for_status()
    return response.json()


def operator_tickets(request, slug):
    operator = get_object_or_404(Operator, slug=slug)
    source = get_source()
    code = get_object_or_404(OperatorCode, operator=operator, source=source)
    response = get_response(source, code.code)

    try:
        categories = response["_links"]["topup:category"]
    except KeyError:
        raise Http404 from None
    groupings = response["_embedded"]["render"]["group_by"]
    for grouping in groupings:
        grouping["categories"] = [
            category for category in categories if category["type"] == grouping["value"]
        ]

    context = {"breadcrumb": [operator], "operator": operator, "groupings": groupings}

    return render(request, "operator_tickets.html", context)


def operator_ticket(request, slug, id):
    operator = get_object_or_404(Operator, slug=slug)
    source = get_source()
    code = get_object_or_404(OperatorCode, operator=operator, source=source)
    response = get_response(source, id)

    if (
        response["_links"]["parent"]["id"] != code.code
        or "topup" not in response["_embedded"]
    ):
        raise Http404

    context = {
        "breadcrumb": [operator, Tickets(operator)],
        "operator": operator,
        "title": response["title"],
        "description": response["description"],
        "categories": response["_embedded"]["topup"],
    }
    for category in context["categories"]:
        category["price"] = f"{category['price'] / 100:.2f}"
        category["url"] = category["_links"]["public:view-product"]["href"]

    json_ld = json.dumps(
        [
            {
                "@context": "https://schema.org/",
                "@type": "Product",
                "name": category["title"],
                "description": category["description"],
                "brand": {
                    "@type": "Brand",
                    "name": category["_embedded"]["operator"]["name"],
                },
                "image": category["_embedded"]["operator"]["logo"],
                "offers": {
                    "@type": "Offer",
                    "url": category["url"],
                    "priceCurrency": "GBP",
                    "price": category["price"],
                    "availability": "https://schema.org/InStock",
                },
            }
            for category in context["categories"]
        ]
    )
    context["json_ld"] = mark_safe(
        f'<script type="application/ld+json">{json_ld}</script>'
    )

    return render(request, "operator_ticket.html", context)
