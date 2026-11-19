from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db.models import CharField

validate_colour = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Must be a colour like #ff0000")


def validate_colours(value):
    for colour in value.split():
        validate_colour(colour)


def validate_css(value):
    if value.count("(") != value.count(")"):
        raise ValidationError("Must contain equal numbers of ( and )")
    if "{" in value or "}" in value:
        raise ValidationError("Must not contain { or }")


class ColourField(CharField):
    default_validators = (validate_colour,)


class ColoursField(CharField):
    default_validators = (validate_colours,)


class CSSField(CharField):
    default_validators = (validate_css,)
