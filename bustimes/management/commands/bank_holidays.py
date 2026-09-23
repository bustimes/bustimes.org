import logging

from django.core.management.base import BaseCommand
from govuk_bank_holidays.bank_holidays import BankHolidays

from bustimes.models import BankHoliday, BankHolidayDate

logger = logging.getLogger(__name__)


def get_bank_holiday_name(bank_holiday):
    title = bank_holiday["title"].replace("\u2019", "").replace(" ", "")
    if bank_holiday["notes"] == "Substitute day":
        title += "Holiday"
    return title


class Command(BaseCommand):
    def handle(self, **options):
        bhs = {bh.name: bh for bh in BankHoliday.objects.all()}

        bank_holidays = BankHolidays()

        # Northern Ireland

        bank_holiday_dates = [
            BankHolidayDate(
                bank_holiday=bhs["Northern Ireland bank holidays"],
                date=bank_holiday["date"],
            )
            for bank_holiday in bank_holidays.get_holidays(
                division=bank_holidays.NORTHERN_IRELAND
            )
        ]
        BankHolidayDate.objects.bulk_create(bank_holiday_dates, ignore_conflicts=True)

        # England, Wales and Scotland

        bank_holiday_dates = []

        scotland = bank_holidays.get_holidays(division=bank_holidays.SCOTLAND)
        england_and_wales = bank_holidays.get_holidays(
            division=bank_holidays.ENGLAND_AND_WALES
        )

        for bank_holiday in england_and_wales:
            title = get_bank_holiday_name(bank_holiday)
            match title:
                case "EarlyMaybankholiday" | "EarlyMaybankholiday(VEday)":
                    title = "MayDay"
                case "Springbankholiday":
                    title = "SpringBank"
                case "Summerbankholiday":
                    title = "LateSummerBankHolidayNotScotland"

            if title in bhs:
                bh = bhs[title]
                bank_holiday_dates.append(
                    BankHolidayDate(bank_holiday=bh, date=bank_holiday["date"])
                )
                if bank_holiday["notes"] == "Substitute day":
                    bank_holiday_dates.append(
                        BankHolidayDate(
                            bank_holiday=bhs["DisplacementHolidays"],
                            date=bank_holiday["date"],
                        )
                    )
                if title == "ChristmasDay" or title == "BoxingDay":
                    bank_holiday_dates.append(
                        BankHolidayDate(
                            bank_holiday=bhs["Christmas"],
                            date=bank_holiday["date"],
                        )
                    )
                bank_holiday_dates.append(
                    BankHolidayDate(
                        bank_holiday=bhs["AllBankHolidays"],
                        date=bank_holiday["date"],
                        scotland=(False if bank_holiday not in scotland else None),
                    )
                )
            else:
                logger.warning(title)

        for bank_holiday in scotland:
            if bank_holiday in england_and_wales:
                continue
            title = get_bank_holiday_name(bank_holiday)
            match title:
                case "2ndJanuary":
                    title = "Jan2ndScotland"
                case "2ndJanuaryHoliday":
                    title = "Jan2ndScotlandHoliday"
                case "Summerbankholiday":
                    title = "AugustBankHolidayScotland"

            if title in bhs:
                bh = bhs[title]
                bank_holiday_dates.append(
                    BankHolidayDate(bank_holiday=bh, date=bank_holiday["date"])
                )
                bank_holiday_dates.append(
                    BankHolidayDate(
                        bank_holiday=bhs["AllBankHolidays"],
                        date=bank_holiday["date"],
                        scotland=True,
                    )
                )
            else:
                logger.warning(title)

        BankHolidayDate.objects.bulk_create(bank_holiday_dates, ignore_conflicts=True)
