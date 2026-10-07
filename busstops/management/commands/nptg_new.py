import logging
import xml.etree.ElementTree as ET

import requests
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.management.base import BaseCommand

from ...models import AdminArea, DataSource, District, Locality, Region
from ...utils import get_datetime

logger = logging.getLogger(__name__)


def get_locality(locality_element: ET.Element) -> Locality:
    lon = locality_element.findtext("Location/Translation/Longitude")
    lat = locality_element.findtext("Location/Translation/Latitude")
    return Locality(
        id=locality_element.findtext("NptgLocalityCode"),
        name=locality_element.findtext("Descriptor/LocalityName"),
        qualifier_name=locality_element.findtext(
            "Descriptor/Qualify/QualifierName", ""
        ),
        created_at=get_datetime(locality_element.attrib["CreationDateTime"]),
        admin_area_id=locality_element.findtext("AdministrativeAreaRef"),
        district_id=locality_element.findtext("NptgDistrictRef") or None,
        parent_id=locality_element.findtext("ParentNptgLocalityRef") or None,
        modified_at=get_datetime(locality_element.attrib["ModificationDateTime"]),
        latlong=GEOSGeometry(f"POINT({lon} {lat})"),
    )


def handle_regions(element: ET.Element):
    for region_element in element:
        region = Region(
            id=region_element.findtext("RegionCode"),
            name=region_element.findtext("Name"),
        )
        if len(region.id) > 2:
            if region.id == "ULS_NI":
                region.id = "NI"
            else:
                region.id = region.id[:2]
        yield region

        for admin_area_element in region_element.find("AdministrativeAreas"):
            admin_area = AdminArea(
                id=int(admin_area_element.findtext("AdministrativeAreaCode")),
                atco_code=admin_area_element.findtext("AtcoAreaCode"),
                name=admin_area_element.findtext("Name"),
                short_name=admin_area_element.findtext("ShortName", ""),
                country=region_element.findtext("Country")[:3],
                region=region,
                created_at=get_datetime(admin_area_element.attrib["CreationDateTime"]),
                modified_at=get_datetime(
                    admin_area_element.attrib["ModificationDateTime"]
                ),
            )
            yield admin_area

            for district_element in admin_area_element.findall(
                "NptgDistricts/NptgDistrict"
            ):
                yield District(
                    id=int(district_element.findtext("NptgDistrictCode")),
                    name=district_element.findtext("Name"),
                    admin_area=admin_area,
                    created_at=get_datetime(
                        district_element.attrib["CreationDateTime"]
                    ),
                    modified_at=get_datetime(
                        district_element.attrib["ModificationDateTime"]
                    ),
                )


def download():
    url = "https://naptan.api.dft.gov.uk/v1/nptg"
    return requests.get(url, timeout=60, stream=True)


class Command(BaseCommand):
    @staticmethod
    def add_arguments(parser):
        parser.add_argument("filename", nargs="?", type=str)

    def handle(self, *args, **options):
        if options["filename"]:
            source, _created = DataSource.objects.get_or_create(
                name=options["filename"]
            )
            path = options["filename"]
        else:
            source, _created = DataSource.objects.get_or_create(name="NPTG")

            path = settings.DATA_DIR / "nptg.xml"

            # download new data if there is any
            response = download()
            if response:
                with path.open("wb") as open_file:
                    for chunk in response.iter_content(chunk_size=102400):
                        open_file.write(chunk)

        regions = Region.objects.in_bulk()
        admin_areas = AdminArea.objects.only("modified_at").in_bulk()
        districts = District.objects.only("modified_at").in_bulk()

        iterator = ET.iterparse(path, events=["start", "end"])
        for event, element in iterator:
            if event == "start":
                if (
                    element.tag
                    == "{http://www.naptan.org.uk/}NationalPublicTransportGazetteer"
                ):
                    modified_at = get_datetime(element.attrib["ModificationDateTime"])
                    # if modified_at == source.datetime:
                    #     return

                    # print(modified_at, source.datetime)

                    source.datetime = modified_at

                continue

            element.tag = element.tag.removeprefix("{http://www.naptan.org.uk/}")

            if element.text:
                element.text = element.text.strip()

            if element.tag == "Regions":
                for item in handle_regions(element):
                    if type(item) is Region:
                        if item.pk not in regions:
                            item.save()

                    elif type(item) is AdminArea:
                        if item.pk not in admin_areas:
                            item.save(force_insert=True)
                        elif admin_areas[item.pk].modified_at != item.modified_at:
                            item.save(force_update=True)

                    elif type(item) is District:
                        if item.pk not in districts:
                            item.save(force_insert=True)
                        elif districts[item.pk].modified_at != item.modified_at:
                            item.save(force_update=True)

                element.clear()  # save memory

            elif element.tag == "NptgLocalities":
                localities = Locality.objects.only("modified_at").in_bulk()
                localities_with_parents = []

                for locality_element in element:
                    item = get_locality(locality_element)

                    if item.district_id and int(item.district_id) not in districts:
                        if item.district_id != "310":
                            logger.warning(
                                f"{item} district {item.district_id} does not exist"
                            )
                        item.district_id = None
                    if item.parent_id and item.parent_id not in localities:
                        localities_with_parents.append(item)
                    else:
                        if item.pk not in localities:
                            item.save(force_insert=True)
                        elif localities[item.pk].modified_at != item.modified_at:
                            item.slug = localities[item.pk].slug
                            item.save(force_update=True)
                        localities[item.id] = item

                element.clear()  # save memory

                for locality in localities_with_parents:
                    if locality.parent_id in localities:
                        locality.save()
                for locality in localities_with_parents:
                    if locality.parent_id not in localities:
                        locality.save()
        iterator.close()

        source.save(update_fields=["datetime"])
