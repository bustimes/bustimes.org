from functools import cached_property

from django.contrib.postgres.fields import DateTimeRangeField
from django.db import models
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import camel_case_to_spaces

# class Currency(models.TextChoices):
#     GBP = "GBP", "Pound Sterling"
#     EUR = "EUR", "Euro"


# currency = models.CharField(
#     max_length=3,
#     choices=Currency.choices,
#     default=Currency.GBP,
# )


class DataSet(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    description = models.CharField(max_length=255, blank=True)
    operators = models.ManyToManyField(
        "busstops.Operator", blank=True, through="DataSetOperator"
    )
    datetime = models.DateTimeField(null=True, blank=True)
    published = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("dataset_detail", args=(self.id,))

    def nice_name(self):
        return self.name.split("_", 1)[0]

    def credit(self):
        text = self.nice_name()

        if self.url:
            if "bus-data.dft.gov.uk" in self.url:
                text = f"{text}/Bus Open Data Service"

            text = format_html('<a href="{}" rel="nofollow">{}</a>', self.url, text)

        if self.datetime:
            text = f"{text}, {self.datetime:%-d %B %Y}"

        return mark_safe(f'<p class="credit">Fares data from {text}</p>')


class DataSetOperator(models.Model):
    dataset = models.ForeignKey(
        DataSet, models.DB_CASCADE, related_name="datasetoperator+"
    )
    operator = models.ForeignKey(
        "busstops.Operator", models.DB_CASCADE, related_name="datasetoperator+"
    )

    class Meta:
        db_table = "fares_dataset_operators"
        unique_together = ("dataset", "operator")


class TimeInterval(models.Model):
    code = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    def __str__(self):
        return self.description


class SalesOfferPackage(models.Model):
    code = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=255, blank=True)
    cash = models.BooleanField(default=False)
    contactless = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class PreassignedFareProduct(models.Model):
    code = models.CharField(max_length=140, blank=True)
    name = models.CharField(max_length=140, blank=True)
    charging_moment = models.CharField(max_length=25, blank=True)
    tariff_basis = models.CharField(max_length=25)


class UserProfile(models.Model):
    code = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)
    proof_required = models.CharField(max_length=255, blank=True)
    discount_basis = models.CharField(max_length=255, blank=True)
    min_age = models.PositiveSmallIntegerField(null=True, blank=True)
    max_age = models.PositiveSmallIntegerField(null=True, blank=True)

    def __str__(self):
        return camel_case_to_spaces(self.name)


class TypeOfTariff(models.TextChoices):
    # DISTANCE_KILOMETERS = "Distance_kilometers", "Kilometer Distance Kilometers"
    # FLAT = "flat", "Flat"
    POINT_TO_PONT = "point_to_point", "Point to point"
    ZONAL = "zonal", "Zonal"
    # SECTION = "section", "Section"    # BANDED = "banded", "Section"
    # STORED_VALUE = "stored_value", "Stored value"
    # MULTITRIP = "multitrip", "Multitrip carnet"


