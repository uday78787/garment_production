"""
master_data/models.py

Reference / setup data: production lines, articles (styles) with SAM, and
the factory holiday calendar.
"""

from django.core.validators import MinValueValidator
from django.db import models

from common.models import TimeStampedModel


class Factory(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=30, unique=True)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="India")
    active_status = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class ProductionLine(TimeStampedModel):
    factory = models.ForeignKey(
        Factory, on_delete=models.PROTECT, related_name="production_lines", null=True, blank=True
    )
    line_name = models.CharField(max_length=100, unique=True)
    capacity_manpower = models.PositiveIntegerField(
        help_text="Standard/maximum manpower this line is built for."
    )
    default_working_minutes = models.PositiveIntegerField(
        default=480, help_text="Default working minutes per operator per day."
    )
    active_status = models.BooleanField(default=True)

    class Meta:
        ordering = ["line_name"]

    def __str__(self):
        return self.line_name


class Article(TimeStampedModel):
    """A style/article the factory produces. Also referred to as 'Style'."""

    article_code = models.CharField(max_length=50, unique=True)
    article_name = models.CharField(max_length=200)
    sam = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
        verbose_name="SAM",
        help_text="Standard Allotted Minutes to produce one piece.",
    )

    class Meta:
        ordering = ["article_code"]

    def __str__(self):
        return f"{self.article_code} - {self.article_name}"


class Holiday(models.Model):
    holiday_date = models.DateField(unique=True)
    holiday_name = models.CharField(max_length=150)

    class Meta:
        ordering = ["holiday_date"]

    def __str__(self):
        return f"{self.holiday_date} - {self.holiday_name}"
