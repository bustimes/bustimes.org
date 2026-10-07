import logging
import xml.etree.ElementTree as ET

import yaml
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.management.base import BaseCommand
from django.db.models.functions import Now

from busstops.models import AdminArea, DataSource, Locality, StopArea, StopPoint
from busstops.utils import get_coord_transform, get_datetime
from bustimes.download_utils import download_if_modified

logger = logging.getLogger(__name__)


def get_point(element):
    if element is None:
        return

    lon = element.findtext("Translation/Longitude") or element.findtext("Longitude")
    lat = element.findtext("Translation/Latitude") or element.findtext("Latitude")
    if lat is not None and lon is not None:
        point = GEOSGeometry(f"POINT({lon} {lat})")
        if (point.x or point.y) and not (
            lon.startswith("-7.5") and lat.startswith("49.7")
        ):
            return point

    easting = element.findtext("Easting")
    northing = element.findtext("Northing")
    grid_type = element.findtext("GridType")

    if not easting:
        easting = element.findtext("Translation/Easting")
        northing = element.findtext("Translation/Northing")
        grid_type = element.findtext("Translation/GridType")
    if easting and not ((easting in {"0", "7"}) and northing == "0"):
        match grid_type:
            case "ITM":
                srid = 2157
            case "IrishOS":
                srid = 29902
            case "UKOS" | "" | None:
                srid = 27700
        return GEOSGeometry(f"SRID={srid};POINT({easting} {northing})")


mapping = (
    ("Descriptor/CommonName", "common_name"),
    ("Descriptor/Landmark", "landmark"),
    ("Descriptor/Street", "street"),
    ("Descriptor/Indicator", "indicator"),
    ("Descriptor/Crossing", "crossing"),
    ("Place/Suburb", "suburb"),
    ("Place/Town", "town"),
    ("StopClassification/StopType", "stop_type"),
    ("StopClassification/OnStreet/Bus/BusStopType", "bus_stop_type"),
    ("StopClassification/OnStreet/Bus/TimingStatus", "timing_status"),
)

# dumb placeholders in the data that should be blank
nothings = {
    "-",
    "--",
    "---",
    "Crossing not known",
    "Street not known",
    "Landmark not known",
    "Unknown",
    "*",
    "Data Unavailable",
    "N/A",
    "Tba",
    "type_undefined",
    "class_undefined",
}


def get_stop(element, atco_code):
    point = get_point(element.find("Place/Location")) or get_point(
        element.find("Location")
    )

    bearing = element.findtext(
        "StopClassification/OnStreet/Bus/MarkedPoint/Bearing/CompassPoint"
    )
    if bearing is None:
        bearing = element.findtext(
            "StopClassification/OnStreet/Bus/UnmarkedPoint/Bearing/CompassPoint"
        )
    if bearing is None:
        bearing = ""

    stop = StopPoint(
        atco_code=atco_code,
        naptan_code=element.findtext("NaptanCode") or element.findtext("PlateCode"),
        latlong=point,
        bearing=bearing,
        active=element.attrib.get("Status", "active") == "active",
    )

    for xml_path, key in mapping:
        value = element.findtext(xml_path, "").strip().replace("`", "'")
        if value in nothings:
            value = ""
        setattr(stop, key, value)

        if stop.indicator == stop.naptan_code:
            stop.indicator = ""

    return stop


def get_stop_area(element):
    stop_area_code = element.findtext("StopAreaCode")

    point = get_point(element.find("Location"))

    return StopArea(
        id=stop_area_code,
        name=element.findtext("Name", "").replace("`", "'"),
        latlong=point,
        active=element.attrib.get("Status", "active") == "active",
        admin_area_id=element.findtext("AdministrativeAreaRef"),
        stop_area_type=element.findtext("StopAreaType"),
    )


