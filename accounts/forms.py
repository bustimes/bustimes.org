from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.models import Permission
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db.models.functions import Now
from django.forms import (
    BooleanField,
    CharField,
    CheckboxSelectMultiple,
    EmailField,
    EmailInput,
    Form,
    ModelMultipleChoiceField,
    UUIDField,
)

from .models import Invitation

User = get_user_model()


def invite_code_is_valid(code):
    try:
        return Invitation.objects.filter(uuid=code, expires_at__gt=Now()).exists()
    except ValidationError:
        return False


class InviteCodeField(UUIDField):
    def validate(self, value):
        super().validate(value)
        if not invite_code_is_valid(value):
            raise ValidationError("That invite code is not valid or has expired")


class RegistrationForm(PasswordResetForm):
    invite_code = InviteCodeField()

    field_order = ("invite_code",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["email"].label = "Email address"
        self.fields["email"].help_text = "Will be kept private"

    def save(self, request=None):
        email_address = self.cleaned_data["email"]

        try:
            self.user = User.objects.get(email__iexact=email_address)
        except User.DoesNotExist:
            self.user = User.objects.create_user(email_address, email_address)

        if not self.user.is_active:
            self.user.is_active = True
            self.user.save(update_fields=["is_active"])

        super().save(
            request=request,
            subject_template_name="registration/register_confirm_subject.txt",
            email_template_name="registration/register_confirm_email.txt",
            use_https=True,
        )

    def get_users(self, _):
        return [self.user]


class LoginForm(AuthenticationForm):
    username = EmailField(
        widget=EmailInput(attrs={"autofocus": True, "autocomplete": "email"})
    )


class UserForm(Form):
    name = CharField(
        required=False,
        label="Username",
        validators=[UnicodeUsernameValidator()],
        max_length=50,
    )

    def __init__(self, data, *args, user, **kwargs):
        super().__init__(data, *args, **kwargs)

        self.fields["name"].initial = (
            user.username if user.username != user.email else ""
        )
        self.fields["name"].help_text = f"""Will be displayed publicly.
Leave blank to be 'user {user.id}'"""
        self.fields["name"].widget.attrs["placeholder"] = f"user {user.id}"


class UserPermissionsForm(Form):
    permissions = ModelMultipleChoiceField(
        queryset=None, widget=CheckboxSelectMultiple, required=False
    )

    def __init__(self, data, *args, user, **kwargs):
        super().__init__(data, *args, **kwargs)

        self.fields["permissions"].initial = user.user_permissions.all()
        self.fields["permissions"].queryset = Permission.objects.filter(
            codename__in=(
                "add_vehiclerevision",
                "change_vehiclerevision",
                "change_vehicle",
            )
        ).select_related("content_type")


class DeleteForm(Form):
    confirm_delete = BooleanField(label="Please delete my account")
