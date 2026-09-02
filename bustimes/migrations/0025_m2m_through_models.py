# The tables already exist, as implicit many-to-many tables; all that's new is
# the explicit through models describing them.  See buses/migration_utils.py.

import django.db.models.deletion
from django.db import migrations, models

from buses.migration_utils import cascade


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0019_m2m_through_models"),
        ("bustimes", "0024_remove_stoptime_stop_code_alter_stoptime_stop"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="StopTimeNote",
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
                            "note",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="stoptimenote+",
                                to="bustimes.note",
                            ),
                        ),
                        (
                            "stoptime",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="stoptimenote+",
                                to="bustimes.stoptime",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "bustimes_stoptime_notes",
                        "unique_together": {("stoptime", "note")},
                    },
                ),
                migrations.AlterField(
                    model_name="stoptime",
                    name="notes",
                    field=models.ManyToManyField(
                        blank=True, through="bustimes.StopTimeNote", to="bustimes.note"
                    ),
                ),
                migrations.CreateModel(
                    name="TimetableDataSourceOperator",
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
                                related_name="timetabledatasourceoperator+",
                                to="busstops.operator",
                            ),
                        ),
                        (
                            "timetabledatasource",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="timetabledatasourceoperator+",
                                to="bustimes.timetabledatasource",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "bustimes_timetabledatasource_operators",
                        "unique_together": {("timetabledatasource", "operator")},
                    },
                ),
                migrations.AlterField(
                    model_name="timetabledatasource",
                    name="operators",
                    field=models.ManyToManyField(
                        blank=True,
                        through="bustimes.TimetableDataSourceOperator",
                        to="busstops.operator",
                    ),
                ),
                migrations.CreateModel(
                    name="TripNote",
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
                            "note",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="tripnote+",
                                to="bustimes.note",
                            ),
                        ),
                        (
                            "trip",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="tripnote+",
                                to="bustimes.trip",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "bustimes_trip_notes",
                        "unique_together": {("trip", "note")},
                    },
                ),
                migrations.AlterField(
                    model_name="trip",
                    name="notes",
                    field=models.ManyToManyField(
                        blank=True, through="bustimes.TripNote", to="bustimes.note"
                    ),
                ),
            ],
            database_operations=cascade(
                ("bustimes_stoptime_notes", "stoptime_id", "bustimes_stoptime", "id"),
                ("bustimes_stoptime_notes", "note_id", "bustimes_note", "id"),
                (
                    "bustimes_timetabledatasource_operators",
                    "timetabledatasource_id",
                    "bustimes_timetabledatasource",
                    "id",
                ),
                (
                    "bustimes_timetabledatasource_operators",
                    "operator_id",
                    "busstops_operator",
                    "noc",
                ),
                ("bustimes_trip_notes", "trip_id", "bustimes_trip", "id"),
                ("bustimes_trip_notes", "note_id", "bustimes_note", "id"),
            ),
        ),
    ]
