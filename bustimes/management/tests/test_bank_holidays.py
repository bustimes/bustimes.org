from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from vcr import use_cassette

from ...models import (
    BankHoliday,
    BankHolidayDate,
)


class BankHolidaysTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        bhs = BankHoliday.objects.bulk_create(
            BankHoliday(name=name)
            for name in (
                "AllHolidaysExceptChristmas",
                "Holidays",
                "DisplacementHolidays",
                "StAndrewsDayHoliday",
                "AugustBankHolidayScotland",
                "StAndrewsDay",
                "EarlyRunOffDays",
                "Christmas",
                "AllBankHolidays",
                "HolidayMondays",
                "Jan2ndScotlandHoliday",
                "Jan2ndScotland",
                "NewYearsEve",
                "ChristmasEve",
                "NewYearsDayHoliday",
                "BoxingDayHoliday",
                "ChristmasDayHoliday",
                "SpringBank",
                "EasterMonday",
                "MayDay",
                "LateSummerBankHolidayNotScotland",
                "NewYearsDay",
                "GoodFriday",
                "BoxingDay",
                "ChristmasDay",
            )
        )
        # create one Christmas Day date
        BankHolidayDate.objects.create(bank_holiday=bhs[-1], date="2025-12-25")

    def test_bank_holidays(self):
        fixtures_dir = Path(__file__).resolve().parent / "fixtures"

        with (
            use_cassette(fixtures_dir / "bank_holidays.yaml") as cassette,
            self.assertNumQueries(4),
            self.assertLogs(
                "bustimes.management.commands.bank_holidays", "WARNING"
            ) as cm,
        ):
            call_command("bank_holidays")
            cassette.rewind()
            call_command("bank_holidays")  # test for idempotence

        # it should warn about special bank holidays it doesn't know what to do with
        self.assertIn(
            "WARNING:bustimes.management.commands.bank_holidays:WorldCupbankholiday",
            cm.output,
        )
        self.assertIn(
            "WARNING:bustimes.management.commands.bank_holidays:BankHolidayfortheStateFuneralofQueenElizabethII",
            cm.output,
        )

        years = {
            d.year
            for d in BankHolidayDate.objects.filter(
                bank_holiday__name="AllBankHolidays"
            ).values_list("date", flat=True)
        }
        self.assertEqual(
            sorted(years),
            sorted(
                d.year
                for d in BankHolidayDate.objects.filter(
                    bank_holiday__name="ChristmasEve", date__month=12, date__day=24
                ).values_list("date", flat=True)
            ),
        )
        self.assertEqual(
            len(years),
            BankHolidayDate.objects.filter(
                bank_holiday__name="EarlyRunOffDays", date__month=12, date__day=31
            ).count(),
        )

        self.assertEqual(
            len(years),
            BankHolidayDate.objects.filter(bank_holiday__name="ChristmasDay").count(),
        )

        def get_scotland(name, date):
            return BankHolidayDate.objects.get(
                bank_holiday__name=name, date=date
            ).scotland

        self.assertIsNone(get_scotland("SpringBank", "2027-05-31"))
        self.assertIsNone(get_scotland("HolidayMondays", "2027-05-31"))
        self.assertIsNone(get_scotland("AllBankHolidays", "2027-05-31"))
        self.assertIsNone(get_scotland("EasterMonday", "2026-04-06"))
        self.assertIs(get_scotland("HolidayMondays", "2026-04-06"), False)
        self.assertIs(get_scotland("AllHolidaysExceptChristmas", "2026-04-06"), False)
        self.assertIs(get_scotland("HolidayMondays", "2026-08-03"), True)
        self.assertIsNone(get_scotland("Holidays", "2026-04-03"))

        # Saturday - the gov.uk data only has the substitute day (BoxingDayHoliday)
        self.assertIsNone(get_scotland("BoxingDay", "2026-12-26"))
        self.assertIsNone(get_scotland("Christmas", "2026-12-26"))
        self.assertIsNone(get_scotland("BoxingDayHoliday", "2026-12-28"))
        self.assertIs(get_scotland("AllBankHolidays", "2027-01-02"), True)

        # debug view
        response = self.client.get("/bank_holidays")
        self.assertNotContains(response, " is not in ")
