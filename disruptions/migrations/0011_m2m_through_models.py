# The tables already exist, as implicit many-to-many tables; all that's new is
# the explicit through models describing them.  See buses/migration_utils.py.

import django.db.models.deletion
from django.db import migrations, models

from buses.migration_utils import cascade


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0019_m2m_through_models"),
        ("disruptions", "0010_consequence_m2m_on_delete_cascade"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ConsequenceOperator",
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
                            "consequence",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="consequenceoperator+",
                                to="disruptions.consequence",
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="consequenceoperator+",
                                to="busstops.operator",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "disruptions_consequence_operators",
                        "unique_together": {("consequence", "operator")},
                    },
                ),
                migrations.AlterField(
                    model_name="consequence",
                    name="operators",
                    field=models.ManyToManyField(
                        blank=True,
                        through="disruptions.ConsequenceOperator",
                        to="busstops.operator",
                    ),
                ),
                migrations.CreateModel(
                    name="ConsequenceService",
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
                            "consequence",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="consequenceservice+",
                                to="disruptions.consequence",
                            ),
                        ),
                        (
                            "service",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="consequenceservice+",
                                to="busstops.service",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "disruptions_consequence_services",
                        "unique_together": {("consequence", "service")},
                    },
                ),
                migrations.AlterField(
                    model_name="consequence",
                    name="services",
                    field=models.ManyToManyField(
                        blank=True,
                        through="disruptions.ConsequenceService",
                        to="busstops.service",
                    ),
                ),
                migrations.CreateModel(
                    name="ConsequenceStop",
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
                            "consequence",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="consequencestop+",
                                to="disruptions.consequence",
                            ),
                        ),
                        (
                            "stoppoint",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="consequencestop+",
                                to="busstops.stoppoint",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "disruptions_consequence_stops",
                        "unique_together": {("consequence", "stoppoint")},
                    },
                ),
                migrations.AlterField(
                    model_name="consequence",
                    name="stops",
                    field=models.ManyToManyField(
                        blank=True,
                        through="disruptions.ConsequenceStop",
                        to="busstops.stoppoint",
                    ),
                ),
            ],
            database_operations=cascade(
                (
                    "disruptions_consequence_operators",
                    "consequence_id",
                    "disruptions_consequence",
                    "id",
                ),
                (
                    "disruptions_consequence_operators",
                    "operator_id",
                    "busstops_operator",
                    "noc",
                ),
                (
                    "disruptions_consequence_services",
                    "consequence_id",
                    "disruptions_consequence",
                    "id",
                ),
                (
                    "disruptions_consequence_services",
                    "service_id",
                    "busstops_service",
                    "id",
                ),
                (
                    "disruptions_consequence_stops",
                    "consequence_id",
                    "disruptions_consequence",
                    "id",
                ),
                (
                    "disruptions_consequence_stops",
                    "stoppoint_id",
                    "busstops_stoppoint",
                    "atco_code",
                ),
            ),
        ),
    ]
