import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("bustimes", "0026_importtask_dataqualityobservation_dodgyroutelink_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="stoptime",
                    name="stop",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        to="busstops.stoppoint",
                    ),
                ),
                migrations.RemoveIndex(
                    model_name="stoptime",
                    name="stoptime_stop_dep_covering",
                ),
                migrations.AddIndex(
                    model_name="stoptime",
                    index=models.Index(
                        fields=["stop", "departure"],
                        include=("trip",),
                        name="stoptime_stop_dep_covering",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="SET lock_timeout = '2s'",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='CREATE INDEX CONCURRENTLY "stoptime_stop_dep_covering_new" '
                    "ON bustimes_stoptime (stop_id, departure) INCLUDE (trip_id)",
                    reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS "stoptime_stop_dep_covering_new"',
                ),
                migrations.RunSQL(
                    sql="SET lock_timeout = '2s'",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='DROP INDEX CONCURRENTLY IF EXISTS "stoptime_stop_dep_covering"',
                    reverse_sql='CREATE INDEX CONCURRENTLY "stoptime_stop_dep_covering" '
                    "ON bustimes_stoptime (stop_id, departure) INCLUDE (trip_id) WHERE pick_up",
                ),
                migrations.RunSQL(
                    sql="SET lock_timeout = '2s'",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='ALTER INDEX "stoptime_stop_dep_covering_new" RENAME TO "stoptime_stop_dep_covering"',
                    reverse_sql='ALTER INDEX "stoptime_stop_dep_covering" RENAME TO "stoptime_stop_dep_covering_new"',
                ),
                migrations.RunSQL(
                    sql="SET lock_timeout = '2s'",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='DROP INDEX CONCURRENTLY IF EXISTS "bustimes_stoptime_stop_id_29cfeabe"',
                    reverse_sql='CREATE INDEX CONCURRENTLY "bustimes_stoptime_stop_id_29cfeabe" '
                    "ON bustimes_stoptime (stop_id)",
                ),
            ],
        ),
    ]
