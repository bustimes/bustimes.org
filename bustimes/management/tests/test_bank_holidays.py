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

        # Christmas Eve and NYE are not really bank holidays,
        # only they are the world of TransXChange,
        # so we must create them manually elsewhere
        self.assertEqual(
            0, BankHolidayDate.objects.filter(bank_holiday__name="ChristmasEve").count()
        )
        self.assertEqual(
            0, BankHolidayDate.objects.filter(bank_holiday__name="NewYearsEve").count()
        )

        self.assertEqual(
            7, BankHolidayDate.objects.filter(bank_holiday__name="ChristmasDay").count()
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

        # debug view
        response = self.client.get("/bank_holidays")
        self.assertNotContains(response, " is not in ")
