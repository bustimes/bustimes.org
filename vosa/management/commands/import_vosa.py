import csv
import logging
from datetime import datetime

from django.conf import settings
from django.core.management import BaseCommand
from django.db.models import Q

from busstops.models import DataSource
from bustimes import download_utils

from ...models import Licence, Registration, Variation

logger = logging.getLogger(__name__)


def parse_date(date_string: str):
    if date_string:
        return datetime.strptime(date_string, "%d/%m/%y").date()


def download_if_modified(path: str):
    url = f"https://content.mgmt.dvsacloud.uk/olcs.app.prod.dvsa.aws/data-gov-uk-export/{path}"
    source, _ = DataSource.objects.get_or_create({"url": url}, name=path)
    if url != source.url:
        source.url = url
        source.save(update_fields=["url"])
    return download_utils.download_if_modified(settings.DATA_DIR / path, source)


class Command(BaseCommand):
    @staticmethod
    def add_arguments(parser):
        parser.add_argument("regions", nargs="?", type=str, default="FBCMKGDH")

    def get_rows(self, path: str):
        with open(settings.DATA_DIR / path) as open_file:
            yield from csv.DictReader(open_file)

    def handle(self, regions: str, **kwargs) -> None:
        """call handle_region for each region if that region's data has changed"""

        for region in regions:
            modified_1, last_modified_1 = download_if_modified(
                f"Bus_RegisteredOnly_{region}.csv"
            )
            modified_2, last_modified_2 = download_if_modified(
                f"Bus_Variation_{region}.csv"
            )
            if modified_1 or modified_2:
                logger.info(f"{region} {last_modified_1} {last_modified_2}")
                self.handle_region(region)

    def get_existing_variations(self, region) -> dict:
        variations = Variation.objects.filter(
            Q(registration__licence__traffic_area=region)
            | Q(registration__licence__traffic_area="")
        )
        variations = variations.select_related("registration").all()
        variations_dict = {}
        for variation in variations:
            reg_no = variation.registration.registration_number
            if reg_no in variations_dict:
                variations_dict[reg_no][variation.variation_number] = variation
            else:
                variations_dict[reg_no] = {variation.variation_number: variation}
        return variations_dict

    def handle_region(self, region: str) -> None:
        lics = Licence.objects.filter(Q(traffic_area=region) | Q(traffic_area=""))
        lics = lics.in_bulk(field_name="licence_number")
        lics_to_update = set()
        lics_to_create = []

        regs = Registration.objects.filter(
            Q(licence__traffic_area=region) | Q(licence__traffic_area="")
        )
        regs = regs.in_bulk(field_name="registration_number")
        regs_to_update = set()
        regs_to_create = []

        variations = self.get_existing_variations(region)

        vars_to_update = []
        vars_to_create = []

        reg_no = None
        var_no = None
        # cardinals = set()

        for line in self.get_rows(f"Bus_Variation_{region}.csv"):
            prev_reg_no = reg_no
            prev_var_no = var_no

            reg_no = line["Reg_No"]
            var_no = int(line["Variation Number"])

            if reg_no != prev_reg_no:
                max_var_no = var_no
            else:
                max_var_no = max(var_no, max_var_no)

            lic_no = line["Lic_No"]

            if lic_no in lics:
                licence = lics[lic_no]
                if licence.id and licence not in lics_to_update:
                    licence.trading_name = ""
                    lics_to_update.add(licence)
            else:
                licence = Licence(licence_number=lic_no)
                lics_to_create.append(licence)
                lics[lic_no] = licence

            licence.name = line["Op_Name"]

            # a licence can have multiple trading names
            if line["trading_name"] not in licence.trading_name:
                if licence.trading_name:
                    licence.trading_name = (
                        f"{licence.trading_name}\n{line['trading_name']}"
                    )
                else:
                    licence.trading_name = line["trading_name"]

            if licence.address != line["Address"]:
                if licence.address:
                    print(licence.address, line["Address"])
                licence.address = line["Address"]

            if licence.traffic_area:
                assert licence.traffic_area == line["Current Traffic Area"]
            else:
                licence.traffic_area = line["Current Traffic Area"]

            licence.discs = line["Discs in Possession"] or None
            licence.authorised_discs = line["AUTHDISCS"] or None
            licence.description = line["Description"]
            licence.granted_date = parse_date(line["Granted_Date"])
            licence.expiry_date = parse_date(line["Exp_Date"])
            licence.licence_status = line["Licence Status"]

            if len(reg_no) > 20:
                # PK0000098/PK0000098/364
                parts = reg_no.split("/")
                assert len(parts) == 3
                assert parts[0] == parts[1]
                reg_no = f"{parts[1]}/{parts[2]}"

            if reg_no in regs:
                registration = regs[reg_no]
                if registration.id and registration not in regs_to_update:
                    regs_to_update.add(registration)
            else:
                registration = Registration(
                    registration_number=reg_no, registered=False
                )
                regs_to_create.append(registration)
                regs[reg_no] = registration
            registration.licence = licence

            if prev_reg_no != reg_no or var_no >= max_var_no:
                status = line["Registration Status"]
                registration.registration_status = status

                if status == "New" or status == "Registered" or status == "Variation":
                    registration.registered = True
                elif (
                    status == "Admin Cancelled"
                    or status == "Cancellation"
                    or status == "Cancelled"
                ):
                    registration.registered = False

                registration.start_point = line["start_point"]
                registration.finish_point = line["finish_point"]
                registration.via = line["via"]
                registration.subsidies_description = line["Subsidies_Description"]
                registration.subsidies_details = line["Subsidies_Details"]
                registration.traffic_area_office_covered_by_area = line[
                    "TAO Covered BY Area"
                ]
                registration.service_number = line["Service Number"]

                # a registration can have multiple types
                if registration.service_type_description:
                    if (
                        line["Service_Type_Description"]
                        not in registration.service_type_description
                    ):
                        registration.service_type_description += (
                            f"\n{line['Service_Type_Description']}"
                        )
                else:
                    registration.service_type_description = line[
                        "Service_Type_Description"
                    ]

                if (
                    registration.authority_description
                    and line["Auth_Description"]
                    not in registration.authority_description
                    and len(registration.authority_description) < 255
                ):
                    registration.authority_description += (
                        f"\n{line['Auth_Description']}"
                    )
                    if len(registration.authority_description) > 255:  # too long
                        # some National Express coach services cover maaany authorities
                        registration.authority_description = (
                            f"{registration.authority_description[:254]}…"
                        )
                else:
                    registration.authority_description = line["Auth_Description"]

            if prev_reg_no == reg_no and prev_var_no == var_no:
                pass

            else:
                variation = Variation(
                    registration=registration, variation_number=var_no
                )
                if reg_no in variations:
                    if var_no in variations[reg_no]:
                        variation = variations[reg_no][var_no]
                        vars_to_update.append(variation)
                    else:
                        variations[reg_no][var_no] = variation
                        vars_to_create.append(variation)
                else:
                    variations[reg_no] = {var_no: variation}
                    vars_to_create.append(variation)

                variation.effective_date = parse_date(line["effective_date"])
                variation.date_received = parse_date(line["received_date"])
                variation.end_date = parse_date(line["end_date"])
                variation.service_type_other_details = line[
                    "Service_Type_Other_Details"
                ]
                variation.registration_status = line["Registration Status"]
                variation.publication_text = line["Pub_Text"]
                variation.short_notice = line["Short Notice"]

        # if a registration is in this file, it is current, not cancelled/expired
        for line in self.get_rows(f"Bus_RegisteredOnly_{region}.csv"):
            reg_no = line["Reg_No"]
            reg = regs[reg_no]
            reg.registration_status = line["Registration Status"]
            reg.registered = True

        Licence.objects.bulk_update(
            lics_to_update,
            [
                "name",
                "trading_name",
                "traffic_area",
                "discs",
                "authorised_discs",
                "description",
                "granted_date",
                "expiry_date",
                "address",
                "licence_status",
            ],
        )
        Licence.objects.bulk_create(lics_to_create)

        for registration in regs_to_create:
            registration.licence = registration.licence

        Registration.objects.bulk_update(
            regs_to_update,
            [
                "start_point",
                "finish_point",
                "via",
                "subsidies_description",
                "subsidies_details",
                "traffic_area_office_covered_by_area",
                "service_number",
                "service_type_description",
                "registration_status",
                "authority_description",
                "registered",
            ],
            batch_size=1000,
        )
        Registration.objects.bulk_create(regs_to_create)

        Variation.objects.bulk_create(vars_to_create)
        Variation.objects.bulk_update(
            vars_to_update,
            [
                "effective_date",
                "date_received",
                "end_date",
                "service_type_other_details",
                "registration_status",
                "publication_text",
                "short_notice",
            ],
            batch_size=1000,
        )

        # for reg in regs.values():
        #     if reg not in regs_to_create and reg not in regs_to_update:
        #         print(reg.registration_number, reg.delete())

        for lic in lics.values():
            if lic not in lics_to_create and lic not in lics_to_update:
                print(lic.licence_number, lic.delete())
