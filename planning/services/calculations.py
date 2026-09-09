"""
planning/services/calculations.py

Centralised, reusable garment-production calculation functions.

Design rules followed in this module:
- Every function is a *pure* function: given the same inputs it always returns
  the same output, and it never touches the database, request, or Django
  settings. This makes the functions trivial to unit test and safe to reuse
  from views, management commands, Celery tasks, DRF serializers, reports,
  etc.
- All efficiency values are expressed as decimals (e.g. 25% -> 0.25), not as
  whole numbers, unless a function name explicitly says otherwise.
- All "minutes" values are in standard minutes (SAM-based), not clock time.
- Functions raise ValueError for inputs that would produce meaningless or
  undefined results (division by zero, negative quantities, etc.) rather than
  silently returning 0, so calculation errors surface immediately instead of
  corrupting downstream reports.

Glossary
--------
SAM                  Standard Allotted Minutes to produce one piece.
Manpower              Number of operators working on the line.
Available minutes      Total standard minutes physically available in the day
                      (manpower * minutes worked per operator).
Planned efficiency     The efficiency the line is *expected* to run at on a
                      given day of the ramp-up curve.
Planned productive
minutes                Available minutes actually expected to be converted
                      into productive work, i.e. available_minutes *
                      planned_efficiency.
Target output           Expected pieces for the day, i.e.
                      planned_productive_minutes / SAM.
Produced minutes        Standard minutes actually earned from the pieces
                      produced, i.e. actual_output * SAM.
Actual efficiency       produced_minutes / available_minutes.
Gain/Loss minutes       produced_minutes - planned_productive_minutes.
                      Positive = gain (ahead of plan), negative = loss.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Union

Number = Union[int, float, Decimal]


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _to_decimal(value: Number) -> Decimal:
    """Normalise any numeric input to Decimal for precise arithmetic."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# --------------------------------------------------------------------------- #
# 1. Planned efficiency (ramp-up curve)
# --------------------------------------------------------------------------- #

def planned_efficiency(
    start_efficiency: Number,
    daily_efficiency_growth: Number,
    day_number: int,
    max_efficiency: Number,
) -> Decimal:
    """
    Efficiency planned for a given day of a line's ramp-up curve.

    A production line rarely starts at full efficiency. Instead, it "ramps
    up" from a start efficiency by a fixed daily growth amount until it hits
    a ceiling (max_efficiency), after which it stays flat.

    Args:
        start_efficiency: Efficiency on day 1, as a decimal (0.25 == 25%).
        daily_efficiency_growth: Efficiency added per day, as a decimal
            (0.05 == +5 percentage points/day).
        day_number: 1-indexed day of the assignment (day 1 = start date).
        max_efficiency: Efficiency ceiling, as a decimal (0.60 == 60%).

    Returns:
        Decimal efficiency for that day, capped at max_efficiency and
        floored at 0.

    Raises:
        ValueError: if day_number < 1.
    """
    if day_number < 1:
        raise ValueError("day_number must be >= 1")

    start = _to_decimal(start_efficiency)
    growth = _to_decimal(daily_efficiency_growth)
    cap = _to_decimal(max_efficiency)

    raw = start + growth * Decimal(day_number - 1)
    capped = min(raw, cap)
    return max(capped, Decimal("0"))


# --------------------------------------------------------------------------- #
# 2. Available minutes
# --------------------------------------------------------------------------- #

def available_minutes(manpower: Number, working_minutes_per_operator: Number) -> Decimal:
    """
    Total standard minutes available in the day for a line.

        available_minutes = manpower * working_minutes_per_operator

    Args:
        manpower: Number of operators on the line that day.
        working_minutes_per_operator: Minutes each operator works that day.

    Raises:
        ValueError: if manpower or working_minutes_per_operator is negative.
    """
    mp = _to_decimal(manpower)
    minutes = _to_decimal(working_minutes_per_operator)
    if mp < 0 or minutes < 0:
        raise ValueError("manpower and working_minutes_per_operator must be >= 0")
    return mp * minutes


