"""Helper for the implicit many-to-many tables of database-level cascades.

Django gives the foreign keys of an implicit many-to-many table a hardcoded
Python-level CASCADE, which emits no referential action at all.  Where either
end of the relationship uses a database-level on_delete, Django's collector no
longer visits these rows: a NO ACTION constraint fails at COMMIT, long after
the offending DELETE, and no constraint at all leaves them orphaned forever.
Django 6.1.1 finally reports this as fields.E323, and the fix is an explicit
through model - but Django can only express the referential action where it
would also accept a database-level on_delete, so the rest is done here.
"""

from django.db import migrations


def alter(table, column, ref_table, ref_column, action="ON DELETE CASCADE"):
    """Give a foreign key a referential action, creating it if need be.

    Finds the existing constraint by column rather than by name, keeps that
    name when recreating it, and leaves it alone if it's already right.  A
    database restored without its referenced table has no constraint here at
    all - and so may have collected rows that would fail to validate - so tidy
    those away first.
    """
    deltype = "c" if action else "a"
    return f"""
DO $$
DECLARE
    name text;
    deltype "char";
BEGIN
    SELECT c.conname, c.confdeltype INTO name, deltype
    FROM pg_constraint c
    JOIN pg_attribute col ON col.attrelid = c.conrelid AND col.attnum = c.conkey[1]
    WHERE c.contype = 'f'
        AND c.conrelid = to_regclass('{table}')
        AND col.attname = '{column}';

    IF name IS NOT NULL AND deltype = '{deltype}' THEN
        RETURN;
    ELSIF name IS NULL THEN
        name := left('{table}_{column}_fk', 63);
        DELETE FROM {table} t
        WHERE NOT EXISTS (
            SELECT FROM {ref_table} r WHERE r.{ref_column} = t.{column}
        );
    ELSE
        EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', name);
    END IF;

    EXECUTE format(
        'ALTER TABLE {table} ADD CONSTRAINT %I FOREIGN KEY ({column})'
        ' REFERENCES {ref_table} ({ref_column}) {action}'
        ' DEFERRABLE INITIALLY DEFERRED',
        name
    );
END $$;
"""


def cascade(*tables):
    """RunSQL operations giving each (table, column, ref_table, ref_column) an
    ON DELETE CASCADE, reversible to no referential action."""
    return [
        migrations.RunSQL(alter(*table), alter(*table, action="")) for table in tables
    ]
