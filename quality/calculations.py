from __future__ import annotations


def safe_divide(numerator, denominator):
    if denominator in (None, 0):
        return 0
    return numerator / denominator


def calculate_dhu(total_defects, inspected_quantity):
    return safe_divide(total_defects * 100, inspected_quantity)


def calculate_defect_rate(defective_pieces, inspected_quantity):
    return safe_divide(defective_pieces * 100, inspected_quantity)


def calculate_fpy(passed_quantity, inspected_quantity):
    return safe_divide(passed_quantity * 100, inspected_quantity)


def calculate_rework_percentage(rework_quantity, inspected_quantity):
    return safe_divide(rework_quantity * 100, inspected_quantity)


def calculate_rejection_percentage(rejected_quantity, inspected_quantity):
    return safe_divide(rejected_quantity * 100, inspected_quantity)


def format_percentage(value):
    if value is None:
        return 0
    return round(float(value), 2)
