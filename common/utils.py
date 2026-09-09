"""
common/utils.py

Shared, reusable utility functions used across apps.
"""

from __future__ import annotations

from datetime import date, timedelta

from .models import AuditLog


def write_audit_log(user, action, instance, old_value=None, new_value=None):
    """
    Write a single AuditLog row for a tracked change.

    Args:
        user: The request.user performing the change (or None for system
            actions).
        action: One of AuditLog.Action values, e.g. AuditLog.Action.UPDATE.
        instance: The model instance being changed (used for model_name and
            object_id).
        old_value / new_value: JSON-serialisable dicts describing the
            before/after state. Keep these small (changed fields only)
            rather than dumping the whole object.
    """
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(instance.pk),
        old_value=old_value,
        new_value=new_value,
    )


def add_working_days(start_date: date, days: int, holiday_dates: set[date] | None = None,
                      weekly_off: int | None = 6) -> date:
    """
    Add `days` working days to start_date, skipping holidays and a weekly
    off day (default 6 = Sunday, Python's date.weekday() convention where
    Monday=0 ... Sunday=6). Pass weekly_off=None to not skip any weekday.

    This is intentionally kept separate from
    planning/services/calculations.expected_completion_date(), which stays
    a simple pure "add calendar days" function; this helper is for callers
    that need a holiday-aware version.
    """
    holiday_dates = holiday_dates or set()
    current = start_date
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current in holiday_dates:
            continue
        if weekly_off is not None and current.weekday() == weekly_off:
            continue
        remaining -= 1
    return current
