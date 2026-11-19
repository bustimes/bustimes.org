import re

from django.db import migrations


def normalise(colour):
    colour = colour.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", colour):
        colour = "#" + "".join(c * 2 for c in colour[1:])
    if re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
        return colour.lower()
    return colour


def normalise_colours(apps, schema_editor):
    ServiceColour = apps.get_model("busstops", "ServiceColour")
    Service = apps.get_model("busstops", "Service")
    Operator = apps.get_model("busstops", "Operator")

    fields = ("foreground", "background", "border")
    keep = {}
    for colour in ServiceColour.objects.order_by("id"):
        changed = False
        for field in fields:
            value = normalise(getattr(colour, field))
            if value != getattr(colour, field):
                setattr(colour, field, value)
                changed = True

        key = (
            colour.name,
            colour.foreground,
            colour.background,
            colour.border,
            colour.use_name_as_brand,
        )
        if key in keep:
            Service.objects.filter(colour=colour.id).update(colour=keep[key])
            Operator.objects.filter(colour=colour.id).update(colour=keep[key])
            colour.delete()
        else:
            keep[key] = colour.id
            if changed:
                colour.save(update_fields=fields)


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0021_service_trip_chaining"),
    ]

    operations = [
        migrations.RunPython(normalise_colours, migrations.RunPython.noop),
    ]
