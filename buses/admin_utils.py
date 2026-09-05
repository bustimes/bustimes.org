from copy import copy, deepcopy


class M2MThroughMixin:
    """ManyToManyField with an explicit through model (to support DB_CASCADE):
    where there are no extra fields, pretend the through model was auto-created,
    so the field is editable in the Django admin panel."""

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        through = db_field.remote_field.through
        if not through._meta.auto_created and all(
            field.is_relation or field.primary_key for field in through._meta.fields
        ):
            meta = copy(through._meta)
            meta.auto_created = through
            db_field = deepcopy(db_field)
            db_field.remote_field.through = type(through.__name__, (), {"_meta": meta})
        return super().formfield_for_manytomany(db_field, request, **kwargs)
