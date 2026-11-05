from dataclasses import dataclass


@dataclass(frozen=True)
class GTFSFeed:
    source_name: str
    noc: str
    timezone: str
    static_url: str = ""
    vehicle_positions_url: str = ""
    trip_updates_url: str = ""
    alerts_url: str = ""
    stop_id_prefix: str = ""
    alert_ids_change: bool = False  # entity ids are different in every response
    skip_alert_headers: tuple = ()
    town_in_stop_names: bool = False  # e.g. "VALOGNES Le Gravier"


FEEDS = {
    "rtcsnv": GTFSFeed(
        source_name="RTCSNV",
        noc="RTCSNV",
        timezone="America/Los_Angeles",
        static_url="https://developer.rtcsnv.com/transitData/google_transit.zip",
        vehicle_positions_url="https://developer.rtcsnv.com/transitData/vehiclePositions.pb",
        trip_updates_url="https://developer.rtcsnv.com/transitData/tripUpdates.pb",
        stop_id_prefix="rtcsnv-",
    ),
    "zditm": GTFSFeed(
        source_name="ZDiTM Szczecin",
        noc="ZDITM",
        timezone="Europe/Warsaw",
        static_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs.zip",
        vehicle_positions_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs-rt-vehicles.pb",
        trip_updates_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs-rt-trips.pb",
        alerts_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs-rt-alerts.pb",
        stop_id_prefix="zditm-",
    ),
    "capcotentin": GTFSFeed(
        source_name="Cap Cotentin",
        noc="CAPCOT",
        timezone="Europe/Paris",
        static_url="https://www.data.gouv.fr/api/1/datasets/r/2e97c9b3-a59f-42dd-9b9e-a232fa771f21",
        vehicle_positions_url="https://app.pysae.com/api/v2/groups/transdev-cotentin/gtfs-rt",
        trip_updates_url="https://app.pysae.com/api/v2/groups/transdev-cotentin/gtfs-rt",
        stop_id_prefix="capcotentin-",
        town_in_stop_names=True,
    ),
    # static data and vehicle positions have their own importers
    "ember": GTFSFeed(
        source_name="Ember",
        noc="EMBR",
        timezone="Europe/London",
        trip_updates_url="https://api.ember.to/v1/gtfs/realtime/",
        alerts_url="https://api.ember.to/v1/gtfs/realtime/",
        stop_id_prefix="ember-",
        alert_ids_change=True,
        skip_alert_headers=("Pre-booking",),  # imported as timetable notes
    ),
}
