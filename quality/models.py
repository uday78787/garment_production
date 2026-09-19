from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel
from master_data.models import Article, Factory, ProductionLine
from planning.models import ProductionOrder


class DefectMaster(TimeStampedModel):
    class Category(models.TextChoices):
        CUTTING = "CUTTING", "Cutting"
        SEWING = "SEWING", "Sewing"
        FINISHING = "FINISHING", "Finishing"
        WASH = "WASH", "Wash"
        PACKING = "PACKING", "Packing"
        OTHER = "OTHER", "Other"

    class Severity(models.TextChoices):
        CRITICAL = "CRITICAL", "Critical"
        MAJOR = "MAJOR", "Major"
        MINOR = "MINOR", "Minor"

    defect_code = models.CharField(max_length=50, unique=True)
    defect_name = models.CharField(max_length=120)
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER)
    operation = models.CharField(max_length=100, blank=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MAJOR)
    description = models.TextField(blank=True)
    department = models.CharField(max_length=80, blank=True, help_text="Department or DCL group owning this defect definition.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["defect_code"]

    def __str__(self):
        return f"{self.defect_code} - {self.defect_name}"

    @classmethod
    def seed_default_defects(cls):
        defaults = [
            ("DCL-001", "Broken Stitch", "SEWING", "Sewing", "MAJOR", "Sewing seam broken or loose stitch", "SEWING"),
            ("DCL-002", "Open Seam", "SEWING", "Sewing", "CRITICAL", "Seam opened during inspection", "SEWING"),
            ("DCL-003", "Uneven Hem", "FINISHING", "Finishing", "MAJOR", "Hem not aligned", "FINISHING"),
            ("DCL-004", "Skewed Sleeve", "SEWING", "Sewing", "MAJOR", "Sleeve alignment out of tolerance", "SEWING"),
            ("DCL-005", "Shading Issue", "CUTTING", "Cutting", "MINOR", "Fabric tone mismatch", "CUTTING"),
            ("DCL-006", "Missing Button", "PACKING", "Packing", "CRITICAL", "Button missing or not attached", "PACKING"),
            ("DCL-007", "Oil Stain", "CUTTING", "Cutting", "MAJOR", "Stain from machine or handling", "CUTTING"),
        ]
        created = []
        for code, name, category, operation, severity, description, department in defaults:
            obj, made = cls.objects.get_or_create(
                defect_code=code,
                defaults={
                    "defect_name": name,
                    "category": category,
                    "operation": operation,
                    "severity": severity,
                    "description": description,
                    "department": department,
                    "active": True,
                },
            )
            created.append((obj, made))
        return created


class HourlyLineCheck(TimeStampedModel):
    class Meta:
        ordering = ["-check_date", "-check_hour", "-created_at"]

    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="hourly_line_checks")
    production_line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, related_name="hourly_line_checks")
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="hourly_line_checks")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="hourly_line_checks")
    check_date = models.DateField()
    check_hour = models.PositiveIntegerField(default=9, help_text="Hour of the shift check, for example 9, 10, 11.")
    shift_name = models.CharField(max_length=100, blank=True)
    inspected_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    passed_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    defective_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    checker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="hourly_line_checks")
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="SUBMITTED", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="hourly_line_checks_created")

    @property
    def total_defects(self):
        return sum(item.defect_quantity for item in self.details.all())

    @property
    def dhu(self):
        if self.inspected_quantity <= 0:
            return 0
        return float((Decimal(self.total_defects) / Decimal(self.inspected_quantity)) * Decimal("100"))

    def __str__(self):
        return f"{self.check_date} - {self.production_line} - H{self.check_hour}"


class HourlyLineCheckDetail(TimeStampedModel):
    hourly_check = models.ForeignKey(HourlyLineCheck, on_delete=models.CASCADE, related_name="details")
    defect = models.ForeignKey(DefectMaster, on_delete=models.PROTECT, related_name="hourly_check_details")
    tracking_code = models.CharField(max_length=6, unique=True, null=True, blank=True, editable=False)
    marked_at = models.DateTimeField(default=timezone.now, editable=False)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    defect_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    repaired_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    resolved = models.BooleanField(default=False)
    defect_image = models.ImageField(upload_to="quality/defects/%Y/%m/%d/", blank=True, null=True, help_text="Upload a defect image captured by QA at the line.")
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="hourly_check_details_created")

    class Meta:
        ordering = ["defect__defect_code"]

    def __str__(self):
        return f"{self.hourly_check} - {self.defect.defect_code}"


