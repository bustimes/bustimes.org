# The tables already exist, as implicit many-to-many tables; all that's new is
# the explicit through models describing them.  See buses/migration_utils.py.

import django.db.models.deletion
from django.db import migrations, models

from buses.migration_utils import cascade


class Migration(migrations.Migration):
    dependencies = [
        ("vehicles", "0026_db_on_delete_vehiclejourney"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="VehicleHasFeature",
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
                            "vehicle",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="vehiclehasfeature+",
                                to="vehicles.vehicle",
                            ),
                        ),
                        (
                            "vehiclefeature",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="vehiclehasfeature+",
                                to="vehicles.vehiclefeature",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "vehicles_vehicle_features",
                        "unique_together": {("vehicle", "vehiclefeature")},
                    },
                ),
                migrations.AlterField(
                    model_name="vehicle",
                    name="features",
                    field=models.ManyToManyField(
                        blank=True,
                        through="vehicles.VehicleHasFeature",
                        to="vehicles.vehiclefeature",
                    ),
                ),
            ],
            database_operations=cascade(
                ("vehicles_vehicle_features", "vehicle_id", "vehicles_vehicle", "id"),
                (
                    "vehicles_vehicle_features",
                    "vehiclefeature_id",
                    "vehicles_vehiclefeature",
                    "id",
                ),
            ),
        ),
    ]
