"""
planning/tests/test_calculations.py

Unit tests for planning/services/calculations.py.

Sample scenario used throughout (as specified in the requirements):
    Order Qty              = 100,000
    SAM                    = 35
    Manpower               = 40
    Minutes/operator/day    = 480
    Start Efficiency        = 25%  (0.25)
    Daily Growth            = 5%   (0.05)
    Maximum Efficiency      = 60%  (0.60)

With this scenario:
    available_minutes = 40 * 480 = 19,200 minutes/day
    Efficiency ramps 25% -> 30% -> 35% -> ... -> 60% on day 8, then holds.
"""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from planning.services import calculations as calc


class SampleScenarioMixin:
    """Shared sample-scenario constants for all test classes below."""

    ORDER_QTY = 100_000
    SAM = 35
    MANPOWER = 40
    MINUTES_PER_OPERATOR = 480
    START_EFFICIENCY = Decimal("0.25")
    DAILY_GROWTH = Decimal("0.05")
    MAX_EFFICIENCY = Decimal("0.60")

    # Derived, known-good values used across multiple tests.
    AVAILABLE_MINUTES = Decimal("19200")  # 40 * 480


class PlannedEfficiencyTests(SampleScenarioMixin, SimpleTestCase):
    def test_day_1_equals_start_efficiency(self):
        result = calc.planned_efficiency(
            self.START_EFFICIENCY, self.DAILY_GROWTH, 1, self.MAX_EFFICIENCY
        )
        self.assertEqual(result, Decimal("0.25"))

    def test_day_2_grows_by_daily_growth(self):
        result = calc.planned_efficiency(
            self.START_EFFICIENCY, self.DAILY_GROWTH, 2, self.MAX_EFFICIENCY
        )
        self.assertEqual(result, Decimal("0.30"))

    def test_day_8_reaches_max_efficiency(self):
        # 0.25 + 0.05 * 7 = 0.60 -> exactly the cap
        result = calc.planned_efficiency(
            self.START_EFFICIENCY, self.DAILY_GROWTH, 8, self.MAX_EFFICIENCY
        )
        self.assertEqual(result, Decimal("0.60"))

    def test_efficiency_is_capped_after_max_is_reached(self):
        # Day 20 would be 0.25 + 0.05*19 = 1.20 without a cap; must stay at 0.60
        result = calc.planned_efficiency(
            self.START_EFFICIENCY, self.DAILY_GROWTH, 20, self.MAX_EFFICIENCY
        )
        self.assertEqual(result, Decimal("0.60"))

    def test_efficiency_never_negative(self):
        result = calc.planned_efficiency(Decimal("-0.10"), Decimal("0"), 1, self.MAX_EFFICIENCY)
        self.assertEqual(result, Decimal("0"))

    def test_invalid_day_number_raises(self):
        with self.assertRaises(ValueError):
            calc.planned_efficiency(self.START_EFFICIENCY, self.DAILY_GROWTH, 0, self.MAX_EFFICIENCY)


class AvailableMinutesTests(SampleScenarioMixin, SimpleTestCase):
    def test_available_minutes_matches_expected(self):
        result = calc.available_minutes(self.MANPOWER, self.MINUTES_PER_OPERATOR)
        self.assertEqual(result, self.AVAILABLE_MINUTES)

    def test_zero_manpower_gives_zero_minutes(self):
        result = calc.available_minutes(0, self.MINUTES_PER_OPERATOR)
        self.assertEqual(result, Decimal("0"))

    def test_negative_manpower_raises(self):
        with self.assertRaises(ValueError):
            calc.available_minutes(-1, self.MINUTES_PER_OPERATOR)


class PlannedProductiveMinutesTests(SampleScenarioMixin, SimpleTestCase):
    def test_day_1_productive_minutes(self):
        eff = calc.planned_efficiency(self.START_EFFICIENCY, self.DAILY_GROWTH, 1, self.MAX_EFFICIENCY)
        result = calc.planned_productive_minutes(self.AVAILABLE_MINUTES, eff)
        # 19200 * 0.25 = 4800
        self.assertEqual(result, Decimal("4800.00"))

    def test_day_8_productive_minutes_at_max_efficiency(self):
        eff = calc.planned_efficiency(self.START_EFFICIENCY, self.DAILY_GROWTH, 8, self.MAX_EFFICIENCY)
        result = calc.planned_productive_minutes(self.AVAILABLE_MINUTES, eff)
        # 19200 * 0.60 = 11520
        self.assertEqual(result, Decimal("11520.00"))

    def test_negative_available_minutes_raises(self):
        with self.assertRaises(ValueError):
            calc.planned_productive_minutes(-1, Decimal("0.25"))