class Tariff(models.Model):
    code = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    services = models.ManyToManyField("busstops.Service", blank=True)
    operators = models.ManyToManyField(
        "busstops.Operator", blank=True, through="TariffOperator"
    )
    source = models.ForeignKey(DataSet, models.CASCADE)
    filename = models.CharField(max_length=255)
    user_profile = models.ForeignKey(UserProfile, models.CASCADE, null=True, blank=True)
    trip_type = models.CharField(max_length=255, blank=True)
    valid_between = DateTimeRangeField(null=True, blank=True)
    type_of_tariff = models.CharField(max_length=19, choices=TypeOfTariff, blank=True)
    access_zones = models.ManyToManyField("FareZone", blank=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("tariff_detail", args=(self.id,))

    class Meta:
        unique_together = ("source", "filename", "code")


class TariffOperator(models.Model):
    # Tariff is deleted by Python; the ON DELETE CASCADE comes from the migration
    tariff = models.ForeignKey(
        Tariff, models.DO_NOTHING, related_name="tariffoperator+"
    )
    operator = models.ForeignKey(
        "busstops.Operator", models.DB_CASCADE, related_name="tariffoperator+"
    )

    class Meta:
        db_table = "fares_tariff_operators"
        unique_together = ("tariff", "operator")


class Price(models.Model):
    amount = models.DecimalField(max_digits=6, decimal_places=2)  # maximum £9999.99
    time_interval = models.ForeignKey(
        TimeInterval, models.CASCADE, null=True, blank=True
    )
    user_profile = models.ForeignKey(UserProfile, models.CASCADE, null=True, blank=True)
    sales_offer_package = models.ForeignKey(
        SalesOfferPackage, models.CASCADE, null=True, blank=True
    )
    tariff = models.ForeignKey(Tariff, models.CASCADE, null=True, blank=True)

    def __str__(self):
        if self.amount and self.amount >= 10:
            return str(self.amount).removesuffix(".00")
        return str(self.amount)


class FareTable(models.Model):
    code = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)
    user_profile = models.ForeignKey(UserProfile, models.CASCADE, null=True, blank=True)
    sales_offer_package = models.ForeignKey(
        SalesOfferPackage, models.CASCADE, null=True, blank=True
    )
    preassigned_fare_product = models.ForeignKey(
        PreassignedFareProduct, models.CASCADE, null=True, blank=True
    )
    tariff = models.ForeignKey(Tariff, models.CASCADE)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("table_detail", args=(self.id,))

    @cached_property
    def is_triangular(self):
        cols = [col.name for col in self.column_set.all()]
        rows = [row.name for row in self.row_set.all()]
        rows.reverse()
        if len(cols) == len(rows) and cols[1:] == rows[:-1]:
            return True

    @cached_property
    def staircase(self):
        """Each stop once on the diagonal, heading both its column (fares from)
        and its row (fares to). For a table of fares in one direction only
        (like Stagecoach's), or the same fares in both directions
        """
        if self.is_triangular:
            return

        def get_key(line):
            # Stagecoach zone codes identify stops visited twice, like on a circular route
            for suffix in ("@boarding", "@alighting"):
                if line.code.endswith(suffix):
                    return line.code.removesuffix(suffix)
            return line.name

        columns = list(self.column_set.all())
        column_keys = {column.id: get_key(column) for column in columns}
        stops = list(column_keys.values())
        if len(set(stops)) != len(stops):
            return
        names = {get_key(column): column.name for column in columns}

        prices = {}
        for row in self.row_set.all():
            cells = row.cell_set.all()
            if not cells:
                continue
            key = get_key(row)
            if key not in names:
                # insert before the first stop this row has a fare to
                first_column = min(
                    cells, key=lambda cell: stops.index(column_keys[cell.column_id])
                )
                stops.insert(stops.index(column_keys[first_column.column_id]), key)
                names[key] = row.name
            for cell in cells:
                prices[key, column_keys[cell.column_id]] = cell.price

        if not prices:
            return
        position = {stop: i for i, stop in enumerate(stops)}
        for (a, b), price in list(prices.items()):
            if position[a] > position[b]:
                # a fare in the other direction, only if it's the same
                if prices.get((b, a)) != price:
                    return
                del prices[a, b]
        has_diagonal = any(a == b for a, b in prices)

        ids = [f"fare-table-{self.id}-{i}" for i in range(len(stops))]
        return [
            {
                "name": names[stop],
                "id": ids[i],
                "colspan": len(stops) - i,
                "cells": [
                    {
                        "price": prices.get((stops[j], stop)),
                        "headers": f"{ids[j]} {ids[i]}" if j < i else ids[i],
                    }
                    for j in range(i + 1 if has_diagonal else i)
                ],
            }
            for i, stop in enumerate(stops)
        ]

    def columns(self):
        return self.column_set.all()

    def rows(self):
        cols = {col.id: col for col in self.column_set.all()}

        rows = list(self.row_set.all())
        for row in rows:
            for cell in row.cell_set.all():
                cell.column = cols[cell.column_id]
        if self.is_triangular:
            for i, row in enumerate(rows, start=1):
                row.colspan = i
            rows.reverse()
        else:
            # rows may be out of order (e.g. stops added when a route was extended)
            column_order = {col.name: col.order for col in cols.values()}
            if len(column_order) == len(cols) and all(
                column_order.get(row.name) is not None for row in rows
            ):
                rows.sort(key=lambda row: column_order[row.name])

        return rows


class FareZone(models.Model):
    code = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    stops = models.ManyToManyField("busstops.StopPoint", blank=True)
    source = models.ForeignKey(DataSet, models.CASCADE, null=True, blank=True)

    def __str__(self):
        return self.name


class DistanceMatrixElement(models.Model):
    code = models.CharField(max_length=255)
    price = models.ForeignKey(Price, models.DO_NOTHING)
    start_zone = models.ForeignKey(FareZone, models.CASCADE, related_name="starting")
    end_zone = models.ForeignKey(FareZone, models.CASCADE, related_name="ending")
    tariff = models.ForeignKey(Tariff, models.CASCADE)

    def html(self):
        if self.tariff.user_profile:
            tariff = self.tariff.user_profile
            if self.tariff.trip_type:
                tariff = f"{tariff} {self.tariff.trip_type}"
        else:
            tariff = self.tariff

        return f"{tariff}: £{self.price.amount}"

    def __str__(self):
        return self.code


class Column(models.Model):
    table = models.ForeignKey(FareTable, models.CASCADE)
    code = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255)
    order = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("order",)

    def __str__(self):
        return self.name


class Row(models.Model):
    table = models.ForeignKey(FareTable, models.CASCADE)
    code = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255)
    order = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("order",)

    def cells(self):
        prev_order = 0
        for cell in self.cell_set.all():
            while cell.column.order - prev_order > 1:
                yield None
                prev_order += 1
            yield cell
            prev_order = cell.column.order
        while prev_order <= (len(self.table.row_set.all()) - self.order):
            yield None
            prev_order += 1
        # return self.cell_set.all()

    def __str__(self):
        return self.name


class Cell(models.Model):
    column = models.ForeignKey(Column, models.CASCADE)
    row = models.ForeignKey(Row, models.CASCADE)
    distance_matrix_element = models.ForeignKey(
        DistanceMatrixElement, models.CASCADE, null=True
    )
    price = models.ForeignKey(Price, models.DO_NOTHING, null=True)


# GTFS fare_attributes.txt and fare_rules.txt


class Fare(models.Model):
    source = models.ForeignKey("busstops.DataSource", models.CASCADE)
    fare_id = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    currency = models.CharField(max_length=3)
    payment_method = models.PositiveSmallIntegerField()
    transfers = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ("source", "fare_id")


class FareRule(models.Model):
    fare = models.ForeignKey(Fare, models.CASCADE)
    service = models.ForeignKey(
        "busstops.Service", models.DO_NOTHING, null=True, blank=True
    )
    origin = models.ForeignKey(
        "busstops.StopPoint",
        models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="origin_rules",
    )
    destination = models.ForeignKey(
        "busstops.StopPoint",
        models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="destination_rules",
    )
