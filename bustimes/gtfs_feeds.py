from dataclasses import dataclass


@dataclass(frozen=True)
class GTFSFeed:
    source_name: str
    noc: str
    timezone: str
    static_url: str = ""
    info_url: str = ""  # for credit link
    vehicle_positions_url: str = ""
    trip_updates_url: str = ""
    alerts_url: str = ""
    stop_id_prefix: str = ""
    alert_ids_change: bool = False  # entity ids are different in every response
    skip_alert_headers: tuple = ()
    town_in_stop_names: bool = False  # e.g. "VALOGNES Le Gravier"
    line_names: tuple = ()  # (route_id, line_name) pairs
    inbound_routes: tuple = ()  # for feeds without direction_id


FEEDS = {
    "rtcsnv": GTFSFeed(
        source_name="RTCSNV",
        noc="RTCSNV",
        timezone="America/Los_Angeles",
        static_url="https://developer.rtcsnv.com/transitData/google_transit.zip",
        info_url="https://www.rtcsnv.com/ways-to-travel/transit-services/for-developers/",
        vehicle_positions_url="https://developer.rtcsnv.com/transitData/vehiclePositions.pb",
        trip_updates_url="https://developer.rtcsnv.com/transitData/tripUpdates.pb",
        stop_id_prefix="rtcsnv-",
    ),
    "zditm": GTFSFeed(
        source_name="ZDiTM Szczecin",
        noc="ZDITM",
        timezone="Europe/Warsaw",
        static_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs.zip",
        info_url="https://www.zditm.szczecin.pl/en/zditm/for-developers/gtfs",
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
        info_url="https://transport.data.gouv.fr/datasets/horaires-theoriques-et-en-temps-reel-des-bus-et-autocars-circulant-sur-le-reseau-cap-cotentin",
        vehicle_positions_url="https://app.pysae.com/api/v2/groups/transdev-cotentin/gtfs-rt",
        trip_updates_url="https://app.pysae.com/api/v2/groups/transdev-cotentin/gtfs-rt",
        stop_id_prefix="capcotentin-",
        town_in_stop_names=True,
    ),
    "flibco-lil": GTFSFeed(
        source_name="Flibco Lille",
        noc="FLIB",
        timezone="Europe/Paris",
        static_url="https://www.data.gouv.fr/api/1/datasets/r/43b2ad90-b1e1-41ea-9658-c6b8a30d8d06",
        info_url="https://transport.data.gouv.fr/datasets/flibco-lille-airport-gtfs",
        stop_id_prefix="flibco-lil-",
        line_names=(("AIR_LIL", "LIL"), ("LIL_AIR", "LIL")),
        inbound_routes=("LIL_AIR",),
    ),
    "flibco-crl": GTFSFeed(
        source_name="Flibco Charleroi",
        noc="FLIB",
        timezone="Europe/Brussels",
        static_url="https://www.data.gouv.fr/api/1/datasets/r/3cbd82e5-0cc5-45a9-8299-1c282b59c06c",
        info_url="https://www.data.gouv.fr/datasets/flibco-crl-airport-gtfs",
        stop_id_prefix="flibco-crl-",
        line_names=(("254", "CRL"), ("255", "CRL")),
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
