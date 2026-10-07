from django.core.exceptions import ValidationError
from django.db.models import CharField
from webcolors import html5_parse_simple_color


def validate_colour(value):
    if value:
        try:
            html5_parse_simple_color(value)
        except ValueError as e:
            raise ValidationError(str(e)) from e


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
