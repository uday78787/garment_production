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
from django.core.exceptions import ValidationError
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

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    order_number = models.CharField(max_length=50, unique=True, verbose_name="PO No. / Order ID")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="orders")
    order_quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name="Order Quantity",
    )
    customer_name = models.CharField(max_length=150, verbose_name="Client")
    merchant = models.CharField(max_length=150, blank=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    start_date = models.DateField(null=True, blank=True, verbose_name="Order Start Date")
    delivery_date = models.DateField(verbose_name="Order End Date")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["-delivery_date"]

    def __str__(self):
        return f"{self.order_number} ({self.article.article_code})"

    def clean(self):
        super().clean()
        if self.start_date and self.delivery_date and self.delivery_date < self.start_date:
            raise ValidationError({"delivery_date": "Order end date must be on or after the order start date."})

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
    start_date = models.DateField(verbose_name="Operation Start Date")
    end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Operation End Date",
    )
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

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Operation end date must be on or after the start date."})

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


class FactorySection(TimeStampedModel):
    """A factory-specific department/operation in its configured process flow."""

    factory = models.ForeignKey(
        "master_data.Factory",
        on_delete=models.PROTECT,
        related_name="process_sections",
    )
    name = models.CharField(max_length=100)
    quality_criteria = models.TextField(
        help_text="Quality criteria operators and inspectors must follow at this section.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["factory__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=("factory", "name"),
                name="unique_factory_process_section_name",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.quality_criteria.strip():
            raise ValidationError({"quality_criteria": "Set the quality criteria required at this section."})

    def __str__(self):
        return f"{self.factory.name} - {self.name}"


class SectionEAVField(TimeStampedModel):
    """Superuser-defined custom data field captured when a section batch runs."""

    class FieldType(models.TextChoices):
        TEXT = "TEXT", "Text"
        NUMBER = "NUMBER", "Number"
        DATE = "DATE", "Date"
        YES_NO = "YES_NO", "Yes / No"
        CHOICE = "CHOICE", "Choice"

    section = models.ForeignKey(
        FactorySection,
        on_delete=models.CASCADE,
        related_name="eav_fields",
    )
    name = models.CharField(max_length=100)
    key = models.SlugField(max_length=60)
    field_type = models.CharField(max_length=10, choices=FieldType.choices)
    required = models.BooleanField(default=False)
    choices = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["section__factory__name", "section__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=("section", "key"),
                name="unique_section_eav_field_key",
            )
        ]

    def clean(self):
        super().clean()
        if self.field_type == self.FieldType.CHOICE:
            if not isinstance(self.choices, list) or not self.choices:
                raise ValidationError({"choices": "Add at least one choice for a Choice field."})
            if any(not isinstance(choice, str) or not choice.strip() for choice in self.choices):
                raise ValidationError({"choices": "Each choice must be a non-empty text value."})
            if len({choice.strip() for choice in self.choices}) != len(self.choices):
                raise ValidationError({"choices": "Choices must be unique."})
        elif self.choices:
            raise ValidationError({"choices": "Choices are only supported for Choice fields."})

    def __str__(self):
        return f"{self.section} - {self.name}"


class ArticleProcessFlow(TimeStampedModel):
    """A reusable, ordered operation flow for one Article at one factory."""

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="process_flows",
    )
    factory = models.ForeignKey(
        "master_data.Factory",
        on_delete=models.PROTECT,
        related_name="article_process_flows",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="article_process_flows_created",
    )

    class Meta:
        ordering = ["factory__name", "article__article_code"]
        constraints = [
            models.UniqueConstraint(
                fields=("article", "factory"),
                name="unique_article_factory_process_flow",
            )
        ]

    def clean(self):
        super().clean()
        if self.pk and self.steps.exclude(section__factory_id=self.factory_id).exists():
            raise ValidationError("All flow operations must use sections from the selected factory.")

    def __str__(self):
        return f"{self.article.article_code} - {self.factory.name}"


