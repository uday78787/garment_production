"""
reports/views.py

- ProductionReportView: HTML report page with DataTables (search/filter),
  scoped by role.
- export_excel: streams an .xlsx built with pandas + openpyxl.
- export_pdf: renders an HTML template to PDF via WeasyPrint (falls back to
  ReportLab-friendly plain response if WeasyPrint isn't installed on the
  host — see requirements.txt notes).
"""

from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.generic import TemplateView

from master_data.models import Factory
from production.models import DailyProductionEntry


def _scoped_entries(user, selected_factory_id=None):
    qs = DailyProductionEntry.objects.select_related(
        "assignment__production_line", "assignment__production_order__article"
    )
    if user.is_superuser:
        scoped_qs = qs
    elif not hasattr(user, "profile"):
        scoped_qs = qs.none()
    elif user.profile.is_admin:
        scoped_qs = qs.filter(
            assignment__production_line__factory__in=user.profile.accessible_factories.all()
        )
    else:
        scoped_qs = qs.filter(assignment__production_line__in=user.profile.assigned_lines.all())
        if user.profile.factory_id:
            scoped_qs = scoped_qs.filter(
                assignment__production_line__factory_id=user.profile.factory_id
            )
    if selected_factory_id:
        scoped_qs = scoped_qs.filter(
            assignment__production_line__factory_id=selected_factory_id
        )
    return scoped_qs


def _filtered_entries(request):
    """Return report entries scoped to the user and optional inclusive date range."""
    selected_factory_id = request.session.get("selected_factory_id")
    entries = _scoped_entries(request.user, selected_factory_id)
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    try:
        if date_from:
            entries = entries.filter(production_date__gte=date.fromisoformat(date_from))
        if date_to:
            entries = entries.filter(production_date__lte=date.fromisoformat(date_to))
    except ValueError:
        pass
    return entries.order_by("-production_date", "assignment__production_line__line_name", "pk")


def _report_location_name(request, entries):
    selected_factory_id = request.session.get("selected_factory_id")
    if selected_factory_id:
        name = Factory.objects.filter(pk=selected_factory_id).values_list("name", flat=True).first()
        if name:
            return name
    return entries.values_list("assignment__production_line__factory__name", flat=True).first() or "All Locations"


def _report_rows(entries):
    """Build one consistent row shape for the HTML, Excel, and PDF reports."""
    rows = []
    for entry in entries:
        assignment = entry.assignment
        rows.append(
            {
                "Date": entry.production_date,
                "Line": assignment.production_line.line_name,
                "Order": assignment.production_order.order_number,
                "Style": assignment.production_order.article.article_code,
                "SAM": float(entry.sam),
                "Manpower": entry.actual_manpower,
                "Working Minutes": entry.working_minutes,
                "Available Minutes": float(entry.available_minutes),
                "Actual Output": entry.actual_output,
                "Target Output": float(entry.target_output),
                "Planned Efficiency %": float(entry.planned_efficiency) * 100,
                "Actual Efficiency %": float(entry.actual_efficiency) * 100,
                "Forecast Daily Output": float(assignment.forecast_daily_output()),
                "Expected Completion": assignment.expected_completion_date(),
                "Gain/Loss Minutes": float(entry.gain_loss_minutes),
            }
        )
    return rows


class ProductionReportView(LoginRequiredMixin, TemplateView):
    template_name = "reports/production_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["entries"] = _filtered_entries(self.request)
        context["selected_date_from"] = self.request.GET.get("date_from") or ""
        context["selected_date_to"] = self.request.GET.get("date_to") or ""
        context["report_export_query"] = self.request.GET.urlencode()
        return context


@login_required
def export_excel(request):
    """Export the scoped daily production entries to an .xlsx file."""
    import pandas as pd

    entries = _filtered_entries(request)
    rows = _report_rows(entries)
    df = pd.DataFrame(rows)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"production_report_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    response["Content-Disposition"] = f"attachment; filename={filename}"

    with pd.ExcelWriter(response, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Production Report")
        worksheet = writer.sheets["Production Report"]
        for i, column in enumerate(df.columns, start=1):
            max_len = max(df[column].astype(str).map(len).max() if not df.empty else 0, len(column))
            worksheet.column_dimensions[chr(64 + i)].width = max_len + 2

    return response


@login_required
def export_pdf(request):
    """Export the scoped report as PDF, with a Windows-safe ReportLab fallback."""
    entries = _filtered_entries(request)
    location_name = _report_location_name(request, entries)
    html_string = render_to_string(
        "reports/production_report_pdf.html",
        {
            "entries": entries,
            "generated_at": datetime.now(),
            "location_name": location_name,
            "logo_uri": (Path(settings.BASE_DIR) / "static" / "images" / "brand.png").as_uri(),
        },
    )

    try:
        from weasyprint import HTML  # imported lazily; optional dependency

        pdf_bytes = HTML(string=html_string).write_pdf()
    except Exception:
        pdf_bytes = _reportlab_pdf(entries, location_name)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"production_report_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    response["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def _reportlab_pdf(entries, location_name):
    """Create a simple downloadable PDF when WeasyPrint's native libraries are unavailable."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=0.3 * inch, rightMargin=0.3 * inch)
    styles = getSampleStyleSheet()
    data = [[
        "Date", "Line", "Order", "Style", "SAM", "Manpower", "Work Min", "Avail Min", "Actual", "Target",
        "Plan Eff %", "Actual Eff %", "Forecast/Day", "Completion", "Gain/Loss",
    ]]
    for row in _report_rows(entries):
        data.append([
            row["Date"], row["Line"], row["Order"], row["Style"], f'{row["SAM"]:.2f}',
            row["Manpower"], row["Working Minutes"], f'{row["Available Minutes"]:.2f}',
            row["Actual Output"], f'{row["Target Output"]:.0f}',
            f'{row["Planned Efficiency %"]:.2f}', f'{row["Actual Efficiency %"]:.2f}',
            f'{row["Forecast Daily Output"]:.0f}', row["Expected Completion"],
            f'{row["Gain/Loss Minutes"]:.2f}',
        ])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    logo = Image(str(Path(settings.BASE_DIR) / "static" / "images" / "brand.png"), width=0.55 * inch, height=0.55 * inch)
    header = Table([[logo, Paragraph("<b>ProdVanta</b><br/>Production Report<br/><font size=9>Location: %s</font>" % location_name, styles["Normal"])]], colWidths=[0.7 * inch, 9.8 * inch])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    document.build([header, Spacer(1, 8), table])
    return buffer.getvalue()