class Command(BaseCommand):
    def handle_stop(self, element):
        atco_code = element.findtext("AtcoCode")

        stop = get_stop(element, atco_code)

        stop.source = self.source
        stop.modified_at = Now()

        # a stop can be in multiple stop areas
        # we assume (dubiously) that it has no more than 1 active one
        for stop_area_ref in element.findall("StopAreas/StopAreaRef"):
            if stop_area_ref.attrib.get("Modification") != "delete":
                stop.stop_area_id = stop_area_ref.text or None
                # break

        stop.locality_id = element.findtext("Place/NptgLocalityRef") or None
        if stop.locality_id and stop.locality_id not in self.localities:
            logger.warning("%s locality %s does not exist", atco_code, stop.locality_id)
            stop.locality_id = None

        stop.admin_area_id = element.findtext("AdministrativeAreaRef")
        if atco_code.startswith(stop.admin_area_id):
            stop.admin_area = self.admin_areas.get(stop.admin_area_id)
        else:
            stop.admin_area_id = int(stop.admin_area_id)

        if atco_code in self.overrides:
            for key, value in self.overrides[atco_code].items():
                setattr(stop, key, value)

        if existing := self.existing_stops.get(atco_code.upper()):
            stop.atco_code = existing.atco_code  # in case case is different
            for key in self.bulk_update_fields[1:-1]:
                if getattr(stop, key) != getattr(existing, key):
                    if key == "latlong":
                        if stop.latlong:
                            if stop.latlong.srid and stop.latlong.srid != 4326:
                                stop.latlong.transform(
                                    get_coord_transform(stop.latlong.srid)
                                )
                            if (
                                existing.latlong
                                and stop.latlong.distance(existing.latlong) < 0.00005
                            ):
                                continue
                        # logger.info(
                        #     f"{atco_code}: {existing.latlong} → {stop.latlong}"
                        # )
                        self.stops_to_update.append(stop)
                        break
                    # logger.info(
                    #     f"{atco_code} {key}: {getattr(existing, key)!r} → {getattr(stop, key)!r}"
                    # )
                    self.stops_to_update.append(stop)
                    break
        else:
            stop.created_at = stop.modified_at
            self.stops_to_create.append(stop)

    bulk_update_fields = (
        "modified_at",
        "naptan_code",
        "latlong",
        "bearing",
        "common_name",
        "landmark",
        "street",
        "crossing",
        "stop_type",
        "bus_stop_type",
        "timing_status",
        "locality_id",
        "admin_area_id",
        "stop_area_id",
        "indicator",
        "suburb",
        "town",
        "active",
        "source",
    )

    def update_and_create(self):
        # create any new stop areas
        stops = [stop for stop in self.stops_to_create if stop.stop_area_id]
        stops += [stop for stop in self.stops_to_update if stop.stop_area_id]

        existing_stop_areas = StopArea.objects.in_bulk(self.stop_areas.keys())
        stop_areas_to_update = []
        stop_areas_to_create = []
        for stop_area_id, stop_area in self.stop_areas.items():
            if stop_area_id in existing_stop_areas:
                stop_areas_to_update.append(stop_area)
            else:
                stop_areas_to_create.append(stop_area)

        StopArea.objects.bulk_create(stop_areas_to_create, batch_size=1000)
        StopArea.objects.bulk_update(
            stop_areas_to_update,
            ["name", "latlong", "active", "admin_area", "stop_area_type"],
            batch_size=100,
        )

        existing_stop_areas = StopArea.objects.in_bulk(
            [stop.stop_area_id for stop in stops]
        )
        stop_areas_to_create = {
            StopArea(
                id=stop.stop_area_id, active=True, admin_area_id=stop.admin_area_id
            )
            for stop in stops
            if stop.stop_area_id not in existing_stop_areas
        }
        StopArea.objects.bulk_create(stop_areas_to_create, batch_size=1000)

        # logger.info(
        #     f"{len(stop_areas_to_update)=} {len(stop_areas_to_create)=} {len(self.stops_to_create)=} {len(self.stops_to_update)=}"
        # )

        # create new stops
        StopPoint.objects.bulk_create(self.stops_to_create, batch_size=1000)
        self.stops_to_create = []

        # update updated stops
        StopPoint.objects.bulk_update(
            self.stops_to_update, self.bulk_update_fields, batch_size=1000
        )
        self.stops_to_update = []

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("source_name", nargs="?", default="NaPTAN")

    def handle(self, *args, source_name, **options):
        source = DataSource.objects.get(name=source_name)
        self.source = source

        path = settings.DATA_DIR / f"{source_name}.xml"
        modified, _ = download_if_modified(path, source)

        if not modified:
            return

        iterator = ET.iterparse(path, ["start"])
        event, element = next(iterator)
        iterator.close()
        # the ModificationDateTime attribute of the root element
        # seems to be a reliable way of telling if the data has changed
        assert event == "start" and element.tag == "{http://www.naptan.org.uk/}NaPTAN"
        modified_at = get_datetime(element.attrib["ModificationDateTime"])
        if modified_at == source.datetime:
            return

        source.datetime = modified_at

        # set up overrides/corrections
        overrides_path = settings.BASE_DIR / "fixtures" / "stops.yaml"
        with overrides_path.open() as open_file:
            self.overrides = yaml.load(open_file, yaml.BaseLoader)

        # do this now to detect invalid WKTs (stray commas etc)
        for atco_code in self.overrides:
            if "latlong" in self.overrides[atco_code]:
                self.overrides[atco_code]["latlong"] = GEOSGeometry(
                    self.overrides[atco_code]["latlong"]
                )

        self.stops_to_create = []
        self.stops_to_update = []
        self.admin_areas = {
            admin_area.atco_code: admin_area
            for admin_area in AdminArea.objects.order_by()
        }
        self.localities = {
            locality["pk"] for locality in Locality.objects.values("pk").order_by()
        }
        atco_code_prefix = None

        self.stop_areas = {}

        iterator = ET.iterparse(path)
        for event, element in iterator:
            element.tag = element.tag.removeprefix("{http://www.naptan.org.uk/}")

            if element.text:
                element.text = element.text.strip()

            if element.tag == "StopPoint":
                atco_code = element.findtext("AtcoCode")
                if atco_code[:3] != atco_code_prefix:
                    # logger.info(f"{atco_code_prefix=}")

                    if atco_code_prefix:
                        self.update_and_create()

                    atco_code_prefix = atco_code[:3]

                    self.existing_stops = {
                        stop.atco_code.upper(): stop
                        for stop in StopPoint.objects.filter(
                            atco_code__startswith=atco_code_prefix
                        )
                        .order_by()
                        .defer("search_vector", "modified_at", "created_at")
                    }

                self.handle_stop(element)

                element.clear()  # save memory

            elif element.tag == "StopArea":
                stop_area = get_stop_area(element)
                self.stop_areas[stop_area.id] = stop_area
                element.clear()
        iterator.close()

        self.update_and_create()

        source.save(update_fields=["datetime"])
