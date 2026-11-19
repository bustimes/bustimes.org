from django.core.management.base import BaseCommand

from vehicles.models import Vehicle, normalise_colour

from ...models import ServiceColour


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        line_names = set()

        for vehicle in Vehicle.objects.filter(
            operator__in=("LOTH", "ECBU", "NELB", "EDTR"),
            latest_journey__service__isnull=False,
        ):
            data = vehicle.latest_journey_data

            if data and "routeColor" in data:
                if data["routeName"] in line_names:
                    continue

                service = vehicle.latest_journey.service

                if data["routeName"] == service.line_name:
                    lookup = {
                        "foreground": normalise_colour(data["routeTextColor"]),
                        "background": normalise_colour(data["routeColor"]),
                    }
                    colour = ServiceColour.objects.filter(**lookup).order_by(
                        "id"
                    ).first() or ServiceColour.objects.create(
                        name=data["routeName"], **lookup
                    )
                    service.colour = colour
                    service.save(update_fields=["colour"])

                    line_names.add(data["routeName"])
