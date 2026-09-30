import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from bustimes.formatting import format_timedelta
from vehicles.utils import redis_client

logger = logging.getLogger(__name__)


def _get_feed():
    if settings.NTA_API_KEY:
        if not redis_client.set("ntaie_lock", 1, ex=60, nx=True):
            return
        url = "https://api.nationaltransport.ie/gtfsr/v2/TripUpdates"
        try:
            response = requests.get(
                url,
                headers={"x-api-key": settings.NTA_API_KEY},
                timeout=10,
                verify=False,
            )
            response.raise_for_status()
        except requests.RequestException:
            logger.exception("error fetching NTA GTFS-R feed")
            return
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
        return feed


def get_trip_updates(feed_name) -> dict:
    if feed_name == "ntaie" and (feed := _get_feed()):
        feed = json_format.MessageToDict(feed)
        if "entity" in feed:
            trip_updates = {
                entity["tripUpdate"]["trip"]["tripId"]: entity["tripUpdate"]
                for entity in feed["entity"]
                if "tripId" in entity["tripUpdate"]["trip"]
            }
            cache.set(
                f"{feed_name}_trip_updates", trip_updates, 300
            )  # cache for 5 minutes
            return trip_updates
    return cache.get(f"{feed_name}_trip_updates")


def maybe_get_and_apply_trip_update(trip, stops: list):
    match trip.route and trip.route.source.name:
        case "Realtime Transport Operators":
            feed_name = "ntaie"
        case "Ember" | "FlixBus" as e:
            feed_name = e.lower()
        case _:  # source not supported
            return False

    if trip_update := get_trip_update(trip, feed_name):
        apply_trip_update(stops, trip_update)
        return True


def get_trip_update(trip, feed_name: str) -> dict:
    if (
        (trip_id := trip.ticket_machine_code)
        and (trip_updates := get_trip_updates(feed_name))
        and trip_id in trip_updates
    ):
        return trip_updates[trip_id]


def get_expected_time(scheduled_time, stop_time_update, key):
    if key in stop_time_update:
        update = stop_time_update[key]
        if scheduled_time and "delay" in update:
            expected_time = scheduled_time + timedelta(seconds=update["delay"])
        elif "time" in update:
            return datetime.fromtimestamp(
                int(update["time"]), tz=ZoneInfo("Europe/Dublin")
            )
        else:
            return
        return format_timedelta(expected_time)


def apply_trip_update(stops, trip_update: dict) -> None:
    if "stopTimeUpdate" not in trip_update:
        return

    stops_by_sequence = {}
    for stop in stops:
        assert stop.sequence not in stops_by_sequence
        stops_by_sequence[stop.sequence] = stop
        stop.update = None

    for stop_time_update in trip_update["stopTimeUpdate"]:
        sequence = stop_time_update["stopSequence"]
        stop_time = stops_by_sequence[sequence]
        assert sequence == stop_time.sequence
        # if "stopId" in stop_time_update:
        #     assert stop_time_update["stopId"] == stop_time.stop_id
        stop_time.update = stop_time_update

    stop_time_update = None
    for stop_time in stops:
        if stop_time.update:
            stop_time_update = stop_time.update

        if stop_time_update:
            stop_time.update = stop_time_update
            if stop_time_update.get("scheduleRelationship") == "SKIPPED":
                continue
            stop_time.expected_arrival_time = get_expected_time(
                stop_time.arrival, stop_time_update, "arrival"
            )
            stop_time.expected_departure_time = get_expected_time(
                stop_time.departure, stop_time_update, "departure"
            )


def update_departure(departure: dict, trip_update: dict) -> None:
    if trip_update["trip"].get("scheduleRelationship") == "CANCELED":
        departure["cancelled"] = True
        return
    stop_time_update = None
    for update in trip_update.get("stopTimeUpdate", ()):
        if update["stopSequence"] > departure["stop_time"].sequence:
            break
        stop_time_update = update
    if stop_time_update:
        if stop_time_update.get("scheduleRelationship") == "SKIPPED":
            departure["cancelled"] = True
        elif "departure" in stop_time_update:
            if (
                stop_time_update["stopSequence"] == departure["stop_time"].sequence
                and "time" in stop_time_update["departure"]
            ):
                time = datetime.fromtimestamp(
                    int(stop_time_update["departure"]["time"]),
                    tz=ZoneInfo("Europe/Dublin"),
                )
                departure["live"] = time

            elif "delay" in stop_time_update["departure"]:
                delay = timedelta(seconds=stop_time_update["departure"]["delay"])
                departure["live"] = departure["time"] + delay

    if (
        "vehicle" not in departure
        and "vehicle" in trip_update
        and "licensePlate" in trip_update["vehicle"]
    ):
        departure["vehicle"] = trip_update["vehicle"]["licensePlate"]


def update_stop_departures(departures: list, feed_name: str) -> None:
    trip_updates = get_trip_updates(feed_name)

    if not trip_updates:
        return

    for departure in departures:
        trip_id = departure["stop_time"].trip.ticket_machine_code
        if trip_update := trip_updates.get(trip_id):
            update_departure(departure, trip_update)
