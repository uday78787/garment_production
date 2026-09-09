"""
planning/models.py

Order and planning data. Note: all calculation *logic* lives in
planning/services/calculations.py — these models only store the inputs
(order qty, SAM via Article, ramp-up parameters, overrides) and expose thin
convenience properties that delegate to that service module. Keeping the
formulas out of the models means they can be unit tested (and reused from
reports/exports) without touching the database.
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg

from common.models import TimeStampedModel
from common.utils import add_working_days
from master_data.models import Article, Holiday, ProductionLine
from planning.services import calculations as calc


class ProductionOrder(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        ON_HOLD = "ON_HOLD", "On Hold"
        CANCELLED = "CANCELLED", "Cancelled"

    order_number = models.CharField(max_length=50, unique=True)
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="orders")
    order_quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    customer_name = models.CharField(max_length=150)
    delivery_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["-delivery_date"]

    def __str__(self):
        return f"{self.order_number} ({self.article.article_code})"

    @property
    def sam(self):
        """SAM fetched directly from the selected Article."""
        return self.article.sam

    @property
    def total_produced_quantity(self) -> int:
        """Sum of actual_output across every DailyProductionEntry for every
        assignment tied to this order."""
        from production.models import DailyProductionEntry

        total = DailyProductionEntry.objects.filter(
            assignment__production_order=self
        ).aggregate(total=models.Sum("actual_output"))["total"]
        return total or 0

    @property
    def remaining_quantity(self):
        return calc.remaining_quantity(self.order_quantity, self.total_produced_quantity)

    @property
    def pending_production(self):
        """Pieces still pending to complete the order."""
        return max(self.order_quantity - self.total_produced_quantity, 0)


class LineAssignment(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        ACTIVE = "ACTIVE", "Active"
        COMPLETED = "COMPLETED", "Completed"
        CLOSED = "CLOSED", "Closed"

    production_line = models.ForeignKey(
        ProductionLine, on_delete=models.PROTECT, related_name="assignments"
    )
    production_order = models.ForeignKey(
        ProductionOrder, on_delete=models.CASCADE, related_name="assignments"
    )
    start_date = models.DateField()
    default_manpower = models.PositiveIntegerField()
    working_minutes_per_operator = models.PositiveIntegerField(default=480)
    start_efficiency = models.DecimalField(
        max_digits=5, decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Efficiency on day 1, as a decimal (0.25 = 25%).",
    )
    daily_efficiency_growth = models.DecimalField(
        max_digits=5, decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Efficiency added per day, as a decimal (0.05 = +5 pts/day).",
    )
    max_efficiency = models.DecimalField(
        max_digits=5, decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Efficiency ceiling, as a decimal (0.60 = 60%).",
    )
    forecast_efficiency_override = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Optional override efficiency used for forward forecasting instead "
                  "of the ramp-up curve (e.g. once a line has stabilised).",
    )
    forecast_manpower_override = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Optional override manpower used for forward forecasting.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["production_line", "production_order", "start_date"],
                name="unique_line_order_start_date",
            )
        ]

    def __str__(self):
        return f"{self.production_line} <- {self.production_order}"

    @property
    def sam(self):
        """SAM fetched through the assigned Production Order's Article."""
        return self.production_order.article.sam

    def available_minutes_on(self, on_date: date, manpower: int | None = None):
        mp = manpower if manpower is not None else self.default_manpower
        return calc.available_minutes(mp, self.working_minutes_per_operator)

    def forecast_efficiency(self):
        """Forecast efficiency: override if set, otherwise use latest plan from daily entries."""
        if self.forecast_efficiency_override is not None:
            return self.forecast_efficiency_override

        latest_entry = self.daily_entries.order_by("-production_date").first()
        if latest_entry is not None and latest_entry.planned_efficiency is not None:
            return latest_entry.planned_efficiency

        return Decimal("0")

    def forecast_daily_output(self):
        """Forecast pieces/day using override values if set, else latest known daily plan efficiency."""
        manpower = self.forecast_manpower_override or self.default_manpower
        efficiency = self.forecast_efficiency()
        return calc.forecast_daily_output(
            manpower, self.working_minutes_per_operator, self.production_order.article.sam, efficiency
        )

    def expected_days_remaining(self):
        remaining = self.production_order.remaining_quantity
        daily_output = self.forecast_daily_output()
        if daily_output <= 0:
            return None
        return calc.expected_days_remaining(remaining, daily_output)

    def expected_completion_date(self):
        days = self.expected_days_remaining()
        if days is None:
            return None

        holiday_dates = set(Holiday.objects.values_list("holiday_date", flat=True))
        return add_working_days(date.today(), days, holiday_dates=holiday_dates, weekly_off=6)
