from __future__ import annotations

from datetime import datetime

import pandas as pd
from django.http import HttpResponse

from .models import InspectionDefect, QualityInspection


def get_quality_summary_dataframe(inspections):
    rows = []
    for inspection in inspections.select_related("production_line", "production_order", "article", "factory"):
        rows.append({
            "Inspection Number": inspection.inspection_number,
            "Date": inspection.inspection_date,
            "Factory": inspection.factory.name,
            "Line": inspection.production_line.line_name,
            "Order": inspection.production_order.order_number,
            "Article": inspection.article.article_code,
            "Buyer": inspection.buyer,
            "Inspection Type": inspection.inspection_type,
            "Inspected Qty": inspection.inspected_quantity,
            "Passed Qty": inspection.passed_quantity,
            "Rejected Qty": inspection.rejected_quantity,
            "Rework Qty": inspection.rework_quantity,
            "DHU %": inspection.dhu,
            "FPY %": inspection.fpy,
            "Defect Rate %": inspection.defect_rate,
        })
    return pd.DataFrame(rows)


def build_quality_excel_response(inspections, filename_prefix="quality_report"):
    df = get_quality_summary_dataframe(inspections)
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    filename = f"{filename_prefix}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    response["Content-Disposition"] = f"attachment; filename={filename}"
    with pd.ExcelWriter(response, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Summary")
        worksheet = writer.sheets["Summary"]
        worksheet.freeze_panes = "A2"
        for i, column in enumerate(df.columns, start=1):
            max_len = max(df[column].astype(str).map(len).max() if not df.empty else 0, len(column))
            worksheet.column_dimensions[chr(64 + i)].width = max_len + 2
    return response


def quality_defect_summary_query(inspections):
    defect_rows = []
    for defect in InspectionDefect.objects.filter(inspection__in=inspections).select_related("inspection", "defect"):
        defect_rows.append({
            "Inspection Number": defect.inspection.inspection_number,
            "Defect Code": defect.defect.defect_code,
            "Defect Name": defect.defect.defect_name,
            "Category": defect.defect.category,
            "Severity": defect.severity,
            "Defect Qty": defect.defect_quantity,
            "Affected Pieces": defect.affected_piece_quantity,
        })
    return pd.DataFrame(defect_rows)