# --------------------------------------------------------------------------- #
# 3. Planned productive minutes
# --------------------------------------------------------------------------- #

def planned_productive_minutes(available_minutes_value: Number, planned_efficiency_value: Number) -> Decimal:
    """
    Minutes expected to be converted into productive output.

        planned_productive_minutes = available_minutes * planned_efficiency
    """
    avail = _to_decimal(available_minutes_value)
    eff = _to_decimal(planned_efficiency_value)
    if avail < 0:
        raise ValueError("available_minutes_value must be >= 0")
    return avail * eff


# --------------------------------------------------------------------------- #
# 4. Target output
# --------------------------------------------------------------------------- #

def target_output(planned_productive_minutes_value: Number, sam: Number) -> Decimal:
    """
    Expected number of pieces for the day.

        target_output = planned_productive_minutes / SAM

    Raises:
        ValueError: if sam <= 0.
    """
    sam_d = _to_decimal(sam)
    if sam_d <= 0:
        raise ValueError("sam must be > 0")
    ppm = _to_decimal(planned_productive_minutes_value)
    return ppm / sam_d


# --------------------------------------------------------------------------- #
# 5. Hourly target
# --------------------------------------------------------------------------- #

def hourly_target(manpower: Number, sam: Number, planned_efficiency_value: Number) -> Decimal:
    """
    Expected pieces per hour for the line at a given efficiency.

        hourly_target = (manpower * 60 * planned_efficiency) / SAM

    Raises:
        ValueError: if sam <= 0.
    """
    sam_d = _to_decimal(sam)
    if sam_d <= 0:
        raise ValueError("sam must be > 0")
    mp = _to_decimal(manpower)
    eff = _to_decimal(planned_efficiency_value)
    return (mp * Decimal(60) * eff) / sam_d


# --------------------------------------------------------------------------- #
# 6. Produced minutes
# --------------------------------------------------------------------------- #

def produced_minutes(actual_output: Number, sam: Number) -> Decimal:
    """
    Standard minutes earned from the pieces actually produced.

        produced_minutes = actual_output * SAM

    Raises:
        ValueError: if actual_output < 0 or sam <= 0.
    """
    sam_d = _to_decimal(sam)
    if sam_d <= 0:
        raise ValueError("sam must be > 0")
    out = _to_decimal(actual_output)
    if out < 0:
        raise ValueError("actual_output must be >= 0")
    return out * sam_d


# --------------------------------------------------------------------------- #
# 7. Actual efficiency
# --------------------------------------------------------------------------- #

def actual_efficiency(produced_minutes_value: Number, available_minutes_value: Number) -> Decimal:
    """
    Efficiency actually achieved for the day.

        actual_efficiency = produced_minutes / available_minutes

    Returns 0 when available_minutes is 0 (no operators/no time logged),
    instead of raising, since this is a normal "no work happened" case that
    reports need to render gracefully.
    """
    avail = _to_decimal(available_minutes_value)
    produced = _to_decimal(produced_minutes_value)
    if avail == 0:
        return Decimal("0")
    return produced / avail


# --------------------------------------------------------------------------- #
# 8. Gain / loss minutes
# --------------------------------------------------------------------------- #

def gain_loss_minutes(produced_minutes_value: Number, planned_productive_minutes_value: Number) -> Decimal:
    """
    Minutes gained (positive) or lost (negative) versus plan.

        gain_loss_minutes = produced_minutes - planned_productive_minutes
    """
    produced = _to_decimal(produced_minutes_value)
    planned = _to_decimal(planned_productive_minutes_value)
    return produced - planned


# --------------------------------------------------------------------------- #
# 9. Remaining quantity
# --------------------------------------------------------------------------- #

