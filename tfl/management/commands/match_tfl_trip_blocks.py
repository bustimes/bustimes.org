from collections import defaultdict

from django.core.management import BaseCommand

from busstops.models import Service
from bustimes.models import StopTime, Trip

from ... import models

# Matches tfl Journeys straight to bustimes Trips by (service, departure time),
# without needing a VehicleJourney to already exist. Backfills Trip.block from
# TfL's own block/running numbers, so the existing get_other_trips_in_block()
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
        base_version = models.BaseVersion.objects.order_by("-version").first()
        if not base_version:
            self.stdout.write("no tfl data imported")
            return

        matched = 0
        for line in models.Line.objects.filter(base_version=base_version):
            services = list(
                Service.objects.filter(
                    line_name=line.service_line_no, current=True, region_id="L"
                )
            )
            if len(services) != 1:
                continue
            matched += self.match_line(base_version, line, services[0])

        self.stdout.write(f"matched {matched} trips")

    def match_line(self, base_version, line, service):
        patterns = list(
            models.Pattern.objects.filter(
                base_version=base_version, contract_line_no=line.contract_line_no
            ).values_list("idx", flat=True)
        )
        if not patterns:
            return 0

        journeys = list(
            models.Journey.objects.filter(
                base_version=base_version, pattern_idx__in=patterns
            )
        )
        if not journeys:
            return 0

        first_stops_in_pattern = {
            sip.pattern_idx: sip.stop_idx
            for sip in models.StopInPattern.objects.filter(
                base_version=base_version, pattern_idx__in=patterns, sequence_no=1
            )
        }
        atco_codes = dict(
            models.Stop.objects.filter(
                base_version=base_version,
                idx__in=first_stops_in_pattern.values(),
            ).values_list("idx", "naptan_code")
        )
        first_atco_code_by_pattern = {
            pattern_idx: atco_codes.get(stop_idx)
            for pattern_idx, stop_idx in first_stops_in_pattern.items()
        }

        block_numbers = dict(
            models.Block.objects.filter(
                base_version=base_version,
                idx__in=[j.block_idx for j in journeys],
            ).values_list("idx", "block_no")
        )

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

        to_update = []
        for journey in journeys:
            candidates = trips_by_start.get(journey.start_time, [])
            if len(candidates) > 1:
                first_atco_code = first_atco_code_by_pattern.get(journey.pattern_idx)
                candidates = [
                    trip
                    for trip in candidates
                    if first_stop_by_trip.get(trip.id) == first_atco_code
                ]
            if len(candidates) != 1:
                continue  # no match, or still ambiguous - don't guess

            block_no = block_numbers.get(journey.block_idx)
            trip = candidates[0]
            if block_no is not None and trip.block != str(block_no):
                trip.block = str(block_no)
                to_update.append(trip)

        Trip.objects.bulk_update(to_update, ["block"], batch_size=1000)
        return len(to_update)