class QualityInspection(TimeStampedModel):
    class InspectionType(models.TextChoices):
        INLINE = "INLINE", "Inline"
        END_LINE = "END_LINE", "End Line"
        FINAL = "FINAL", "Final"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        HOLD = "HOLD", "Hold"
        REJECTED = "REJECTED", "Rejected"

    inspection_number = models.CharField(max_length=80, unique=True)
    inspection_date = models.DateField()
    inspection_time = models.TimeField(null=True, blank=True)
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="quality_inspections")
    production_line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, related_name="quality_inspections")
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="quality_inspections")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="quality_inspections")
    buyer = models.CharField(max_length=150, blank=True, default="")
    inspection_type = models.CharField(max_length=20, choices=InspectionType.choices, default=InspectionType.END_LINE)
    shift = models.CharField(max_length=40, blank=True)
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="quality_inspections")
    sample_size = models.PositiveIntegerField(default=0)
    inspected_quantity = models.PositiveIntegerField(default=0)
    passed_quantity = models.PositiveIntegerField(default=0)
    rejected_quantity = models.PositiveIntegerField(default=0)
    rework_quantity = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="quality_inspections_created")

    class Meta:
        ordering = ["-inspection_date", "-created_at"]

    def __str__(self):
        return f"{self.inspection_number} - {self.production_line}"

    @property
    def total_defects(self):
        return sum(item.defect_quantity for item in self.defects.all())

    @property
    def dhu(self):
        from .calculations import calculate_dhu
        return calculate_dhu(self.total_defects, self.inspected_quantity)

    @property
    def defect_rate(self):
        from .calculations import calculate_defect_rate
        return calculate_defect_rate(self.total_defects, self.inspected_quantity)

    @property
    def fpy(self):
        from .calculations import calculate_fpy
        return calculate_fpy(self.passed_quantity, self.inspected_quantity)

    def save(self, *args, **kwargs):
        if not self.buyer:
            self.buyer = self.production_order.customer_name or ""
        super().save(*args, **kwargs)


class InspectionDefect(TimeStampedModel):
    inspection = models.ForeignKey(QualityInspection, on_delete=models.CASCADE, related_name="defects")
    defect = models.ForeignKey(DefectMaster, on_delete=models.PROTECT, related_name="inspection_occurrences")
    defect_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    affected_piece_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    operation = models.CharField(max_length=100, blank=True)
    severity = models.CharField(max_length=20, choices=DefectMaster.Severity.choices, default=DefectMaster.Severity.MAJOR)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="inspection_defects_created")

    class Meta:
        ordering = ["defect__defect_code"]

    def __str__(self):
        return f"{self.inspection.inspection_number} - {self.defect.defect_code}"


class QualityTarget(TimeStampedModel):
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, blank=True, null=True, related_name="quality_targets")
    production_line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, blank=True, null=True, related_name="quality_targets")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, blank=True, null=True, related_name="quality_targets")
    inspection_type = models.CharField(max_length=20, choices=QualityInspection.InspectionType.choices, default=QualityInspection.InspectionType.END_LINE)
    dhu_target = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5.00"))
    fpy_target = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("95.00"))
    defect_rate_target = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5.00"))
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Quality Target for {self.production_line or self.factory or self.article or 'All'}"


class QualityHold(TimeStampedModel):
    class Status(models.TextChoices):
        HOLD = "HOLD", "Hold"
        RELEASED = "RELEASED", "Released"
        REJECTED = "REJECTED", "Rejected"

    hold_number = models.CharField(max_length=80, unique=True)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="quality_holds")
    line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, related_name="quality_holds")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="quality_holds")
    quantity = models.PositiveIntegerField(default=0)
    reason = models.TextField()
    severity = models.CharField(max_length=20, choices=DefectMaster.Severity.choices, default=DefectMaster.Severity.MAJOR)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="quality_holds_created")
    hold_date = models.DateField(auto_now_add=True)
    release_date = models.DateField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="quality_holds_released")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.HOLD)

    class Meta:
        ordering = ["-hold_date"]

    def __str__(self):
        return f"{self.hold_number} - {self.production_order}"


class Rework(TimeStampedModel):
    class Status(models.TextChoices):
        ASSIGNED = "ASSIGNED", "Assigned"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        VERIFIED = "VERIFIED", "Verified"
        CLOSED = "CLOSED", "Closed"

    inspection = models.ForeignKey(QualityInspection, on_delete=models.PROTECT, related_name="reworks")
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="reworks")
    line = models.ForeignKey(ProductionLine, on_delete=models.PROTECT, related_name="reworks")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="reworks")
    defect = models.ForeignKey(DefectMaster, on_delete=models.PROTECT, related_name="reworks")
    quantity = models.PositiveIntegerField(default=0)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="rework_assignments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    start_time = models.DateTimeField(null=True, blank=True)
    completion_time = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="rework_verifications")
    verification_result = models.CharField(max_length=80, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Rework {self.pk} - {self.article}"


class RootCauseAnalysis(TimeStampedModel):
    class Category(models.TextChoices):
        MAN = "MAN", "Man"
        MACHINE = "MACHINE", "Machine"
        MATERIAL = "MATERIAL", "Material"
        METHOD = "METHOD", "Method"
        MEASUREMENT = "MEASUREMENT", "Measurement"
        ENVIRONMENT = "ENVIRONMENT", "Environment"

    issue = models.CharField(max_length=200)
    category = models.CharField(max_length=25, choices=Category.choices, default=Category.METHOD)
    root_cause = models.TextField()
    corrective_action = models.TextField(blank=True)
    preventive_action = models.TextField(blank=True)
    responsible_person = models.CharField(max_length=120, blank=True)
    target_date = models.DateField(null=True, blank=True)
    actual_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="OPEN", blank=True)
    effectiveness_check = models.TextField(blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="rca_verified")

    def __str__(self):
        return self.issue


class CAPA(TimeStampedModel):
    issue = models.ForeignKey(RootCauseAnalysis, on_delete=models.PROTECT, related_name="capa_entries")
    corrective_action = models.TextField()
    preventive_action = models.TextField(blank=True)
    responsible_person = models.CharField(max_length=120, blank=True)
    target_date = models.DateField(null=True, blank=True)
    actual_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="OPEN", blank=True)
    effectiveness_check = models.TextField(blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="capa_verified")

    def __str__(self):
        return f"CAPA for {self.issue}"
