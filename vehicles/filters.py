from django.forms.widgets import NumberInput, TextInput
from django_filters import ChoiceFilter, FilterSet, ModelChoiceFilter

from accounts.models import User
from vehicles.models import Operator, Vehicle


class VehicleRevisionFilter(FilterSet):
    operator = ModelChoiceFilter(
        label="Operator code",
        method="operator_filter",
        queryset=Operator.objects,
        widget=TextInput,
    )
    vehicle = ModelChoiceFilter(
        label="Vehicle ID", queryset=Vehicle.objects, widget=NumberInput
    )
    user = ModelChoiceFilter(label="Edited by user", queryset=User.objects, widget=NumberInput)
    # approved_by = ModelChoiceFilter(
    #     label="(Dis)approver", queryset=User.objects, widget=NumberInput
    # )
    status = ChoiceFilter(
        label="Status",
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("disapproved", "disapproved"),
        ],
        method="status_filter",
        required=True,
    )

    def operator_filter(self, queryset, _, value):
        revisions = queryset.model.objects
        return queryset.filter(
            id__in=revisions.filter(vehicle__operator=value)
            .values("id")
            .union(
                revisions.filter(from_operator=value).values("id"),
                revisions.filter(to_operator=value).values("id"),
            )
        )

    def status_filter(self, queryset, _, value):
        match value:
            case "pending":
                return queryset.filter(pending=True, disapproved=False)
            case "disapproved":
                return queryset.filter(pending=False, disapproved=True)
            case "approved":
                return queryset.filter(pending=False, disapproved=False)
