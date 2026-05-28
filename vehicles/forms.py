from django import forms
from django.conf import settings
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import ValidationError

from busstops.models import Operator

from .fields import validate_colours
from .form_fields import RegField, SummaryField
from .models import Livery, Vehicle, VehicleFeature, VehicleType


class AutocompleteWidget(forms.Select):
    # optgroups method from the Django admin AutocompleteSelect widget
    optgroups = AutocompleteSelect.optgroups

    def __init__(self, field=None, attrs=None, choices=(), using=None):
        self.field = field
        self.attrs = {} if attrs is None else attrs.copy()
        self.choices = choices
        self.db = None


class EditVehicleForm(forms.Form):
    @property
    def media(self):
        return forms.Media(
            js=(
                "admin/js/vendor/jquery/jquery.min.js",
                "admin/js/vendor/select2/select2.full.min.js",
                "js/edit-vehicle.js",
            ),
            css={
                "screen": ("admin/css/vendor/select2/select2.min.css",),
            },
        )

    field_order = [
        "withdrawn",
        "spare_ticket_machine",
        "fleet_number",
        "reg",
        "operator",
        "vehicle_type",
        "colours",
        "other_colour",
        "branding",
        "name",
        "previous_reg",
        "features",
        "notes",
        "rules",
    ]
    spare_ticket_machine = forms.BooleanField(
        required=False,
        help_text="i.e. the ticket machine code is something like SPARE",
    )
    withdrawn = forms.BooleanField(
        label="Remove from list",
        required=False,
        help_text="Rarely necessary, unless you're sure this vehicle has definitely been permenantly withdrawn",
    )

    fleet_number = forms.CharField(required=False, max_length=24)
    reg = RegField(label="Number plate", required=False, max_length=24)

    operator = forms.ModelChoiceField(
        queryset=Operator.objects,
        widget=AutocompleteWidget(field=Vehicle.operator.field),
        required=False,
        empty_label="",
    )

    vehicle_type = forms.ModelChoiceField(
        widget=AutocompleteWidget(field=Vehicle.vehicle_type.field),
        queryset=VehicleType.objects,
        required=False,
        empty_label="",
    )

    colours = forms.ModelChoiceField(
        widget=AutocompleteWidget(field=Vehicle.livery.field),
        label="Current livery",
        queryset=Livery.objects,
        required=False,
        help_text="""Don't change this until the bus has *been painted*
(<em>not</em> just "in the paint shop" or "awaiting repaint")""",
    )
    other_colour = forms.CharField(
        label="Other colours",
        help_text="E.g. '#c0c0c0 #ff0000 #ff0000' (red with a silver front)",
        validators=[validate_colours],
        required=False,
        max_length=255,
    )

    branding = forms.CharField(
        label="Other branding",
        required=False,
        max_length=40,
        help_text="If it's interesting or unusual",
    )
    name = forms.CharField(
        label="Vehicle name",
        required=False,
        max_length=40,
    )
    previous_reg = RegField(
        required=False,
        help_text="Separate multiple regs with a comma (,)",
    )

    features = forms.ModelMultipleChoiceField(
        queryset=VehicleFeature.objects,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    notes = forms.CharField(required=False, max_length=255)
    summary = SummaryField(
        max_length=255,
        help_text="""Explain your changes,
if they need explaining.
E.g. how you *know* a vehicle has *definitely been* withdrawn or repainted,
link to a picture to prove it. Be polite.""",
    )
    rules = forms.BooleanField(
        label="I agree that my edit is made in good faith and complies with the <a href=\"/rules\">editing rules</a>",
        required=True,
    )

    def clean_reg(self):
        reg = self.cleaned_data["reg"].replace(".", "")
        if self.cleaned_data.get("spare_ticket_machine") and reg:
            raise ValidationError(
                "A spare ticket machine can\u2019t have a number plate"
            )
        return reg

    def __init__(self, data, *args, user, vehicle, sibling_vehicles, **kwargs):
        super().__init__(data, *args, **kwargs)

        self.fields["operator"].initial = vehicle.operator
        self.fields["reg"].initial = vehicle.reg
        self.fields["vehicle_type"].initial = vehicle.vehicle_type
        self.fields["colours"].initial = vehicle.livery_id

        if not vehicle.vehicle_type_id:
            self.fields["vehicle_type"].widget.attrs["data-suggested"] = ",".join(
                str(v.vehicle_type_id)
                for v in sibling_vehicles
                if v and v.vehicle_type_id
            )
        if not vehicle.livery_id:
            self.fields["colours"].widget.attrs["data-suggested"] = ",".join(
                str(v.livery_id) for v in sibling_vehicles if v and v.livery_id
            )

        self.fields["other_colour"].initial = vehicle.colours or ""
        self.fields["features"].initial = vehicle.features.all()
        self.fields["branding"].initial = vehicle.branding
        self.fields["name"].initial = vehicle.name
        self.fields["previous_reg"].initial = (
            vehicle.data and vehicle.data.get("Previous reg") or None
        )
        self.fields["notes"].initial = vehicle.notes
        self.fields["withdrawn"].initial = vehicle.withdrawn
        self.fields["spare_ticket_machine"].initial = vehicle.is_spare_ticket_machine()

        if vehicle.fleet_code:
            self.fields["fleet_number"].initial = vehicle.fleet_code
        elif vehicle.fleet_number is not None:
            self.fields["fleet_number"].intial = str(vehicle.fleet_number)

        if vehicle.vehicle_type_id and not vehicle.is_spare_ticket_machine():
            del self.fields["spare_ticket_machine"]

        if not (vehicle.livery_id and vehicle.vehicle_type_id and vehicle.reg):
            self.fields["summary"].required = False
            self.fields["summary"].label = "Summary (optional)"

        if not user.is_superuser:
            if not (
                vehicle.notes
                or vehicle.operator_id in settings.ALLOW_VEHICLE_NOTES_OPERATORS
            ):
                del self.fields["notes"]

        if vehicle.is_spare_ticket_machine():
            del self.fields["notes"]
            if not vehicle.fleet_code:
                del self.fields["fleet_number"]
            if not vehicle.reg:
                del self.fields["reg"]
            if not vehicle.vehicle_type_id:
                del self.fields["vehicle_type"]
            if not vehicle.name:
                del self.fields["name"]
            if not vehicle.data:
                del self.fields["previous_reg"]
            if (
                not vehicle.colours
                and not vehicle.livery_id
                and "colours" in self.fields
            ):
                del self.fields["colours"]
                del self.fields["other_colour"]
            if not vehicle.branding:
                del self.fields["branding"]
            if not vehicle.features.all():
                del self.fields["features"]


class DebuggerForm(forms.Form):
    data = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))


class DateForm(forms.Form):
    date = forms.DateField()


class RulesForm(forms.Form):
    rules = forms.BooleanField(label="I've read the rules", required=True)
