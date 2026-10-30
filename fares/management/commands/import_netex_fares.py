import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from functools import cache

import requests
from django.core.management.base import BaseCommand
from django.db import DataError, IntegrityError
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q
from django.utils.http import http_date, parse_http_date
from sql_util.utils import Exists

from busstops.models import Operator, Service
from bustimes.utils import log_time_taken
from vehicles.utils import filename_from_content_disposition

from ... import models

logger = logging.getLogger(__name__)


def get_user_profile(element, user_profiles):
    code = element.attrib["id"]
    if code in user_profiles:
        return user_profiles[code], False

    return models.UserProfile.objects.get_or_create(
        {
            "name": element.findtext("Name"),
            "min_age": element.findtext("MinimumAge"),
            "max_age": element.findtext("MaximumAge"),
        },
        code=code,
    )


def get_sales_offer_package(element):
    return models.SalesOfferPackage.objects.get_or_create(
        code=element.attrib["id"],
        name=element.findtext("Name", ""),
        description=element.findtext("Description", ""),
    )


def get_fare_product(element):
    return models.PreassignedFareProduct.objects.get_or_create(
        code=element.attrib["id"],
        name=element.findtext("Name", ""),
        charging_moment=element.findtext("ChargingMomentType", ""),
        tariff_basis=element.findtext("ConditionSummary/TariffBasis", ""),
    )


def get_existing_fare_zones(source):
    return {f"{zone.code} {zone.name}": zone for zone in source.farezone_set.all()}


def get_fare_zones(source, existing_zones, fare_zone_elements):
    zones = {}
    for fare_zone_element in fare_zone_elements:
        code = fare_zone_element.attrib["id"]
        name = fare_zone_element.findtext("Name", "")

        key = f"{code} {name}"
        if key in existing_zones:
            zone = existing_zones[key]
            assert zone.name == name
        else:
            zone = models.FareZone(
                code=code,
                name=name,
                source=source,
            )
            existing_zones[key] = zone
        zones[code] = zone

        # stop_refs = [stop.attrib['ref'] for stop in fare_zone_element.findall('members/ScheduledStopPointRef')]
        # if stop_refs:
        #     try:
        #         if stop_refs[0].startswith('atco'):
        #             fare_zone.stops.set(StopPoint.objects.filter(
        #                 atco_code__in=[stop_ref.removeprefix('atco:') for stop_ref in stop_refs]
        #             ))
        #         # elif stop_refs[0].startswith('naptStop'):
        #         #     fare_zone.stops.set(StopPoint.objects.filter(
        #         #         naptan_code__iexact__in=[stop_ref.removeprefix('naptStop:') for stop_ref in stop_refs]
        #         #     ))
        #         else:
        #             print(stop_refs[0])
        #     except IntegrityError as e:
        #         print(e)

    models.FareZone.objects.bulk_create(
        [zone for zone in zones.values() if not zone.id]
    )
    return zones


@cache
def get_service(operator, line_name, line_id):
    services = Service.objects.filter(
        Exists("route", filter=Q(line_name__iexact=line_name)),
        operator=operator,
        current=True,
    )
    try:
        try:
            return services.get()
        except Service.MultipleObjectsReturned:
            # disambiguate using the line id, which usually matches the TransXChange
            return services.get(Exists("route", filter=Q(line_id=line_id)))
    except (Service.DoesNotExist, Service.MultipleObjectsReturned) as e:
        logger.warning(f"{e} {operator} {line_name}")


def delete_unused_prices(max_id):
    deleted, _ = models.Price.objects.filter(
        id__lte=max_id,
        tariff=None,
        time_interval=None,
        cell=None,
        distancematrixelement=None,
    ).delete()
    logger.info(f"deleted {deleted} unused prices")


