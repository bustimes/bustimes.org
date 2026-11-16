"""Compare route geometries generated from OpenStreetMap (see bustimes/osm_routing.py)
with RouteLinks from timetable data (e.g. BODS TransXChange), to see how well the
algorithm works and tune its settings.

Usage:

    ./manage.py compare_route_links [service_id ...] [--operator NOC] [--limit 10]
        [--postgis | --osm overpass.json] [--no-line-relations] [--csv results.csv]

With --postgis, OSM data comes from tables loaded by osm2pgsql (see
bustimes/osm_postgis.py). That's best for more than a few services. Otherwise, OSM
data for each service is downloaded from the Overpass API (and cached in DATA_DIR/osm).
"""

import csv
import hashlib
import json
import statistics
from collections import defaultdict
from itertools import pairwise
from time import perf_counter

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse

from busstops.models import Service, StopPoint

from ...models import RouteLink, StopTime
from ...osm_postgis import load_osm
from ...osm_routing import Graph, compare, overpass_query

MARGIN = 0.01  # degrees around the stops' bounding box


class Command(BaseCommand):
    help = "Compare route geometries from OSM with RouteLinks from timetable data"

    def add_arguments(self, parser):
        parser.add_argument("services", nargs="*", type=int)
        parser.add_argument("--operator", help="operator NOC")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument(
            "--osm", help="an Overpass API JSON file covering all the services"
        )
        parser.add_argument(
            "--postgis",
            action="store_true",
            help="get OSM data from the database (see bustimes/osm_postgis.py)",
        )
        parser.add_argument(
            "--overpass-url", default="https://overpass-api.de/api/interpreter"
        )
        parser.add_argument(
            "--no-line-relations",
            action="store_true",
            help="ignore OSM bus route relations",
        )
        parser.add_argument(
            "--threshold",
            type=float,
            default=25,
            help="metres - a link 'matches' if it's never further than this away",
        )
        parser.add_argument("--csv", help="write a row per link to this file")

    def handle(self, services, operator, limit, osm, **options):
        self.options = options
        self.graph = None
        if osm:
            with open(osm) as f:
                self.graph = self.get_graph(json.load(f))

        # services with RouteLinks from timetable data, not ones drawn using /snap
        queryset = Service.objects.filter(routelink__override=False).distinct()
        if services:
            queryset = queryset.filter(id__in=services)
        else:
            queryset = queryset.filter(current=True)
            if operator:
                queryset = queryset.filter(operator=operator)
            queryset = queryset.order_by("?")[:limit]

        rows = []
        for service in queryset:
            rows += self.compare_service(service)

        hausdorffs = [row["hausdorff"] for row in rows if row["hausdorff"] is not None]
        if hausdorffs:
            threshold = options["threshold"]
            good = sum(1 for distance in hausdorffs if distance <= threshold)
            self.stdout.write(
                f"\nOverall: {good}/{len(hausdorffs)} "
                f"({good / len(hausdorffs):.0%}) links within {threshold:.0f} m, "
                f"median {statistics.median(hausdorffs):.1f} m, "
                f"{len(rows) - len(hausdorffs)} unmatched"
            )

        if options["csv"] and rows:
            with open(options["csv"], "w", newline="") as f:
                writer = csv.DictWriter(f, rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

    def get_graph(self, osm_data):
        graph = Graph(osm_data)
        if self.options["no_line_relations"]:
            graph.way_refs = {}
        return graph

    def fetch_osm(self, west, south, east, north):
        query = overpass_query(west, south, east, north)
        cache_dir = settings.DATA_DIR / "osm"
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{hashlib.sha256(query.encode()).hexdigest()[:16]}.json"
        if path.exists():
            with path.open() as f:
                return json.load(f)
        response = requests.post(
            self.options["overpass_url"], data={"data": query}, timeout=600
        )
        response.raise_for_status()
        path.write_bytes(response.content)
        return response.json()

    def get_patterns(self, service):
        """distinct stop sequences of the service's trips"""
        sequences = defaultdict(list)
        for trip_id, stop_id in (
            StopTime.objects.filter(trip__route__service=service)
            .order_by("trip_id", "id")
            .values_list("trip_id", "stop_id")
        ):
            sequences[trip_id].append(stop_id)
        return {tuple(sequence) for sequence in sequences.values()}

    def compare_service(self, service):
        patterns = self.get_patterns(service)
        stops = StopPoint.objects.filter(
            atco_code__in={stop for pattern in patterns for stop in pattern},
            latlong__isnull=False,
        ).in_bulk()
        existing = {
            (link.from_stop_id, link.to_stop_id): link
            for link in RouteLink.objects.filter(service=service, override=False)
        }
        if not stops or not existing:
            return []

        graph = self.graph
        if graph is None and self.options["postgis"]:
            graph = self.get_graph(
                load_osm(
                    [
                        [
                            stops[stop].latlong.coords
                            for stop in pattern
                            if stop in stops
                        ]
                        for pattern in patterns
                    ],
                    MARGIN * 2,
                )
            )
        elif graph is None:
            longitudes = [stop.latlong.x for stop in stops.values()]
            latitudes = [stop.latlong.y for stop in stops.values()]
            graph = self.get_graph(
                self.fetch_osm(
                    min(longitudes) - MARGIN,
                    min(latitudes) - MARGIN,
                    max(longitudes) + MARGIN,
                    max(latitudes) + MARGIN,
                )
            )

        start = perf_counter()
        matched = {}
        for pattern in patterns:
            # split at stops without a location
            run = []
            for stop_id in pattern + (None,):
                if stop_id in stops:
                    run.append(stop_id)
                    continue
                if len(run) > 1:
                    links = graph.match(
                        [stops[stop].latlong.coords for stop in run],
                        (service.line_name,),
                    )
                    for key, link in zip(pairwise(run), links):
                        if key in existing and (key not in matched or link):
                            matched[key] = link
                run = []
        elapsed = perf_counter() - start

        rows = []
        for key, link in matched.items():
            route_link = existing[key]
            row = {
                "service": service.id,
                "line_name": service.line_name,
                "from_stop": key[0],
                "to_stop": key[1],
                "route_link": route_link.id,
                "hausdorff": None,
                "mean": None,
                "length_ratio": None,
            }
            if link:
                row.update(compare(link.coords, route_link.geometry.coords))
            rows.append(row)

        compared = [row for row in rows if row["hausdorff"] is not None]
        threshold = self.options["threshold"]
        summary = f"{service} ({service.id}): {len(rows)} links"
        if compared:
            good = sum(1 for row in compared if row["hausdorff"] <= threshold)
            median = statistics.median(row["hausdorff"] for row in compared)
            summary += (
                f", {good}/{len(compared)} within {threshold:.0f} m, "
                f"median {median:.1f} m"
            )
        if len(compared) < len(rows):
            summary += f", {len(rows) - len(compared)} unmatched"
        self.stdout.write(f"{summary} ({elapsed:.1f}s)")

        for row in sorted(compared, key=lambda row: -row["hausdorff"])[:3]:
            if row["hausdorff"] > threshold:
                url = reverse("routelink_detail", args=(row["route_link"],))
                self.stdout.write(
                    f"  {row['hausdorff']:.0f} m  {row['from_stop']} → "
                    f"{row['to_stop']}  {url}"
                )

        return rows
