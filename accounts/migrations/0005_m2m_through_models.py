# The tables already exist, as implicit many-to-many tables; all that's new is
# the explicit through models describing them.  See buses/migration_utils.py.

import django.db.models.deletion
from django.db import migrations, models

from buses.migration_utils import cascade


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_alter_operatoruser_operator_alter_operatoruser_user"),
        ("busstops", "0018_alter_datasource_options_alter_region_options_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="InvitationOperator",
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
                            "invitation",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="invitationoperator+",
                                to="accounts.invitation",
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DB_CASCADE,
                                related_name="invitationoperator+",
                                to="busstops.operator",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "accounts_invitation_operators",
                        "unique_together": {("invitation", "operator")},
                    },
                ),
                migrations.AlterField(
                    model_name="invitation",
                    name="operators",
                    field=models.ManyToManyField(
                        blank=True,
                        through="accounts.InvitationOperator",
                        to="busstops.operator",
                    ),
                ),
            ],
            database_operations=cascade(
                (
                    "accounts_invitation_operators",
                    "invitation_id",
                    "accounts_invitation",
                    "id",
                ),
                (
                    "accounts_invitation_operators",
                    "operator_id",
                    "busstops_operator",
                    "noc",
                ),
            ),
        ),
    ]
