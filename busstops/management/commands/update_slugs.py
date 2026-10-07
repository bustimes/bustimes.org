import contextlib
from difflib import SequenceMatcher

from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils.text import slugify

from ...models import Service, ServiceCode


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


class Command(BaseCommand):
    def handle(self, **options):
        services = Service.objects.filter(current=True).iterator()

        for service in services:
            ideal_slug = slugify(service)[:50]
            if ideal_slug != service.slug and similar(service.slug, ideal_slug) < 0.5:
                print(service.slug, ideal_slug)
                with contextlib.suppress(IntegrityError):
                    ServiceCode.objects.create(
                        service=service, code=service.slug, scheme="slug"
                    )
                service.slug = ideal_slug
                service.save(update_fields=["slug"])
