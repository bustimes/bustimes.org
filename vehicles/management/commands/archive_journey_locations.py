from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ...tasks import archive_journey_locations


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("days", type=int, help="start this many days ago")

    def handle(self, days, **options):
        today = timezone.localdate()
        for n in range(days, 1, -1):
            date = today - timedelta(days=n)
            self.stdout.write(str(date))
            archive_journey_locations(date)
