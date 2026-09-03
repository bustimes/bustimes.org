from django import forms
from turnstile.fields import TurnstileField

from .models import ServiceOverride


class ContactForm(forms.Form):
    name = forms.CharField(label="Name")
    email = forms.EmailField(label="Email address")
    message = forms.CharField(label="Message", widget=forms.Textarea)
    referrer = forms.CharField(
        label="Referrer", required=False, widget=forms.HiddenInput
    )
    turnstile = TurnstileField(label="Confirm that you’re a human (not a robot)")

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)


class SearchForm(forms.Form):
    q = forms.CharField(widget=forms.TextInput(attrs={"type": "search"}))


class CheckboxSelectMultipleWithDefault(forms.CheckboxSelectMultiple):
    default = ()

    def value_from_datadict(self, data, files, name):
        if name not in data:
            return self.default
        return super().value_from_datadict(data, files, name)


class TimetableForm(forms.Form):
    date = forms.DateField(required=False)
    calendar = forms.IntegerField(required=False)
    detailed = forms.BooleanField(required=False)
    vehicles = forms.BooleanField(required=False)
    service = forms.MultipleChoiceField(
        required=False, widget=CheckboxSelectMultipleWithDefault
    )

    def __init__(self, *args, **kwargs):
        service = kwargs.pop("service")
        self.related = kwargs.pop("related")
        super().__init__(*args, **kwargs)

        line_names = service.get_line_names()
        self.fields["service"].choices = [
            (f"{service.id}:{line_name}", line_name) for line_name in line_names
        ]
        self.fields["service"].initial = self.fields["service"].widget.default = [
            choice[0] for choice in self.fields["service"].choices
        ]

        if self.related:
            for s in self.related:
                self.fields["service"].choices += [
                    (f"{s.id}:{line_name}", line_name)
                    for line_name in s.get_line_names()
                ]
        if len(self.fields["service"].choices) > 1:
            self.fields["service"].choices = sorted(
                self.fields["service"].choices,
                key=lambda choice: service.get_line_name_order(choice[1]),
            )
        else:
            del self.fields["service"]

    def get_timetable(self, service):
        if self.is_valid():
            date = self.cleaned_data["date"]
            calendar_id = self.cleaned_data["calendar"]
            line_names = self.cleaned_data.get("service")
            detailed = self.cleaned_data["detailed"]
        else:
            date = None
            calendar_id = None
            line_names = None
            detailed = False

        return service.get_timetable(
            day=date,
            calendar_id=calendar_id,
            also_services=self.related,
            line_names=line_names,
            detailed=detailed,
        )


class DeparturesForm(forms.Form):
    date = forms.DateField()
    time = forms.TimeField(required=False)


class ServiceOverrideForm(forms.ModelForm):
    class Meta:
        model = ServiceOverride
        fields = ("field", "value")
