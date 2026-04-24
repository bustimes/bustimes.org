from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from sql_util.utils import SubqueryCount

from . import forms

UserModel = get_user_model()


def register(request):
    if request.method == "POST":
        form = forms.RegistrationForm(request.POST)
        if form.is_valid():
            form.save(request=request)
    else:
        form = None

        if (
            invite_code := request.GET.get("invite_code")
        ) and forms.invite_code_is_valid(invite_code):
            form = forms.RegistrationForm()
            form.fields["invite_code"].initial = invite_code

    return render(
        request,
        "register.html",
        {
            "form": form,
        },
    )


class RegisterConfirmView(auth_views.PasswordResetConfirmView):
    pass


class LoginView(auth_views.LoginView):
    form_class = forms.LoginForm


class PasswordResetView(auth_views.PasswordResetView):
    def get_context_data(self, *args, **kwargs):
        context_data = super().get_context_data(*args, **kwargs)
        context_data["form"].fields["email"].label = "Email address"
        return context_data


@login_required
def user_detail(request, pk):
    users = UserModel.objects.annotate(
        total_count=SubqueryCount("vehiclerevision"),
        approved_count=SubqueryCount(
            "vehiclerevision", filter=Q(disapproved=False, pending=False)
        ),
        disapproved_count=SubqueryCount(
            "vehiclerevision", filter=Q(pending=False, disapproved=True)
        ),
        pending_count=SubqueryCount(
            "vehiclerevision", filter=Q(pending=True, disapproved=False)
        ),
    )

    user = get_object_or_404(users, pk=pk)

    context = {"object": user}

    if request.user == user:
        form = forms.UserForm(request.POST or None, user=user)

        delete_form = forms.DeleteForm()

        if request.POST and form.is_valid():
            if "confirm_delete" in request.POST:
                delete_form = forms.DeleteForm(request.POST)
                if (
                    delete_form.is_valid()
                    and request.user == user
                    and delete_form.cleaned_data["confirm_delete"]
                ):
                    user.is_active = False
                    user.save(update_fields=["is_active"])

            elif "name" in form.changed_data:
                user.username = form.cleaned_data["name"]
                if not user.username:
                    user.username = user.email
                try:
                    user.save(update_fields=["username"])
                except IntegrityError:
                    form.add_error("name", "Username taken")
                    user.refresh_from_db()

        context["form"] = form
        context["delete_form"] = delete_form

    elif request.user.is_superuser:
        form = forms.UserPermissionsForm(request.POST or None, user=user)

        if request.POST and form.is_valid():
            user.user_permissions.set(form.cleaned_data["permissions"])

        context["form"] = form

    return render(request, "user_detail.html", context)