class TargetOutputTests(SampleScenarioMixin, SimpleTestCase):
    def test_day_1_target_output(self):
        # planned_productive_minutes = 4800, SAM = 35 -> 137.142857...
        result = calc.target_output(Decimal("4800.00"), self.SAM)
        self.assertAlmostEqual(float(result), 137.142857, places=4)

    def test_sam_zero_raises(self):
        with self.assertRaises(ValueError):
            calc.target_output(Decimal("4800.00"), 0)

    def test_sam_negative_raises(self):
        with self.assertRaises(ValueError):
            calc.target_output(Decimal("4800.00"), -5)


class HourlyTargetTests(SampleScenarioMixin, SimpleTestCase):
    def test_day_1_hourly_target(self):
        # (40 * 60 * 0.25) / 35 = 600/35 = 17.142857...
        result = calc.hourly_target(self.MANPOWER, self.SAM, self.START_EFFICIENCY)
        self.assertAlmostEqual(float(result), 17.142857, places=4)

    def test_day_8_hourly_target_at_max_efficiency(self):
        # (40 * 60 * 0.60) / 35 = 1440/35 = 41.142857...
        result = calc.hourly_target(self.MANPOWER, self.SAM, self.MAX_EFFICIENCY)
        self.assertAlmostEqual(float(result), 41.142857, places=4)

    def test_sam_zero_raises(self):
        with self.assertRaises(ValueError):
            calc.hourly_target(self.MANPOWER, 0, self.START_EFFICIENCY)


class ProducedMinutesTests(SampleScenarioMixin, SimpleTestCase):
    def test_produced_minutes_for_200_pieces(self):
        result = calc.produced_minutes(200, self.SAM)
        self.assertEqual(result, Decimal("7000"))

    def test_zero_output_gives_zero_minutes(self):
        result = calc.produced_minutes(0, self.SAM)
        self.assertEqual(result, Decimal("0"))

    def test_negative_output_raises(self):
        with self.assertRaises(ValueError):
            calc.produced_minutes(-10, self.SAM)

    def test_sam_zero_raises(self):
        with self.assertRaises(ValueError):
            calc.produced_minutes(200, 0)


class ActualEfficiencyTests(SampleScenarioMixin, SimpleTestCase):
    def test_actual_efficiency_for_200_pieces_produced(self):
        produced = calc.produced_minutes(200, self.SAM)  # 7000
        result = calc.actual_efficiency(produced, self.AVAILABLE_MINUTES)  # 7000/19200
        self.assertAlmostEqual(float(result), 0.364583, places=4)

    def test_actual_efficiency_zero_when_no_minutes_available(self):
        result = calc.actual_efficiency(Decimal("500"), Decimal("0"))
        self.assertEqual(result, Decimal("0"))


class GainLossMinutesTests(SampleScenarioMixin, SimpleTestCase):
    def test_gain_when_produced_exceeds_plan(self):
        planned = calc.planned_productive_minutes(
            self.AVAILABLE_MINUTES,
            calc.planned_efficiency(self.START_EFFICIENCY, self.DAILY_GROWTH, 1, self.MAX_EFFICIENCY),
        )  # 4800
        produced = calc.produced_minutes(200, self.SAM)  # 7000
        result = calc.gain_loss_minutes(produced, planned)
        self.assertEqual(result, Decimal("2200.00"))

    def test_loss_when_produced_below_plan(self):
        planned = Decimal("5000")
        produced = Decimal("4000")
        result = calc.gain_loss_minutes(produced, planned)
        self.assertEqual(result, Decimal("-1000"))


class RemainingQuantityTests(SampleScenarioMixin, SimpleTestCase):
    def test_remaining_after_partial_production(self):
        result = calc.remaining_quantity(self.ORDER_QTY, 5000)
        self.assertEqual(result, Decimal("95000"))

    def test_remaining_never_goes_negative(self):
        result = calc.remaining_quantity(self.ORDER_QTY, self.ORDER_QTY + 5000)
        self.assertEqual(result, Decimal("0"))

    def test_negative_order_quantity_raises(self):
        with self.assertRaises(ValueError):
            calc.remaining_quantity(-1, 0)