class ArticleProcessStep(TimeStampedModel):
    """One Article-specific operation, ordered independently of factory sections."""

    flow = models.ForeignKey(
        ArticleProcessFlow,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    sequence = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    section = models.ForeignKey(
        FactorySection,
        on_delete=models.PROTECT,
        related_name="article_process_steps",
    )
    operation_name = models.CharField(max_length=150)
    operation_description = models.TextField(blank=True)
    machine_equipment = models.CharField(max_length=150, blank=True)
    attachment_foot = models.CharField(max_length=150, blank=True)
    standard_time_minutes = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    quality_checkpoint = models.TextField(blank=True)
    skill_level = models.CharField(max_length=80, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=("flow", "sequence"),
                name="unique_article_process_step_sequence",
            )
        ]

    def clean(self):
        super().clean()
        if self.flow_id and self.section_id:
            if self.flow.factory_id != self.section.factory_id:
                raise ValidationError({"section": "Select a section belonging to this flow's factory."})

    def __str__(self):
        return f"{self.flow} - {self.sequence}. {self.operation_name}"


class OrderProcess(TimeStampedModel):
    """A production order's factory-scoped, ordered process flow."""

    production_order = models.OneToOneField(
        ProductionOrder,
        on_delete=models.CASCADE,
        related_name="process_flow",
    )
    factory = models.ForeignKey(
        "master_data.Factory",
        on_delete=models.PROTECT,
        related_name="order_processes",
    )
    source_flow = models.ForeignKey(
        ArticleProcessFlow,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_processes",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="order_processes_created",
    )

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if self.source_flow_id:
            if self.source_flow.factory_id != self.factory_id:
                raise ValidationError({"source_flow": "Select an Article flow for this factory."})
            if (
                self.production_order_id
                and self.source_flow.article_id != self.production_order.article_id
            ):
                raise ValidationError(
                    {"source_flow": "The Article flow must match the Production Order Article."}
                )
        if self.production_order_id and self.factory_id:
            assigned_factory_ids = set(
                self.production_order.assignments.values_list(
                    "production_line__factory_id", flat=True
                )
            )
            if assigned_factory_ids and assigned_factory_ids != {self.factory_id}:
                raise ValidationError(
                    {"factory": "The process factory must match the order's assigned production lines."}
                )

    def create_steps(self, article_flow):
        """Snapshot the Article flow and section settings for this order."""
        if not self.pk:
            raise ValidationError("Save the order process before adding its sections.")
        if self.steps.exists():
            raise ValidationError("This order process already has configured steps.")

        if (
            article_flow.factory_id != self.factory_id
            or article_flow.article_id != self.production_order.article_id
        ):
            raise ValidationError("The Article flow must match this order's Article and factory.")
        article_steps = article_flow.steps.select_related("section").order_by("sequence")
        if not article_steps.exists():
            raise ValidationError("Add at least one operation to the Article process flow.")

        for article_step in article_steps:
            section = article_step.section
            schema = [
                {
                    "key": item.key,
                    "label": item.name,
                    "type": item.field_type,
                    "required": item.required,
                    "choices": item.choices,
                }
                for item in section.eav_fields.filter(active=True).order_by("pk")
            ]
            OrderProcessStep.objects.create(
                process=self,
                article_step=article_step,
                section=section,
                section_name=section.name,
                operation_name=article_step.operation_name,
                operation_description=article_step.operation_description,
                operation_details={
                    "machine_equipment": article_step.machine_equipment,
                    "attachment_foot": article_step.attachment_foot,
                    "standard_time_minutes": (
                        str(article_step.standard_time_minutes)
                        if article_step.standard_time_minutes is not None
                        else ""
                    ),
                    "quality_checkpoint": article_step.quality_checkpoint,
                    "skill_level": article_step.skill_level,
                    "remarks": article_step.remarks,
                },
                sequence=article_step.sequence,
                quality_criteria="\n".join(
                    value
                    for value in (section.quality_criteria, article_step.quality_checkpoint)
                    if value
                ),
                field_schema=schema,
            )

    @property
    def is_complete(self):
        last_step = self.steps.order_by("-sequence").first()
        return bool(
            last_step
            and last_step.passed_quantity >= self.production_order.order_quantity
        )

    def __str__(self):
        return f"{self.production_order.order_number} - {self.factory.name}"


