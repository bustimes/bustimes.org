from collections import defaultdict

from django.core.management import BaseCommand

from busstops.models import Service
from bustimes.models import StopTime, Trip

from ... import models

# Matches tfl Journeys straight to bustimes Trips by (service, departure time),
# without needing a VehicleJourney to already exist. Backfills Trip.block from
# TfL's own block/running numbers, so the existing Trip.get_trips_in_block()
# block-of-the-day grouping works for TfL-contracted routes.
#
# Pattern.direction (1/2) isn't matched against Trip.inbound - it's not clear
# which value means which, and it turns out we don't need to know: when more
# than one trip shares an exact departure time (opposite-direction workings
# departing at the same clock time), we disambiguate by comparing the
# journey's first stop against each candidate trip's first stop instead.


class Command(BaseCommand):
    help = "Matches tfl Journeys to bustimes Trips by service and departure time, and fills in Trip.block"

    def handle(self, **options):
        services = defaultdict(list)
        for service in Service.objects.filter(current=True, region_id="L").only(
            "line_name"
        ):
            services[service.line_name].append(service)

        line_names = models.Line.objects.values_list(
            "service_line_no", flat=True
        ).distinct()

        matched = 0
        for line_name in line_names:
            if len(services[line_name]) == 1:
                matched += self.match_line(line_name, services[line_name][0])

        self.stdout.write(f"matched {matched} trips")

    def match_line(self, line_name, service):
        # every imported base version - see which one fits our timetable best
        journeys_by_version = defaultdict(list)
        for journey in models.Journey.objects.filter(
            pattern__line__service_line_no=line_name
        ).select_related("block"):
            journeys_by_version[journey.base_version_id].append(journey)
        if not journeys_by_version:
            return 0

        first_atco_codes = {
            (base_version_id, pattern_idx): atco_code
            for base_version_id, pattern_idx, atco_code in models.StopInPattern.objects.filter(
                pattern__line__service_line_no=line_name, sequence_no=1
            ).values_list("base_version", "pattern_idx", "stop__naptan_code")
        }

        trips = list(Trip.objects.filter(route__service=service))
        trips_by_start = defaultdict(list)
        for trip in trips:
            trips_by_start[trip.start].append(trip)

        first_stop_by_trip = dict(
            StopTime.objects.filter(trip__in=trips, sequence__isnull=False)
            .order_by("trip_id", "sequence")
            .distinct("trip_id")
            .values_list("trip_id", "stop_id")
        )

        def get_blocks(journeys):
            blocks = {}
            for journey in journeys:
                candidates = trips_by_start.get(journey.start_time, [])
                if len(candidates) > 1:
                    first_atco_code = first_atco_codes.get(
                        (journey.base_version_id, journey.pattern_idx)
                    )
                    candidates = [
                        trip
                        for trip in candidates
                        if first_stop_by_trip.get(trip.id) == first_atco_code
                    ]
                if len(candidates) != 1:
                    continue  # no match, or still ambiguous - don't guess
                if journey.block:
                    blocks[candidates[0]] = str(journey.block.block_no)
            return blocks

        blocks = max(
            (
                get_blocks(journeys_by_version[v])
                for v in sorted(journeys_by_version, reverse=True)
            ),
            key=len,
        )

        to_update = []
        for trip, block in blocks.items():
            if trip.block != block:
                trip.block = block
                to_update.append(trip)

        Trip.objects.bulk_update(to_update, ["block"], batch_size=1000)
        return len(to_update)