class ForecastDailyOutputTests(SampleScenarioMixin, SimpleTestCase):
    def test_forecast_daily_output_at_max_efficiency(self):
        # (40 * 480 * 0.60) / 35 = 11520/35 = 329.142857...
        result = calc.forecast_daily_output(
            self.MANPOWER, self.MINUTES_PER_OPERATOR, self.SAM, self.MAX_EFFICIENCY
        )
        self.assertAlmostEqual(float(result), 329.142857, places=4)

    def test_sam_zero_raises(self):
        with self.assertRaises(ValueError):
            calc.forecast_daily_output(self.MANPOWER, self.MINUTES_PER_OPERATOR, 0, self.MAX_EFFICIENCY)


class ExpectedDaysRemainingTests(SampleScenarioMixin, SimpleTestCase):
    def test_days_remaining_for_full_order_at_max_efficiency(self):
        remaining = calc.remaining_quantity(self.ORDER_QTY, 0)  # 100000
        daily_output = calc.forecast_daily_output(
            self.MANPOWER, self.MINUTES_PER_OPERATOR, self.SAM, self.MAX_EFFICIENCY
        )  # 329.142857...
        result = calc.expected_days_remaining(remaining, daily_output)
        # 100000 / 329.142857 = 303.8... -> rounds up to 304
        self.assertEqual(result, 304)

    def test_zero_remaining_gives_zero_days(self):
        result = calc.expected_days_remaining(0, Decimal("329.14"))
        self.assertEqual(result, 0)

    def test_zero_forecast_output_with_remaining_quantity_raises(self):
        with self.assertRaises(ValueError):
            calc.expected_days_remaining(1000, 0)


class ExpectedCompletionDateTests(SampleScenarioMixin, SimpleTestCase):
    def test_completion_date_adds_days_remaining(self):
        start = date(2026, 1, 1)
        result = calc.expected_completion_date(start, 304)
        self.assertEqual(result, date(2026, 11, 1))

    def test_zero_days_remaining_returns_start_date(self):
        start = date(2026, 1, 1)
        result = calc.expected_completion_date(start, 0)
        self.assertEqual(result, start)

    def test_negative_days_remaining_raises(self):
        with self.assertRaises(ValueError):
            calc.expected_completion_date(date(2026, 1, 1), -1)


class BuildEfficiencyCurveTests(SampleScenarioMixin, SimpleTestCase):
    def test_curve_matches_manual_ramp_up(self):
        curve = calc.build_efficiency_curve(
            self.START_EFFICIENCY, self.DAILY_GROWTH, self.MAX_EFFICIENCY, number_of_days=10
        )
        expected = [
            Decimal("0.25"), Decimal("0.30"), Decimal("0.35"), Decimal("0.40"),
            Decimal("0.45"), Decimal("0.50"), Decimal("0.55"), Decimal("0.60"),
            Decimal("0.60"), Decimal("0.60"),
        ]
        self.assertEqual(curve, expected)


class EndToEndScenarioTests(SampleScenarioMixin, SimpleTestCase):
    """
    Full pipeline test chaining every function together for day 1 of the
    sample scenario, verifying the functions compose correctly end-to-end.
    """

    def test_full_pipeline_day_1(self):
        day = 1
        eff = calc.planned_efficiency(self.START_EFFICIENCY, self.DAILY_GROWTH, day, self.MAX_EFFICIENCY)
        avail = calc.available_minutes(self.MANPOWER, self.MINUTES_PER_OPERATOR)
        ppm = calc.planned_productive_minutes(avail, eff)
        tgt = calc.target_output(ppm, self.SAM)

        self.assertEqual(eff, Decimal("0.25"))
        self.assertEqual(avail, Decimal("19200"))
        self.assertEqual(ppm, Decimal("4800.00"))
        self.assertAlmostEqual(float(tgt), 137.142857, places=4)

        # Now simulate an actual day's output of 150 pieces produced.
        actual_output = 150
        produced = calc.produced_minutes(actual_output, self.SAM)
        actual_eff = calc.actual_efficiency(produced, avail)
        gain_loss = calc.gain_loss_minutes(produced, ppm)

        self.assertEqual(produced, Decimal("5250"))
        self.assertAlmostEqual(float(actual_eff), 0.273438, places=4)
        self.assertEqual(gain_loss, Decimal("450.00"))
