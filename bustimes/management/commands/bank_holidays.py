import logging
from datetime import date

from django.core.management.base import BaseCommand
from govuk_bank_holidays.bank_holidays import BankHolidays

from bustimes.models import BANK_HOLIDAY_GROUPS, BankHoliday, BankHolidayDate

logger = logging.getLogger(__name__)

BANK_HOLIDAY_PARENTS = {
    child: parent
    for parent, children in BANK_HOLIDAY_GROUPS.items()
    for child in children
}


# in TransXChange these are always on the same date,
# even at a weekend (unlike in the gov.uk data)
FIXED_DATES = (
    ("NewYearsDay", 1, 1, False),
    ("Jan2ndScotland", 1, 2, True),
    ("StAndrewsDay", 11, 30, True),
    ("ChristmasEve", 12, 24, False),
    ("ChristmasDay", 12, 25, False),
    ("BoxingDay", 12, 26, False),
    ("NewYearsEve", 12, 31, False),
)


def get_bank_holiday_name(bank_holiday):
    title = bank_holiday["title"].replace("\u2019", "").replace(" ", "")
    if bank_holiday["notes"] == "Substitute day":
        title += "Holiday"
    return title


class Command(BaseCommand):
    def handle(self, **options):
        bhs = {bh.name: bh for bh in BankHoliday.objects.all()}

        bank_holidays = BankHolidays()

        # England, Wales and Scotland

        dates = {}

        def add(name, date, scotland):
            if name not in bhs:
                return
            key = (name, date)
            if key in dates and dates[key] != scotland:
                scotland = None
            dates[key] = scotland

        def add_with_ancestors(name, date, scotland):
            add(name, date, None)
            while name in BANK_HOLIDAY_PARENTS:
                name = BANK_HOLIDAY_PARENTS[name]
                add(name, date, scotland)

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
                add_with_ancestors(
                    title,
                    bank_holiday["date"],
                    False if bank_holiday not in scotland else None,
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
                add_with_ancestors(title, bank_holiday["date"], True)
            else:
                logger.warning(title)

        for year in {bank_holiday["date"].year for bank_holiday in england_and_wales}:
            for name, month, day, scotland_only in FIXED_DATES:
                add_with_ancestors(
                    name, date(year, month, day), True if scotland_only else None
                )

        BankHolidayDate.objects.bulk_create(
            [
                BankHolidayDate(bank_holiday=bhs[name], date=date, scotland=scotland)
                for (name, date), scotland in dates.items()
            ],
            update_conflicts=True,
            unique_fields=("bank_holiday", "date"),
            update_fields=("scotland",),
        )