class Command(BaseCommand):
    base_url = "https://data.bus-data.dft.gov.uk"

    def handle_file(self, source, open_file, filename=None):
        iterator = ET.iterparse(open_file)

        if not filename:
            filename = open_file.name

        try:
            for _, element in iterator:
                # remove NeTEx namespace for simplicity's sake:
                if element.tag[:31] == "{http://www.netex.org.uk/netex}":
                    element.tag = element.tag[31:]
        except ET.ParseError:
            logger.exception("error parsing %s", filename)
            return

        operators = element.findall(
            "dataObjects/CompositeFrame/frames/ResourceFrame/organisations/Operator"
        )
        operators = {operator.attrib["id"]: operator for operator in operators}

        lines = element.findall(
            "dataObjects/CompositeFrame/frames/ServiceFrame/lines/Line"
        )
        lines = {line.attrib["id"]: line for line in lines}

        user_profiles = {**self.user_profiles}
        for usage_parameter in element.findall(
            "dataObjects/CompositeFrame/frames/FareFrame/usageParameters/UserProfile"
        ):
            user_profile, created = get_user_profile(usage_parameter, user_profiles)
            user_profiles[user_profile.code] = user_profile

        sales_offer_packages = {**self.sales_offer_packages}
        for sales_offer_package in element.findall(
            "dataObjects/CompositeFrame/frames/FareFrame/salesOfferPackages/SalesOfferPackage"
        ):
            sales_offer_package, created = get_sales_offer_package(sales_offer_package)
            sales_offer_packages[sales_offer_package.code] = sales_offer_package

        fare_products = {**self.fare_products}
        for fare_product in element.findall(
            "dataObjects/CompositeFrame/frames/FareFrame/fareProducts/PreassignedFareProduct"
        ):
            fare_product, created = get_fare_product(fare_product)
            fare_products[fare_product.code] = fare_product

        price_groups = {}
        price_group_prices = {}
        for price_group_element in element.findall(
            "dataObjects/CompositeFrame/frames/FareFrame/priceGroups/PriceGroup"
        ):
            price_element = price_group_element.find(
                "members/GeographicalIntervalPrice"
            )  # assume only 1 ~
            if price_element is not None:
                price = models.Price(amount=price_element.findtext("Amount"))
                price_groups[price_group_element.attrib["id"]] = price
                price_group_prices[price_element.attrib["id"]] = price
        models.Price.objects.bulk_create(price_groups.values())

        fare_zones = get_fare_zones(
            source,
            self.fare_zones,
            element.findall(
                "dataObjects/CompositeFrame/frames/FareFrame/fareZones/FareZone"
            ),
        )

        prices = {}

        tariffs = {}
        time_intervals = {}
        for tariff_element in element.findall(
            "dataObjects/CompositeFrame/frames/FareFrame/tariffs/Tariff"
        ):
            tariff_code = tariff_element.attrib["id"]

            fare_structure_elements = tariff_element.find("fareStructureElements")

            user_profile = None
            trip_type = ""
            if fare_structure_elements is not None:
                user_profile = fare_structure_elements.find(
                    "FareStructureElement/GenericParameterAssignment/limitations/UserProfile"
                )
                if user_profile is not None:
                    user_profile, created = get_user_profile(
                        user_profile, user_profiles
                    )
                    user_profiles[user_profile.code] = user_profile

                trip_type = fare_structure_elements.findtext(
                    "FareStructureElement/GenericParameterAssignment/limitations/RoundTrip/TripType",
                    "",
                )

            type_of_tariff = tariff_element.find("TypeOfTariffRef")
            if type_of_tariff is not None:
                type_of_tariff = type_of_tariff.attrib["ref"].removeprefix("fxc:")

            valid_between = DateTimeTZRange(
                tariff_element.findtext("validityConditions/ValidBetween/FromDate"),
                tariff_element.findtext("validityConditions/ValidBetween/ToDate"),
                "[]",
            )
            if valid_between.upper and valid_between.upper < valid_between.lower:
                logger.error(f"{filename} {valid_between}")
                valid_between = None

            tariff = models.Tariff.objects.create(
                code=tariff_code,
                name=tariff_element.findtext("Name"),
                source=source,
                filename=filename,
                trip_type=trip_type,
                user_profile=user_profile,
                type_of_tariff=type_of_tariff or "",
                valid_between=valid_between,
            )
            tariffs[tariff.code] = tariff

            operator_ref = tariff_element.find("OperatorRef")
            if operator_ref is not None:
                try:
                    operator = Operator.objects.get(
                        noc=operator_ref.attrib["ref"].removeprefix("noc:")
                    )
                except Operator.DoesNotExist:
                    pass
                else:
                    tariff.operators.add(operator)

                    line_ref = tariff_element.find("LineRef")
                    if line_ref is not None:
                        line_id = line_ref.attrib["ref"]
                        line_name = lines[line_id].findtext("PublicCode")
                        service = get_service(operator, line_name, line_id)
                        if service:
                            tariff.services.add(service)

            distance_matrix_elements = {}
            if fare_structure_elements is not None:
                distance_matrix_element_elements = fare_structure_elements.find(
                    "FareStructureElement/distanceMatrixElements"
                )
                if distance_matrix_element_elements is not None:
                    for distance_matrix_element in distance_matrix_element_elements:
                        price_group_ref = distance_matrix_element.find(
                            "priceGroups/PriceGroupRef"
                        )
                        if price_group_ref is None:
                            continue
                        price_group_ref = price_group_ref.attrib["ref"]

                        start_zone = distance_matrix_element.find(
                            "StartTariffZoneRef"
                        ).attrib["ref"]
                        end_zone = distance_matrix_element.find(
                            "EndTariffZoneRef"
                        ).attrib["ref"]

                        distance_matrix_element = models.DistanceMatrixElement(
                            code=distance_matrix_element.attrib["id"],
                            start_zone=fare_zones[start_zone],
                            end_zone=fare_zones[end_zone],
                            price=price_groups[price_group_ref],
                            tariff=tariff,
                        )
                        distance_matrix_elements[distance_matrix_element.code] = (
                            distance_matrix_element
                        )
                models.DistanceMatrixElement.objects.bulk_create(
                    distance_matrix_elements.values()
                )

            if fare_structure_elements is not None:
                access_zones = fare_structure_elements.find(
                    "FareStructureElement/GenericParameterAssignment/validityParameters/FareZoneRef"
                )
                if access_zones is not None:
                    tariff.access_zones.add(fare_zones[access_zones.attrib["ref"]])

            time_intervals_element = tariff_element.find("timeIntervals")
            if time_intervals_element is not None:
                for time_interval in time_intervals_element:
                    time_interval, _ = models.TimeInterval.objects.get_or_create(
                        code=time_interval.attrib["id"],
                        name=time_interval.findtext("Name"),
                        description=time_interval.findtext("Description", ""),
                    )
                    time_intervals[time_interval.code] = time_interval

        for fare_table_element in element.findall(
            "dataObjects/CompositeFrame/frames/FareFrame/fareTables/FareTable"
        ):
            tariff_ref = fare_table_element.find("usedIn/TariffRef")
            if tariff_ref is None:
                pass
            else:
                tariff_ref = tariff_ref.attrib["ref"]
                tariff = tariffs[tariff_ref]

            user_profile_ref = fare_table_element.find("pricesFor/UserProfileRef")
            if user_profile_ref is not None:
                user_profile_ref = user_profile_ref.attrib["ref"]
                if user_profile_ref not in user_profiles:
                    user_profile_ref = f"fxc:{user_profile_ref}"
                user_profile = user_profiles[user_profile_ref]
            else:
                user_profile = None

            sales_offer_package_ref = fare_table_element.find(
                "pricesFor/SalesOfferPackageRef"
            )
            if sales_offer_package_ref is not None:
                sales_offer_package_ref = sales_offer_package_ref.attrib["ref"]
                sales_offer_package = sales_offer_packages[sales_offer_package_ref]
            else:
                sales_offer_package = None

            preassigned_fare_product_ref = fare_table_element.find(
                "pricesFor/PreassignedFareProductRef"
            )
            if preassigned_fare_product_ref is not None:
                preassigned_fare_product_ref = preassigned_fare_product_ref.attrib[
                    "ref"
                ]
                preassigned_fare_product = fare_products[preassigned_fare_product_ref]
            else:
                preassigned_fare_product = None

            columns_element = fare_table_element.find("columns")
            rows_element = fare_table_element.find("rows")

            if columns_element is not None and rows_element is not None:
                table, created = models.FareTable.objects.update_or_create(
                    {
                        "user_profile": user_profile,
                        "sales_offer_package": sales_offer_package,
                        "preassigned_fare_product": preassigned_fare_product,
                        "description": fare_table_element.findtext("Description", ""),
                    },
                    tariff=tariff,
                    code=fare_table_element.attrib["id"],
                    name=fare_table_element.findtext("Name", ""),
                )

                assert created

                if not created:
                    table.column_set.all().delete()
                    table.row_set.all().delete()

                columns = {}
                if columns_element is not None:
                    for column in columns_element:
                        column = models.Column(
                            table=table,
                            code=column.attrib["id"],
                            name=column.findtext("Name"),
                            order=column.attrib.get("order"),
                        )
                        columns[column.code] = column
                models.Column.objects.bulk_create(columns.values())

                rows = {}
                if rows_element is not None:
                    for row in rows_element:
                        row = models.Row(
                            table=table,
                            code=row.attrib["id"],
                            name=row.findtext("Name"),
                            order=row.attrib.get("order"),
                        )
                        rows[row.code] = row
                models.Row.objects.bulk_create(rows.values())

            else:
                table = None

                # Stagecoach
                distance_matrix_elements = tariff_element.find("distanceMatrixElements")
                if distance_matrix_elements is not None:
                    distance_matrix_elements = {
                        element.attrib["id"]: element
                        for element in distance_matrix_elements
                    }

                    for price in fare_table_element.findall(
                        "prices/DistanceMatrixElementPrice"
                    ):
                        distance_matrix_element_ref = price.find(
                            "DistanceMatrixElementRef"
                        ).attrib["ref"]
                        distance_matrix_element = distance_matrix_elements[
                            distance_matrix_element_ref
                        ]
                        amount = price.findtext("Amount")
                        if amount not in prices:
                            prices[amount] = models.Price.objects.create(amount=amount)
                        start_zone = distance_matrix_element.find(
                            "StartTariffZoneRef"
                        ).attrib["ref"]
                        end_zone = distance_matrix_element.find(
                            "EndTariffZoneRef"
                        ).attrib["ref"]
                        models.DistanceMatrixElement.objects.create(
                            tariff=tariff,
                            start_zone=fare_zones[start_zone],
                            end_zone=fare_zones[end_zone],
                            price=prices[amount],
                        )

            cells = []

            for sub_fare_table_element in fare_table_element.findall(
                "includes/FareTable"
            ):
                # fare tables within fare tables
                cells_element = sub_fare_table_element.find("cells")
                if cells_element is not None:
                    for cell_element in cells_element:
                        distance_matrix_element_price = cell_element.find(
                            "DistanceMatrixElementPrice"
                        )
                        if distance_matrix_element_price is None:
                            continue

                        price_ref = distance_matrix_element_price.find(
                            "GeographicalIntervalPriceRef"
                        )
                        if price_ref is None:
                            continue

                        distance_matrix_element_ref = (
                            distance_matrix_element_price.find(
                                "DistanceMatrixElementRef"
                            )
                        )
                        distance_matrix_element = distance_matrix_elements[
                            distance_matrix_element_ref.attrib["ref"]
                        ]

                        column_ref = cell_element.find("ColumnRef").attrib["ref"]
                        column = columns.get(column_ref)

                        row_ref = cell_element.find("RowRef").attrib["ref"]
                        row = rows.get(row_ref)

                        if row is None or column is None:
                            continue

                        cells.append(
                            models.Cell(
                                column=column,
                                row=row,
                                price=price_group_prices[price_ref.attrib["ref"]],
                                distance_matrix_element=distance_matrix_element,
                            )
                        )

                sales_offer_package_ref = sub_fare_table_element.find(
                    "pricesFor/SalesOfferPackageRef"
                )
                if sales_offer_package_ref is not None:
                    sales_offer_package_ref = sales_offer_package_ref.attrib["ref"]
                    sales_offer_package = sales_offer_packages[sales_offer_package_ref]
                else:
                    sales_offer_package = None

                includes = sub_fare_table_element.find("includes")
                if includes is not None:
                    for sub_sub_fare_table_element in includes:
                        cells_element = sub_sub_fare_table_element.find("cells")
                        if cells_element is not None:
                            for cell_element in cells_element:
                                time_interval_price = cell_element.find(
                                    "TimeIntervalPrice"
                                )
                                if time_interval_price is not None:
                                    time_interval = time_interval_price.find(
                                        "TimeIntervalRef"
                                    )
                                    if time_interval is not None:
                                        time_interval = time_intervals[
                                            time_interval.attrib["ref"]
                                        ]
                                    price, created = models.Price.objects.get_or_create(
                                        amount=time_interval_price.findtext("Amount"),
                                        time_interval=time_interval,
                                        tariff=tariff,
                                        sales_offer_package=sales_offer_package,
                                        user_profile=user_profile,
                                    )

            models.Cell.objects.bulk_create(cells)

        # Stagecoach has user profiles and sales offer packages defined separately
        if "_COMMON_" in filename:
            if user_profiles:
                self.user_profiles = user_profiles
            if sales_offer_packages:
                self.sales_offer_packages = sales_offer_packages
            if fare_products:
                self.fare_products = fare_products

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("api_key", type=str)

    def handle_archive(self, dataset, file):
        filenames = set()
        with zipfile.ZipFile(file) as archive:
            for filename in archive.namelist():
                if filename in filenames:
                    logger.warning(f"duplicate filename {filename} in {archive}")
                else:
                    self.handle_file(dataset, archive.open(filename), filename)
                    filenames.add(filename)

    def handle_bods_dataset(self, item):
        download_url = item["url"]
        name = item["name"]
        description = item["description"]
        dataset_url = download_url.removesuffix("download/")
        modified = datetime.fromisoformat(item["modified"])
        try:
            dataset = models.DataSet.objects.get(url=dataset_url)
        except models.DataSet.DoesNotExist:
            dataset = models.DataSet(url=dataset_url)

        if dataset.name != name or dataset.description != description:
            dataset.name = name
            dataset.description = description
            dataset.save()

        if dataset.id:
            if dataset.datetime == modified:
                return dataset  # data hasn't changed

            dataset.tariff_set.all().delete()

        logger.info(dataset)

        with log_time_taken(logger):
            try:
                dataset.operators.set(item["noc"])
            except IntegrityError:
                logger.warning(item["noc"])

            response = self.session.get(download_url, stream=True, timeout=61)

            self.user_profiles = {}
            self.sales_offer_packages = {}
            self.fare_products = {}
            self.fare_zones = get_existing_fare_zones(dataset)

            if (
                content_type := response.headers["Content-Type"]
            ) == "text/xml" or content_type == "application/xml":
                # maybe not fully RFC 6266 compliant
                filename = filename_from_content_disposition(response)
                self.handle_file(dataset, response.raw, filename)
            else:
                assert content_type == "application/zip"
                try:
                    self.handle_archive(dataset, io.BytesIO(response.content))
                except (KeyError, DataError):
                    # don't update timestamp field, try re-importing next time
                    return dataset

        dataset.datetime = modified
        dataset.save(update_fields=["datetime"])
        return dataset

    def ticketer(self, noc):
        download_url = f"https://opendata.ticketer.com/uk/{noc}/fares/current.zip"

        dataset, _created = models.DataSet.objects.get_or_create(
            {"name": f"{noc}"}, url=download_url
        )

        headers = {}
        if dataset.datetime:
            headers["if-modified-since"] = http_date(dataset.datetime.timestamp())

        response = self.session.get(
            download_url, headers=headers, stream=True, timeout=61
        )
        response.raise_for_status()

        if response.status_code == 304:
            return dataset

        last_modified = response.headers["last-modified"]
        last_modified = parse_http_date(last_modified)
        last_modified = datetime.fromtimestamp(last_modified, UTC)

        if dataset.datetime == last_modified:
            return dataset

        logger.info(noc)

        with log_time_taken(logger):
            dataset.tariff_set.all().delete()

            self.user_profiles = {}
            self.sales_offer_packages = {}
            self.fare_products = {}
            self.fare_zones = get_existing_fare_zones(dataset)

            self.handle_archive(dataset, io.BytesIO(response.content))

        dataset.datetime = last_modified
        dataset.save(update_fields=["datetime"])
        return dataset

    def bod(self, api_key):
        datasets = []
        url = f"{self.base_url}/api/v1/fares/dataset/"
        params = {
            "api_key": api_key,
            "status": "published",
            "limit": 100,
        }
        while url:
            response = self.session.get(url, params=params, timeout=61)

            data = response.json()

            for item in data["results"]:
                try:
                    dataset = self.handle_bods_dataset(item)
                    if dataset:
                        datasets.append(dataset.id)
                except (TypeError, IntegrityError) as e:
                    print(e)

            url = data["next"]
            params = None

        # remove removed datasets
        old = models.DataSet.objects.filter(url__startswith=self.base_url).exclude(
            id__in=datasets
        )
        if old:
            logger.info(f"deleting {old}")
            logger.info(f"deleted {old.delete()}")

    def handle(self, api_key, **options):
        self.session = requests.Session()
        max_price_id = (
            models.Price.objects.order_by("-id").values_list("id", flat=True).first()
        )

        if api_key == "ticketer":
            for noc in (
                "FECS",
                "FESX",
                "FCWL",
                "FGLA",
                "FSYO",
                "FWYO",
                "FYOR",
            ):
                self.ticketer(noc)
        else:
            assert len(api_key) == 40
            self.bod(api_key)

        if max_price_id:
            delete_unused_prices(max_price_id)
