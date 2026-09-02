# The tables already exist, as implicit many-to-many tables; all that's new is
# the explicit through models describing them.  See buses/migration_utils.py.

import django.db.models.deletion
from django.db import migrations, models

from buses.migration_utils import cascade


class Migration(migrations.Migration):
    dependencies = [
        ("photos", "0003_photo_dimensions_location_metadata"),
        ("vehicles", "0027_m2m_through_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="PhotoVehicle",
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
                            "photo",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="photovehicle+",
                                to="photos.photo",
                            ),
                        ),
                        (
                            "vehicle",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="photovehicle+",
                                to="vehicles.vehicle",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "photos_photo_vehicles",
                        "unique_together": {("photo", "vehicle")},
                    },
                ),
                migrations.AlterField(
                    model_name="photo",
                    name="vehicles",
                    field=models.ManyToManyField(
                        blank=True, through="photos.PhotoVehicle", to="vehicles.vehicle"
                    ),
                ),
            ],
            database_operations=cascade(
                ("photos_photo_vehicles", "photo_id", "photos_photo", "id"),
                ("photos_photo_vehicles", "vehicle_id", "vehicles_vehicle", "id"),
            ),
        ),
    ]
