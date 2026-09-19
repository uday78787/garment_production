"""
production/models.py

Daily production entry: the one row entered per line per day by a
Production User. All derived fields (produced_minutes, actual_efficiency,
target_output, planned_efficiency, gain_loss_minutes) are computed via
planning/services/calculations.py and stored so that reports can query them
directly without recomputing on every read.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.models import TimeStampedModel
from planning.models import LineAssignment
from planning.services import calculations as calc
from django.core.exceptions import ValidationError


class DailyProductionEntry(TimeStampedModel):
    assignment = models.ForeignKey(
        LineAssignment, on_delete=models.CASCADE, related_name="daily_entries"
    )
    shift = models.ForeignKey(
        "master_data.FactoryShift",
        on_delete=models.PROTECT,
        related_name="daily_production_entries",
        null=True,
        blank=True,
    )
    production_date = models.DateField()
    actual_manpower = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    working_minutes = models.PositiveIntegerField(
        default=480,
        validators=[MinValueValidator(0)],
        help_text="Actual minutes worked by each operator on this article.",
    )
    actual_output = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    defect_pieces = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Defective pieces found during final/end-of-line inspection for this output.",
    )

    # Derived / stored fields, computed on save() via calculations.py
    produced_minutes = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    available_minutes = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    actual_efficiency = models.DecimalField(max_digits=6, decimal_places=4, editable=False)
    planned_efficiency = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="Supervisor-entered planned efficiency for the day, stored as a decimal.",
    )
    target_output = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    gain_loss_minutes = models.DecimalField(max_digits=12, decimal_places=2, editable=False)

    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="production_entries_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="production_entries_updated",
    )

    class Meta:
        ordering = ["-production_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "production_date"], name="unique_assignment_per_day"
            )
        ]
        indexes = [models.Index(fields=["production_date"])]

    def __str__(self):
        return f"{self.assignment.production_line} - {self.production_date}"

    @property
    def sam(self):
        """SAM fetched from the Article used by this entry's order."""
        return self.assignment.production_order.article.sam

    @property
    def planned_efficiency_percent(self):
        """Planned efficiency formatted as a whole percentage for displays."""
        return self.planned_efficiency * 100

    @property
    def actual_efficiency_percent(self):
        """Actual efficiency formatted as a whole percentage for displays."""
        return self.actual_efficiency * 100

    @property
    def dhu(self):
        """Defects per Hundred Units: defective pieces divided by inspected output."""
        if self.actual_output <= 0:
            return Decimal("0")
        return (Decimal(self.defect_pieces) / Decimal(self.actual_output)) * Decimal("100")

    @property
    def defect_rate(self):
        """Alias for end-of-line defect rate used in dashboard and quality reporting."""
        return self.dhu

    def recompute_derived_fields(self):
        """
        Recalculate every derived field using planning/services/calculations.py.
        Called automatically from save(); also callable directly (e.g. from a
        management command) to recompute historical rows after a formula fix.
        """
        sam = self.assignment.production_order.article.sam

        self.available_minutes = calc.available_minutes(
            self.actual_manpower, self.working_minutes
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if self.planned_efficiency is None:
            self.planned_efficiency = Decimal("0")
        planned_productive_minutes = calc.planned_productive_minutes(
            self.available_minutes,
            self.planned_efficiency,
        )
        self.target_output = calc.target_output(planned_productive_minutes, sam).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        self.produced_minutes = calc.produced_minutes(self.actual_output, sam).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.actual_efficiency = calc.actual_efficiency(
            self.produced_minutes, self.available_minutes
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        self.gain_loss_minutes = calc.gain_loss_minutes(
            self.produced_minutes, planned_productive_minutes
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def save(self, *args, **kwargs):
        # Recompute before validation so stale legacy derived values are normalized
        # before Django checks model field precision and the order limit.
        self.recompute_derived_fields()
        try:
            self.full_clean()
        except ValidationError:
            # Re-raise so callers see the validation error
            raise

        super().save(*args, **kwargs)

    def clean(self):
        """Validate entry inputs and protect the linked order quantity."""
        # Basic sanity: assignment should be present
        if not self.assignment_id or self.actual_output is None:
            return

        production_date = self.production_date
        start_date = self.assignment.start_date
        if isinstance(production_date, str):
            production_date = datetime.strptime(production_date, "%Y-%m-%d").date()
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        if production_date is not None and start_date is not None and production_date < start_date:
            raise ValidationError({
                "production_date": "Production date cannot be before the assignment start date."
            })
        if (
            self.actual_manpower is not None
            and self.actual_manpower > self.assignment.production_line.capacity_manpower
        ):
            raise ValidationError({
                "actual_manpower": "Actual manpower exceeds the line's capacity manpower."
            })

        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "assignment_id", "actual_output"
            ).first()
            if previous and previous["assignment_id"] != self.assignment_id:
                raise ValidationError({
                    "assignment": "The assignment cannot be changed after a production entry is saved."
                })

        if (
            self.working_minutes is not None
            and self.working_minutes > self.assignment.working_minutes_per_operator
        ):
            raise ValidationError({
                "working_minutes": (
                    "Working minutes cannot exceed the assignment's configured "
                    f"daily minutes ({self.assignment.working_minutes_per_operator})."
                )
            })

        order = self.assignment.production_order

        previous_value = 0
        if self.pk:
            try:
                previous_value = type(self).objects.get(pk=self.pk).actual_output
            except type(self).DoesNotExist:
                previous_value = 0

            if int(self.actual_output) == previous_value:
                return

        produced_total = order.total_produced_quantity
        produced_excluding = produced_total - previous_value
        remaining = max(order.order_quantity - produced_excluding, 0)
        if int(self.actual_output) > remaining:
            raise ValidationError({
                "actual_output": (
                    f"You cannot produce more than the total order quantity of {order.order_quantity} pcs. "
                    f"Actual production is {order.total_produced_quantity} pcs, so pending production is {order.pending_production} pcs."
                )
            })
