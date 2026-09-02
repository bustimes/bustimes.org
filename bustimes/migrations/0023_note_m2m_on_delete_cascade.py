from django.db import migrations

from buses.migration_utils import cascade

# Trips and stop times are deleted by the database, so Django's collector never
# visits the rows of these implicit many-to-many tables.  See
# buses/migration_utils.py.


class Migration(migrations.Migration):
    dependencies = [
        ("bustimes", "0022_db_on_delete_trip_stoptime"),
    ]

    operations = cascade(
        ("bustimes_trip_notes", "trip_id", "bustimes_trip", "id"),
        ("bustimes_stoptime_notes", "stoptime_id", "bustimes_stoptime", "id"),
    )
