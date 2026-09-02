from django.db import migrations

from buses.migration_utils import cascade

# As in bustimes/migrations/0023_note_m2m_on_delete_cascade.py: consequences are
# now deleted by the database (via situation, via data source), so the implicit
# many-to-many tables need a referential action of their own.


class Migration(migrations.Migration):
    dependencies = [
        ("disruptions", "0009_alter_affectedjourney_situation_and_more"),
    ]

    operations = cascade(
        *(
            (
                f"disruptions_consequence_{name}",
                "consequence_id",
                "disruptions_consequence",
                "id",
            )
            for name in ("stops", "services", "operators")
        )
    )
