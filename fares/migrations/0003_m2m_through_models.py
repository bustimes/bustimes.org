# The tables already exist, as implicit many-to-many tables; all that's new is
# the explicit through models describing them.  See buses/migration_utils.py.

import django.db.models.deletion
from django.db import migrations, models

from buses.migration_utils import cascade


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0019_m2m_through_models"),
        ("fares", "0002_fare_farerule"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="DataSetOperator",
                    fields=[
                        (
                            "id",
                            models.AutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "dataset",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="datasetoperator+",
                                to="fares.dataset",
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="datasetoperator+",
                                to="busstops.operator",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "fares_dataset_operators",
                        "unique_together": {("dataset", "operator")},
                    },
                ),
                migrations.AlterField(
                    model_name="dataset",
                    name="operators",
                    field=models.ManyToManyField(
                        blank=True,
                        through="fares.DataSetOperator",
                        to="busstops.operator",
                    ),
                ),
                migrations.CreateModel(
                    name="TariffOperator",
                    fields=[
                        (
                            "id",
                            models.AutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="tariffoperator+",
                                to="busstops.operator",
                            ),
                        ),
                        (
                            "tariff",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="tariffoperator+",
                                to="fares.tariff",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "fares_tariff_operators",
                        "unique_together": {("tariff", "operator")},
                    },
                ),
                migrations.AlterField(
                    model_name="tariff",
                    name="operators",
                    field=models.ManyToManyField(
                        blank=True,
                        through="fares.TariffOperator",
                        to="busstops.operator",
                    ),
                ),
            ],
            database_operations=cascade(
                ("fares_dataset_operators", "dataset_id", "fares_dataset", "id"),
                ("fares_dataset_operators", "operator_id", "busstops_operator", "noc"),
                ("fares_tariff_operators", "tariff_id", "fares_tariff", "id"),
                ("fares_tariff_operators", "operator_id", "busstops_operator", "noc"),
            ),
        ),
    ]
