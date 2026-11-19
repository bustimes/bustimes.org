import requests
from django.core.management.base import BaseCommand

from vehicles.models import get_text_colour, normalise_colour

from ...models import DataSource, Service, ServiceColour


class Command(BaseCommand):
    def handle(self, *args, **options):
        source, _ = DataSource.objects.get_or_create(name="jersey")

        response = requests.get(
            "http://sojbuslivetimespublic.azurewebsites.net/api/Values/v1/GetRoutes"
        )
        routes = response.json()["routes"]

        for item in routes:
            background = normalise_colour(item["Colour"])
            lookup = {
                "background": background,
                "foreground": normalise_colour(get_text_colour(background) or "#000"),
            }
            colour = ServiceColour.objects.filter(**lookup).order_by(
                "id"
            ).first() or ServiceColour.objects.create(name=item["Number"], **lookup)

            service, _ = Service.objects.update_or_create(
                {
                    "colour": colour,
                    "description": item["Name"].strip(),
                    "current": True,
                    "region_id": "JE",
                },
                line_name=item["Number"],
                source=source,
            )

            service.operator.set(["libertybus"])
