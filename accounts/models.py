from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.urls import reverse
from uuid import uuid4


class CustomUserManager(UserManager):
    def get_by_natural_key(self, username):
        return self.get(email__iexact=username)


class Invitation(models.Model):
    uuid = models.UUIDField(default=uuid4)
    expires_at = models.DateTimeField()
    operators = models.ManyToManyField("busstops.Operator", blank=True)

    def get_absolute_url(self):
        return reverse("register") + f"?invite_code={self.uuid}"

    def __str__(self):
        return str(self.uuid)


class OperatorUser(models.Model):
    operator = models.ForeignKey("busstops.Operator", models.CASCADE)
    user = models.ForeignKey("User", models.CASCADE)
    staff = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.operator_id}: {self.user_id}"


class User(AbstractUser):
    email = models.EmailField(unique=True, verbose_name="email address")
    trusted = models.BooleanField(null=True)
    operators = models.ManyToManyField(
        "busstops.Operator", blank=True, through=OperatorUser
    )
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    score = models.IntegerField(blank=True, null=True)
    objects = CustomUserManager()

    USERNAME_FIELD = "email"  # this was a bad idea
    REQUIRED_FIELDS = ["username"]  # so that ./manage.py createsuperuser works

    def get_absolute_url(self):
        return reverse("user_detail", args=(self.id,))

    def __str__(self):
        if self.trusted is not False and "@" not in self.username:
            return f"{self.id}: {self.username}"
        return f"{self.id}"