def remaining_quantity(order_quantity: Number, cumulative_produced_quantity: Number) -> Decimal:
    """
    Pieces still to be produced on an order.

        remaining_quantity = order_quantity - cumulative_produced_quantity

    Never returns a negative number; an order can't be "more than done" for
    planning purposes, so overproduction is floored at 0.

    Raises:
        ValueError: if order_quantity < 0 or cumulative_produced_quantity < 0.
    """
    order_qty = _to_decimal(order_quantity)
    produced_qty = _to_decimal(cumulative_produced_quantity)
    if order_qty < 0 or produced_qty < 0:
        raise ValueError("order_quantity and cumulative_produced_quantity must be >= 0")
    return max(order_qty - produced_qty, Decimal("0"))


# --------------------------------------------------------------------------- #
# 10. Forecast daily output
# --------------------------------------------------------------------------- #

def forecast_daily_output(
    manpower: Number,
    working_minutes_per_operator: Number,
    sam: Number,
    efficiency: Number,
) -> Decimal:
    """
    Forecast pieces/day for a line running at a given manpower & efficiency.

        forecast_daily_output = (manpower * working_minutes_per_operator * efficiency) / SAM

    Useful for "what-if" forecasting (e.g. forecast_manpower_override /
    forecast_efficiency_override on LineAssignment).

    Raises:
        ValueError: if sam <= 0.
    """
    sam_d = _to_decimal(sam)
    if sam_d <= 0:
        raise ValueError("sam must be > 0")
    avail = available_minutes(manpower, working_minutes_per_operator)
    eff = _to_decimal(efficiency)
    return (avail * eff) / sam_d


# --------------------------------------------------------------------------- #
# 11. Expected days remaining
# --------------------------------------------------------------------------- #

def expected_days_remaining(remaining_quantity_value: Number, forecast_daily_output_value: Number) -> int:
    """
    Whole number of days needed to finish the remaining quantity, at the
    given forecast daily output.

        expected_days_remaining = ceil(remaining_quantity / forecast_daily_output)

    Returns 0 when there is nothing remaining.

    Raises:
        ValueError: if forecast_daily_output_value <= 0 while there is a
            remaining quantity > 0 (i.e. the line will never finish at that
            rate — this must be surfaced rather than silently returning
            infinity or a wrong number).
    """
    remaining = _to_decimal(remaining_quantity_value)
    if remaining <= 0:
        return 0

    forecast = _to_decimal(forecast_daily_output_value)
    if forecast <= 0:
        raise ValueError(
            "forecast_daily_output_value must be > 0 when remaining_quantity_value > 0"
        )

    days = remaining / forecast
    return int(math.ceil(days))


# --------------------------------------------------------------------------- #
# 12. Expected completion date
# --------------------------------------------------------------------------- #

def expected_completion_date(start_date: date, days_remaining: int) -> date:
    """
    Calendar date the order is expected to complete.

        expected_completion_date = start_date + days_remaining

    `days_remaining` is treated as the number of additional *working* days
    needed from (and including) start_date; callers who need to skip
    Sundays/holidays should pre-adjust days_remaining using their holiday
    calendar (see common/utils for a holiday-aware day adder) before calling
    this function, keeping this function itself simple and pure.

    Raises:
        ValueError: if days_remaining < 0.
    """
    if days_remaining < 0:
        raise ValueError("days_remaining must be >= 0")
    return start_date + timedelta(days=days_remaining)


# --------------------------------------------------------------------------- #
# Convenience: build a full day-by-day ramp-up efficiency curve
# --------------------------------------------------------------------------- #

def build_efficiency_curve(
    start_efficiency: Number,
    daily_efficiency_growth: Number,
    max_efficiency: Number,
    number_of_days: int,
) -> list[Decimal]:
    """
    Returns a list of `number_of_days` planned_efficiency values, one per
    day, starting at day 1. Handy for pre-computing a whole assignment's
    forecast in one pass (e.g. for a Gantt/forecast chart).
    """
    if number_of_days < 0:
        raise ValueError("number_of_days must be >= 0")
    return [
        planned_efficiency(start_efficiency, daily_efficiency_growth, day, max_efficiency)
        for day in range(1, number_of_days + 1)
    ]
