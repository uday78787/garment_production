"""
master_data/models.py

Reference / setup data: production lines, articles (styles) with SAM, and
the factory holiday calendar.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

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


class FactoryShift(TimeStampedModel):
    class PresetShift(models.TextChoices):
        DAY = "DAY", "Day Shift"
        OVER_TIME = "OVER_TIME", "Over Time"
        NIGHT = "NIGHT", "Night Shift"

    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="factory_shifts")
    shift_name = models.CharField(max_length=60, help_text="Example: Day Shift, Night Shift, General")
    preset_code = models.CharField(
        max_length=20,
        choices=PresetShift.choices,
        blank=True,
        default="",
        help_text="Factory preset shift type; use Day Shift, Over Time, or Night Shift.",
    )
    start_time = models.TimeField(help_text="Shift start in local factory time")
    end_time = models.TimeField(help_text="Shift end in local factory time")
    standard_hours = models.PositiveSmallIntegerField(
        default=8,
        validators=[MinValueValidator(1), MaxValueValidator(8)],
        help_text="Factory shift length in hours, from 1 to 8 hours.",
    )
    overtime_hours = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(8)],
        help_text="Additional overtime hours for this shift.",
    )
    overtime_start_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional time when the overtime block begins.",
    )
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factory_shifts_assigned",
    )

    class Meta:
        ordering = ["factory__name", "start_time"]

    def __str__(self):
        return f"{self.factory.name} - {self.shift_name} ({self.start_time} to {self.end_time})"

    @staticmethod
    def _coerce_time(value):
        if value is None:
            return None
        if isinstance(value, str):
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    return datetime.strptime(value, fmt).time()
                except ValueError:
                    continue
            return value
        return value

    @property
    def total_shift_hours(self):
        return int(self.standard_hours + self.overtime_hours)

    def shift_end_time(self, current_dt=None):
        now = timezone.localtime(current_dt) if current_dt else timezone.localtime()
        start_date = now.date()
        overtime_start = self._coerce_time(self.overtime_start_time)
        start_time = self._coerce_time(self.start_time)
        if overtime_start and overtime_start >= start_time:
            return datetime.combine(start_date, overtime_start) + timedelta(hours=self.overtime_hours)
        return datetime.combine(start_date, self._coerce_time(self.end_time))

    @property
    def is_active_now(self):
        now = timezone.localtime().time()
        if self.start_time <= self.end_time:
            return self.start_time <= now <= self.end_time
        return now >= self.start_time or now <= self.end_time

    @classmethod
    def get_active_shift_for_factory(cls, factory, current_dt=None):
        active_shifts = cls.objects.filter(factory=factory, active=True).order_by("start_time")
        dt = timezone.localtime(current_dt) if current_dt else timezone.localtime()
        active_now = [shift for shift in active_shifts if shift.is_active_now]
        if active_now:
            return active_now[0]
        if active_shifts:
            return active_shifts.first()
        return None

    def current_hour_window(self, current_dt=None):
        now = timezone.localtime(current_dt) if current_dt else timezone.localtime()
        start_time = self._coerce_time(self.start_time)
        end_time = self._coerce_time(self.end_time)
        overtime_start = self._coerce_time(self.overtime_start_time)

        shift_start_dt = datetime.combine(now.date(), start_time)
        if start_time <= end_time:
            shift_end_dt = datetime.combine(now.date(), end_time)
        else:
            shift_end_dt = datetime.combine(now.date() + timedelta(days=1), end_time)
            if now.time() < end_time:
                shift_start_dt -= timedelta(days=1)

        if now < shift_start_dt:
            shift_start_dt -= timedelta(days=1)

        if overtime_start:
            overtime_start_dt = datetime.combine(now.date(), overtime_start)
            if now.time() < overtime_start:
                overtime_start_dt -= timedelta(days=1)
            if now >= overtime_start_dt:
                shift_end_dt = max(shift_end_dt, overtime_start_dt + timedelta(hours=self.overtime_hours))

        elapsed_seconds = max((now - shift_start_dt).total_seconds(), 0)
        current_hour_index = min(int(elapsed_seconds // 3600), int((shift_end_dt - shift_start_dt).total_seconds() // 3600) - 1)
        hour_start = shift_start_dt + timedelta(hours=current_hour_index)
        hour_end = min(hour_start + timedelta(hours=1), shift_end_dt)
        return hour_start, hour_end

    def current_hour_label(self, current_dt=None):
        hour_start, hour_end = self.current_hour_window(current_dt)
        return f"{hour_start.strftime('%H:%M')} - {hour_end.strftime('%H:%M')}"

    @classmethod
    def default_factory_presets(cls, factory, assigned_by=None):
        presets = [
            (cls.PresetShift.DAY, "Day Shift", "08:00:00", "16:00:00"),
            (cls.PresetShift.OVER_TIME, "Over Time", "16:00:00", "20:00:00"),
            (cls.PresetShift.NIGHT, "Night Shift", "20:00:00", "08:00:00"),
        ]
        created = []
        for preset_code, shift_name, start_time, end_time in presets:
            obj, made = cls.objects.get_or_create(
                factory=factory,
                preset_code=preset_code,
                defaults={
                    "shift_name": shift_name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "description": f"Factory preset: {shift_name}",
                    "active": True,
                    "assigned_by": assigned_by,
                },
            )
            if made:
                created.append(obj)
        return created


class ProductionLineShiftAssignment(TimeStampedModel):
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="line_shift_assignments")
    production_line = models.ForeignKey("ProductionLine", on_delete=models.PROTECT, related_name="shift_assignments")
    shift = models.ForeignKey(FactoryShift, on_delete=models.PROTECT, related_name="line_assignments")
    assignment_date = models.DateField(default=timezone.localdate)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="line_shift_assignments",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["assignment_date", "production_line__line_name"]
        constraints = [
            models.UniqueConstraint(
                fields=("factory", "production_line", "shift", "assignment_date"),
                name="unique_factory_line_shift_assignment",
            )
        ]

    def __str__(self):
        return f"{self.production_line} - {self.shift.shift_name} ({self.assignment_date})"


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
