import logging
from collections import defaultdict
from functools import cache
from pathlib import Path

import gtfs_kit
import pandas as pd
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Operator, Service, StopPoint
from fares.models import Fare, FareRule
from vosa.models import Registration

from ...download_utils import download_if_modified
from ...gtfs_utils import (
    MODES,
    do_route_links,
    finish_gtfs_import,
    get_arrival_and_departure,
    get_calendars,
    get_first_and_last_stop_times,
    save_trips,
    set_trip_times,
)
from ...models import Note, Route, StopTime, Trip

logger = logging.getLogger(__name__)


note_codes = ["¶", "‖", "§", "‡", "†", "*"]


@cache
def get_note(note_text):
    return Note.objects.get_or_create(code=note_codes.pop(), text=note_text[:255])[0]


class Command(BaseCommand):
    def handle(self, *args, **options):
        path = settings.DATA_DIR / Path("ember_gtfs.zip")

        source = DataSource.objects.get(name="Ember")
        source.url = "https://cdn.ember.to/gtfs/static/Ember_GTFS_latest.zip"

        modified, last_modified = download_if_modified(path, source)

        if not modified:
            return  # no new data to import
        source.datetime = last_modified

        logger.info(f"{source} {last_modified}")

        feed = gtfs_kit.read_feed(path, dist_units="km")

        operator = Operator.objects.get(name="Ember")

        existing_services = {
            service.line_name: service for service in operator.service_set.all()
        }
        existing_routes = {route.code: route for route in source.route_set.all()}
        routes = []

        stops = StopPoint.objects.in_bulk(feed.stops.stop_id.to_list())
        new_stops = [
            StopPoint(
                atco_code=f"ember-{stop.stop_id}",
                common_name=stop.stop_name,
                active=True,
                source=source,
                latlong=f"POINT({stop.stop_lon} {stop.stop_lat})",
            )
            for stop in feed.stops.itertuples()
            if stop.stop_id not in stops
        ]
        StopPoint.objects.bulk_create(
            new_stops,
            update_conflicts=True,
            unique_fields=["atco_code"],
            update_fields=["common_name", "latlong"],
        )
        for stop in new_stops:
            stops[stop.atco_code.removeprefix("ember-")] = stop

        calendars = get_calendars(feed, source)

        for row in feed.get_routes(as_gdf=True).itertuples():
            if row.route_id in existing_services:
                service = existing_services[row.route_id]
            else:
                service = Service(line_name=row.route_id)

            if row.route_id in existing_routes:
                route = existing_routes[row.route_id]
            else:
                route = Route(code=row.route_id)
            route.source = source
            route.service = service
            route.line_name = row.route_id
            service.source = source
            service.description = route.description = row.route_long_name
            service.current = True
            service.colour_id = operator.colour_id
            service.mode = MODES[row.route_type]
            if row.geometry:
                service.geometry = row.geometry.wkt

            registrations = Registration.objects.filter(
                licence__licence_number="PM2025892", service_number=row.route_id
            )
            if len(registrations) == 1:
                route.registration = registrations[0]

            service.save()
            service.operator.add(operator)
            route.save()

            routes.append(route)

            existing_routes[route.code] = route  # deals with duplicate rows

        existing_trips = {
            trip.vehicle_journey_code: trip for trip in operator.trip_set.all()
        }
        trips = {}
        for row in feed.trips.itertuples():
            trip = Trip(
                route=existing_routes[row.route_id],
                calendar=calendars[row.service_id],
                inbound=row.direction_id == 1,
                ticket_machine_code=row.trip_id,
                vehicle_journey_code=row.trip_id,
                operator=operator,
                headsign=row.trip_headsign,
            )
            if trip.vehicle_journey_code in existing_trips:
                # reuse existing trip id
                trip.id = existing_trips[trip.vehicle_journey_code].id
            trips[trip.vehicle_journey_code] = trip
        del existing_trips

        sorted_stop_times, first_stop_times, last_stop_times = (
            get_first_and_last_stop_times(feed.stop_times)
        )

        stop_times = []
        for row in sorted_stop_times.itertuples():
            trip = trips[row.trip_id]

            is_last = row.stop_sequence == last_stop_times.stop_sequence[row.trip_id]
            arrival, departure = get_arrival_and_departure(
                row.arrival_time, row.departure_time, is_last
            )

            stop_time = StopTime(
                arrival=arrival,
                departure=departure,
                sequence=row.stop_sequence,
                trip=trip,
                timing_point=bool(row.timepoint),
                pick_up=(row.pickup_type != 1),
                set_down=(row.drop_off_type != 1),
            )

            stop_time.stop = stops[row.stop_id]

            stop_times.append(stop_time)

        for trip_id in set_trip_times(trips, first_stop_times, last_stop_times, stops):
            logger.warning(f"trip {trip_id} has no stop times")

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        stop_codes = {stop_id: stop.atco_code for stop_id, stop in stops.items()}
        do_route_links(feed, source, existing_routes, feed_stops, stop_codes)

        fare_attributes_df = feed.fare_attributes
        fare_rules_df = feed.fare_rules

        # get TripUpdates from the GTFS-RT feed - to mark some stops as "pre-book only":

        realtime_url = "https://api.ember.to/v1/gtfs/realtime/"
        response = requests.get(realtime_url, timeout=10)
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        stop_notes = defaultdict(list)  # map of notes to lists of stop ids

        for item in feed.entity:
            if item.HasField("alert"):
                header = item.alert.header_text.translation[0].text
                description = item.alert.description_text.translation[0].text
                if header == "Pre-booking":
                    stop_id = item.alert.informed_entity[0].stop_id
                    note = get_note(description)
                    stop_notes[note].append(stop_id)

        with transaction.atomic():
            trip_objs = [trip for trip in trips.values() if trip is not None]
            existing_trips = save_trips(
                trip_objs,
                fields=[
                    "route",
                    "calendar",
                    "start",
                    "end",
                    "destination",
                    "block",
                    "vehicle_journey_code",
                    "ticket_machine_code",
                    "inbound",
                    "headsign",
                ],
            )

            StopTime.objects.filter(trip__in=existing_trips).delete()
            StopTime.objects.bulk_create(stop_times)

            existing_notes = {
                (note.code, note.text): note
                for note in Note.objects.filter(trip__operator="EMBR")
            }
            for note, stop_ids in stop_notes.items():
                note_stop_times = [
                    stop_time
                    for stop_time in stop_times
                    if stop_time.stop_id in stop_ids
                ]
                note_trips = [stop_time.trip_id for stop_time in note_stop_times]
                note.stoptime_set.set(note_stop_times)
                note.trip_set.set(note_trips)

            # remove old notes
            for note in existing_notes.values():
                if note not in stop_notes:
                    note.trip_set.clear()

            # fares
            if fare_attributes_df is not None and not fare_attributes_df.empty:
                new_fare_ids = set(fare_attributes_df.fare_id.tolist())
                Fare.objects.filter(source=source).exclude(
                    fare_id__in=new_fare_ids
                ).delete()
                fare_objs = [
                    Fare(
                        source=source,
                        fare_id=row.fare_id,
                        price=row.price,
                        currency=row.currency_type,
                        payment_method=row.payment_method,
                        transfers=int(row.transfers) if pd.notna(row.transfers) else 0,
                    )
                    for row in fare_attributes_df.itertuples()
                ]
                Fare.objects.bulk_create(
                    fare_objs,
                    update_conflicts=True,
                    unique_fields=["source", "fare_id"],
                    update_fields=["price", "currency", "payment_method", "transfers"],
                )
                fares = {fare.fare_id: fare for fare in fare_objs}
                FareRule.objects.filter(fare__source=source).delete()
                if fare_rules_df is not None and not fare_rules_df.empty:
                    rules = []
                    for row in fare_rules_df.itertuples():
                        fare = fares.get(row.fare_id)
                        if fare is None:
                            continue
                        rule = FareRule(fare=fare)
                        if pd.notna(getattr(row, "route_id", float("nan"))):
                            route = existing_routes.get(row.route_id)
                            if route:
                                rule.service = route.service
                        if pd.notna(getattr(row, "origin_id", float("nan"))):
                            rule.origin = stops.get(row.origin_id)
                        if pd.notna(getattr(row, "destination_id", float("nan"))):
                            rule.destination = stops.get(row.destination_id)
                        rules.append(rule)
                    FareRule.objects.bulk_create(rules)
            else:
                Fare.objects.filter(source=source).delete()

            finish_gtfs_import(source, operator, routes, trip_objs)

            source.save(update_fields=["url", "datetime"])
