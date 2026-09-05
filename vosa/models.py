from django.db import models
from django.db.models import Count
from django.urls import reverse


class TrafficArea(models.TextChoices):
    WEST = "H", "West of England"
    WM = "D", "West Midlands"
    WALES = "G", "Wales"
    SE = "K", "London and the South East of England"
    SCOTLAND = "M", "Scotland"
    NW = "C", "North West of England"
    NE = "B", "North East of England"
    EAST = "F", "East of England"


class Description(models.TextChoices):
    RESTRICTED = "Restricted"
    STANDARD_INTERNATIONAL = "Standard International"
    STANDARD_NATIONAL = "Standard National"


class Licence(models.Model):
    name = models.CharField(max_length=255)
    trading_name = models.CharField(max_length=255, blank=True)
    traffic_area = models.CharField(max_length=1, choices=TrafficArea)
    licence_number = models.CharField(max_length=20, unique=True)
    discs = models.PositiveSmallIntegerField(null=True)
    authorised_discs = models.PositiveSmallIntegerField(null=True)
    description = models.CharField(max_length=22, choices=Description)
    granted_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    address = models.TextField()
    licence_status = models.CharField(max_length=255, blank=True)

    def get_operators(self):
        return (
            self.operator_set.annotate(services=Count("service", current=True))
            .filter(services__gt=0)
            .order_by("-services")
        )

    def __str__(self):
        return self.licence_number

    def get_absolute_url(self):
        return reverse("licence_detail", args=(self.licence_number,))


class Registration(models.Model):
    licence = models.ForeignKey(Licence, models.DB_CASCADE)
    registration_number = models.CharField(max_length=20, unique=True)
    service_number = models.CharField(max_length=100, blank=True)
    start_point = models.CharField(max_length=255, blank=True)
    finish_point = models.CharField(max_length=255, blank=True)
    via = models.CharField(blank=True, max_length=255)
    subsidies_description = models.CharField(max_length=255)
    subsidies_details = models.CharField(max_length=255, blank=True)
    service_type_description = models.CharField(max_length=255, blank=True)
    registration_status = models.CharField(max_length=255, db_index=True)
    traffic_area_office_covered_by_area = models.CharField(max_length=100)
    authority_description = models.CharField(max_length=255, blank=True)
    registered = models.BooleanField()
    latest_variation = models.ForeignKey(
        "Variation", models.DB_SET_NULL, null=True, blank=True, related_name="latest"
    )

    def __str__(self):
        string = f"{self.service_number} - {self.start_point} to {self.finish_point}"
        if self.via:
            string = f"{string} via {self.via}"
        return string

    def get_absolute_url(self):
        return reverse("registration_detail", args=(self.registration_number,))


class Variation(models.Model):
    registration = models.ForeignKey(Registration, models.DB_CASCADE)
    variation_number = models.PositiveSmallIntegerField()
    effective_date = models.DateField(null=True, blank=True)
    date_received = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    service_type_other_details = models.TextField()
    registration_status = models.CharField(max_length=255)
    publication_text = models.TextField()
    short_notice = models.CharField(max_length=255)

    class Meta:
        ordering = ("-variation_number",)
        unique_together = ("registration", "variation_number")

    def __str__(self):
        return str(self.registration)

    def get_absolute_url(self):
        url = reverse(
            "registration_detail", args=(self.registration.registration_number,)
        )
        url = f"{url}#{self.variation_number}"
        return url