class OrderProcessStep(TimeStampedModel):
    process = models.ForeignKey(
        OrderProcess,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    section = models.ForeignKey(
        FactorySection,
        on_delete=models.PROTECT,
        related_name="order_steps",
    )
    article_step = models.ForeignKey(
        ArticleProcessStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_steps",
    )
    section_name = models.CharField(max_length=100)
    operation_name = models.CharField(max_length=150, blank=True)
    operation_description = models.TextField(blank=True)
    operation_details = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveSmallIntegerField()
    quality_criteria = models.TextField(blank=True)
    field_schema = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=("process", "sequence"),
                name="unique_order_process_step_sequence",
            ),
        ]

    def clean(self):
        super().clean()
        if self.process_id and self.section_id:
            if self.process.factory_id != self.section.factory_id:
                raise ValidationError({"section": "The section must belong to the process factory."})

    @property
    def previous_step(self):
        return self.process.steps.filter(sequence__lt=self.sequence).order_by("-sequence").first()

    @property
    def passed_quantity(self):
        return (
            self.batches.aggregate(
                total=models.Sum("quality_checks__passed_quantity")
            )["total"]
            or 0
        )

    @property
    def available_input_quantity(self):
        previous = self.previous_step
        available_from_previous = (
            previous.passed_quantity if previous else self.process.production_order.order_quantity
        )
        already_consumed = self.batches.aggregate(total=models.Sum("input_quantity"))["total"] or 0
        return max(available_from_previous - already_consumed, 0)

    def __str__(self):
        return (
            f"{self.process.production_order.order_number} - "
            f"{self.sequence}. {self.operation_name or self.section_name}"
        )


class ProcessBatch(TimeStampedModel):
    step = models.ForeignKey(
        OrderProcessStep,
        on_delete=models.CASCADE,
        related_name="batches",
    )
    input_quantity = models.PositiveIntegerField()
    output_quantity = models.PositiveIntegerField()
    field_values = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="process_batches_entered",
    )

    class Meta:
        ordering = ["created_at", "pk"]

    def clean(self):
        super().clean()
        if self.input_quantity is not None and self.input_quantity <= 0:
            raise ValidationError({"input_quantity": "Input quantity must be greater than zero."})
        if (
            self.output_quantity is not None
            and self.input_quantity is not None
            and self.output_quantity > self.input_quantity
        ):
            raise ValidationError({"output_quantity": "Output quantity cannot exceed input quantity."})
        if self.step_id and self.input_quantity is not None:
            available = self.step.available_input_quantity
            if self.pk:
                old_input = type(self).objects.filter(pk=self.pk).values_list(
                    "input_quantity", flat=True
                ).first()
                available += old_input or 0
            if self.input_quantity > available:
                raise ValidationError(
                    {"input_quantity": f"Only {available} pcs are available from the previous section."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def inspected_quantity(self):
        return self.quality_checks.aggregate(total=models.Sum("inspected_quantity"))["total"] or 0

    @property
    def passed_quantity(self):
        return self.quality_checks.aggregate(total=models.Sum("passed_quantity"))["total"] or 0

    @property
    def uninspected_quantity(self):
        return max(self.output_quantity - self.inspected_quantity, 0)

    def __str__(self):
        return f"{self.step} - {self.output_quantity} pcs output"


class ProcessQualityCheck(TimeStampedModel):
    batch = models.ForeignKey(
        ProcessBatch,
        on_delete=models.CASCADE,
        related_name="quality_checks",
    )
    inspected_quantity = models.PositiveIntegerField()
    passed_quantity = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="process_quality_checks",
    )

    class Meta:
        ordering = ["created_at", "pk"]

    def clean(self):
        super().clean()
        if self.inspected_quantity is not None and self.inspected_quantity <= 0:
            raise ValidationError({"inspected_quantity": "Inspected quantity must be greater than zero."})
        if (
            self.passed_quantity is not None
            and self.inspected_quantity is not None
            and self.passed_quantity > self.inspected_quantity
        ):
            raise ValidationError({"passed_quantity": "Passed quantity cannot exceed inspected quantity."})
        if self.batch_id and self.inspected_quantity is not None:
            available = self.batch.uninspected_quantity
            if self.pk:
                old_inspected = type(self).objects.filter(pk=self.pk).values_list(
                    "inspected_quantity", flat=True
                ).first()
                available += old_inspected or 0
            if self.inspected_quantity > available:
                raise ValidationError(
                    {"inspected_quantity": f"Only {available} uninspected output pcs remain in this batch."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def rejected_quantity(self):
        return self.inspected_quantity - self.passed_quantity

    def __str__(self):
        return f"{self.batch} - inspected {self.inspected_quantity} pcs"
