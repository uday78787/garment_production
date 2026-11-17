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
    def scheduled_working_minutes(self):
        """Return minutes from the saved hourly schedule or shift boundaries."""
        schedule = self.hourly_schedule or []
        if not schedule:
            return self.shift_minutes

        total_minutes = 0
        previous_end = None
        for item in schedule:
            if not isinstance(item, dict):
                raise ValueError("Shift schedule entries must be objects.")
            start = self._coerce_time(item.get("start"))
            end = self._coerce_time(item.get("end"))
            if start is None or end is None:
                raise ValueError("Shift schedule entries must have valid start and end times.")

            start_minute = start.hour * 60 + start.minute
            start_day = item.get("start_day")
            if start_day is None:
                start_day = (previous_end // 1440) if previous_end is not None else 0
                while start_minute + start_day * 1440 < (previous_end or 0):
                    start_day += 1
            start_minute += int(start_day) * 1440

            end_minute = end.hour * 60 + end.minute
            end_day = item.get("end_day")
            if end_day is None:
                end_day = start_minute // 1440
                while end_minute + end_day * 1440 <= start_minute:
                    end_day += 1
            end_minute += int(end_day) * 1440
            if end_minute <= start_minute:
                raise ValueError("Shift hour end time must be after its start time.")

            duration = end_minute - start_minute
            break_start = self._coerce_time(item.get("break_start"))
            break_end = self._coerce_time(item.get("break_end"))
            if bool(break_start) != bool(break_end):
                raise ValueError("Shift breaks must have both start and end times.")
            if break_start and break_end:
                break_start_minute = break_start.hour * 60 + break_start.minute
                break_start_day = start_minute // 1440
                while break_start_minute + break_start_day * 1440 < start_minute:
                    break_start_day += 1
                break_start_minute += break_start_day * 1440

                break_end_minute = break_end.hour * 60 + break_end.minute
                break_end_day = break_start_minute // 1440
                while break_end_minute + break_end_day * 1440 <= break_start_minute:
                    break_end_day += 1
                break_end_minute += break_end_day * 1440
                if not start_minute <= break_start_minute < break_end_minute <= end_minute:
                    raise ValueError("Shift breaks must fall within their configured hour.")
                duration -= break_end_minute - break_start_minute

            if duration < 0:
                raise ValueError("Shift break duration cannot exceed its hour.")
            total_minutes += duration
            previous_end = end_minute
        return total_minutes

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
            in_hour = (
                start <= current_time < end
                if start < end
                else current_time >= start or current_time < end
            )
            if in_hour:
                on_break = bool(
                    break_start is not None
                    and break_end is not None
                    and break_start != break_end
                    and (
                        break_start <= current_time < break_end
                        if break_start < break_end
                        else current_time >= break_start or current_time < break_end
                    )
                )
                break_label = (
                    f"Break {break_start.strftime('%H:%M')} - {break_end.strftime('%H:%M')}"
                    if on_break and break_start is not None and break_end is not None
                    else ""
                )
                return {
                    "number": hour_number,
                    "label": str(item.get("name") or f"Hour {hour_number}"),
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
        self.working_minutes = self.shift.scheduled_working_minutes
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
