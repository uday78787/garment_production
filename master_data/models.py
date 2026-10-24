"""
master_data/models.py

Reference / setup data: production lines, articles (styles) with SAM, and
the factory holiday calendar.
"""

from datetime import datetime, time, timedelta

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

    class Meta(TimeStampedModel.Meta):
        abstract = False
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
    shift_minutes = models.PositiveSmallIntegerField(
        default=480,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        help_text="Planned working minutes for this shift.",
    )
    overtime_start_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional time when the overtime block begins.",
    )
    description = models.TextField(blank=True)
    hourly_schedule = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factory_shifts_assigned",
    )

    class Meta(TimeStampedModel.Meta):
        abstract = False
        ordering = ["factory__name", "start_time"]

    def __str__(self):
        return f"{self.factory.name} - {self.shift_name} ({self.start_time} to {self.end_time})"

    @staticmethod
    def _coerce_time(value: object) -> time | None:
        if value is None:
            return None
        if isinstance(value, str):
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    return datetime.strptime(value, fmt).time()
                except ValueError:
                    continue
            return None
        return value if isinstance(value, time) else None

    @property
    def total_shift_hours(self):
        return int(self.standard_hours + self.overtime_hours)

    def build_hourly_schedule(self, start_time=None, end_time=None, lunch_break_start=None, lunch_break_end=None):
        """Build a clean work-hour schedule with a lunch break and optional overtime."""
        shift_start = self._coerce_time(start_time or self.start_time)
        shift_end = self._coerce_time(end_time or self.end_time)
        lunch_start = self._coerce_time(lunch_break_start)
        lunch_end = self._coerce_time(lunch_break_end)

        if shift_start is None or shift_end is None:
            return []

        regular_hours = max(int(self.standard_hours or 0), 1)
        hours = []
        current = datetime.combine(datetime.today().date(), shift_start)
        for hour_number in range(1, regular_hours + 1):
            hour_end = current + timedelta(hours=1)
            slot = {
                "hour": hour_number,
                "start": current.strftime("%H:%M"),
                "end": hour_end.strftime("%H:%M"),
                "break_start": "",
                "break_end": "",
            }
            if lunch_start and lunch_end and current.time() < lunch_end and hour_end.time() > lunch_start:
                break_start = max(current.time(), lunch_start)
                break_end = min(hour_end.time(), lunch_end)
                if break_start < break_end:
                    slot["break_start"] = break_start.strftime("%H:%M")
                    slot["break_end"] = break_end.strftime("%H:%M")
            hours.append(slot)
            current = hour_end

        overtime_start = self._coerce_time(self.overtime_start_time)
        overtime_hours = max(int(self.overtime_hours or 0), 0)
        if overtime_start and overtime_hours:
            overtime_base = datetime.combine(datetime.today().date(), overtime_start)
            for hour_number in range(regular_hours + 1, regular_hours + overtime_hours + 1):
                slot_start = overtime_base + timedelta(hours=(hour_number - regular_hours - 1))
                slot_end = slot_start + timedelta(hours=1)
                hours.append({
                    "hour": hour_number,
                    "start": slot_start.strftime("%H:%M"),
                    "end": slot_end.strftime("%H:%M"),
                    "break_start": "",
                    "break_end": "",
                })

        if not hours:
            return [{"hour": 1, "start": shift_start.strftime("%H:%M"), "end": shift_end.strftime("%H:%M"), "break_start": "", "break_end": ""}]
        return hours

    def shift_end_time(self, current_dt=None):
        now = timezone.localtime(current_dt) if current_dt else timezone.localtime()
        start_date = now.date()
        overtime_start = self._coerce_time(self.overtime_start_time)
        start_time = self._coerce_time(self.start_time)
        end_time = self._coerce_time(self.end_time)
        if end_time is None:
            raise ValueError("Shift end time must be a valid time.")
        if overtime_start and start_time and overtime_start >= start_time:
            return datetime.combine(start_date, overtime_start) + timedelta(hours=self.overtime_hours)
        return datetime.combine(start_date, end_time)

    def is_active_at(self, current_dt=None):
        now = timezone.localtime(current_dt) if current_dt else timezone.localtime()
        current_time = now.time()
        start_time = self._coerce_time(self.start_time)
        end_time = self._coerce_time(self.end_time)
        if start_time is None or end_time is None:
            raise ValueError("Shift start and end times must be valid.")
        if start_time <= end_time:
            return start_time <= current_time <= end_time
        return current_time >= start_time or current_time <= end_time

    @classmethod
    def get_active_shift_for_factory(cls, factory, current_dt=None):
        active_shifts = cls.objects.filter(factory=factory, active=True).order_by("start_time")
        dt = timezone.localtime(current_dt) if current_dt else timezone.localtime()
        active_now = [shift for shift in active_shifts if shift.is_active_at(dt)]
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
        if start_time is None or end_time is None:
            raise ValueError("Shift start and end times must be valid.")

        shift_start_dt = timezone.make_aware(datetime.combine(now.date(), start_time), timezone.get_current_timezone())
        if start_time <= end_time:
            shift_end_dt = timezone.make_aware(datetime.combine(now.date(), end_time), timezone.get_current_timezone())
        else:
            shift_end_dt = timezone.make_aware(datetime.combine(now.date() + timedelta(days=1), end_time), timezone.get_current_timezone())
            if now.time() < end_time:
                shift_start_dt -= timedelta(days=1)

        if now < shift_start_dt:
            shift_start_dt -= timedelta(days=1)

        if overtime_start:
            overtime_start_dt = timezone.make_aware(datetime.combine(now.date(), overtime_start), timezone.get_current_timezone())
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
        status = self.current_schedule_status(current_dt)
        return status["label"]

    def current_schedule_status(self, current_dt=None):
        """Return the configured hour number and break state for dashboard use."""
        now = timezone.localtime(current_dt) if current_dt else timezone.localtime()
        current_time = now.time()
        for item in self.hourly_schedule or []:
            try:
                hour_number = int(item.get("hour"))
                start = self._coerce_time(item.get("start"))
                end = self._coerce_time(item.get("end"))
                break_start = self._coerce_time(item.get("break_start"))
                break_end = self._coerce_time(item.get("break_end"))
            except (AttributeError, TypeError, ValueError):
                continue
            if not start or not end:
                continue
            if start <= current_time <= end:
                on_break = bool(
                    break_start is not None
                    and break_end is not None
                    and break_start <= current_time <= break_end
                )
                break_label = (
                    f"Break {break_start.strftime('%H:%M')} - {break_end.strftime('%H:%M')}"
                    if on_break and break_start is not None and break_end is not None
                    else ""
                )
                return {
                    "number": hour_number,
                    "label": f"Hour {hour_number}",
                    "window": f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}",
                    "on_break": on_break,
                    "break_label": break_label,
                }
        hour_start, hour_end = self.current_hour_window(current_dt)
        shift_start = self._coerce_time(self.start_time)
        if shift_start is None:
            raise ValueError("Shift start time must be a valid time.")
        fallback_number = max(int((hour_start - timezone.make_aware(datetime.combine(hour_start.date(), shift_start), timezone.get_current_timezone())).total_seconds() // 3600) + 1, 1)
        return {
            "number": fallback_number,
            "label": f"Hour {fallback_number}",
            "window": f"{hour_start.strftime('%H:%M')} - {hour_end.strftime('%H:%M')}",
            "on_break": False,
            "break_label": "",
        }

    @classmethod
    def default_factory_presets(cls, factory, assigned_by=None):
        presets = [
            (cls.PresetShift.DAY, "Day Shift", "08:00:00", "16:00:00"),
            (cls.PresetShift.OVER_TIME, "OT Shift", "16:00:00", "19:00:00"),
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
                    "shift_minutes": {cls.PresetShift.DAY: 480, cls.PresetShift.OVER_TIME: 180, cls.PresetShift.NIGHT: 480}[preset_code],
                    "standard_hours": {cls.PresetShift.DAY: 8, cls.PresetShift.OVER_TIME: 3, cls.PresetShift.NIGHT: 8}[preset_code],
                    "description": f"Factory preset: {shift_name}",
                    "active": True,
                    "assigned_by": assigned_by,
                },
            )
            if made:
                created.append(obj)
        return created


class LineShiftManpower(TimeStampedModel):
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name="line_shift_manpower")
    production_line = models.ForeignKey("ProductionLine", on_delete=models.PROTECT, related_name="shift_manpower")
    shift = models.ForeignKey(FactoryShift, on_delete=models.PROTECT, related_name="manpower_entries")
    entry_date = models.DateField(default=timezone.localdate)
    manpower = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    working_minutes = models.PositiveIntegerField(validators=[MinValueValidator(1)], editable=False)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="manpower_entries_entered")

    class Meta(TimeStampedModel.Meta):
        abstract = False
        ordering = ["-entry_date", "production_line__line_name", "shift__start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["production_line", "shift", "entry_date"],
                name="unique_line_shift_manpower_date",
            )
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        shift = self.shift if getattr(self, "shift_id", None) else None
        production_line = self.production_line if getattr(self, "production_line_id", None) else None
        factory = self.factory if getattr(self, "factory_id", None) else None
        if shift and production_line and shift.factory != production_line.factory:
            raise ValidationError("The shift and production line must belong to the same factory.")
        if factory and production_line and factory != production_line.factory:
            raise ValidationError("The factory must match the production line factory.")

    def save(self, *args, **kwargs):
        self.factory = self.production_line.factory
        self.working_minutes = self.shift.shift_minutes
        self.full_clean()
        return super().save(*args, **kwargs)


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

    class Meta(TimeStampedModel.Meta):
        abstract = False
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

    class Meta(TimeStampedModel.Meta):
        abstract = False
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

    class Meta(TimeStampedModel.Meta):
        abstract = False
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
