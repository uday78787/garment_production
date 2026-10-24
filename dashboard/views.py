"""
dashboard/views.py

Two dashboards:
- AdminDashboardView: factory-wide KPIs, all lines, all orders.
- UserDashboardView: KPIs scoped to the logged-in Production User's
  assigned lines only.

Both feed Chart.js via a small JSON context (labels/series), rendered by
the templates.
"""

import json
import secrets
from datetime import timedelta
from decimal import Decimal
from datetime import datetime, time
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Min, Sum
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.mixins import AdminRequiredMixin, admin_required
from master_data.models import Article, Factory, FactoryShift, LineShiftManpower, ProductionLine, ProductionLineShiftAssignment
from planning.models import LineAssignment, ProductionOrder
from planning.services.calculations import forecast_daily_output
from planning.services import calculations as calc
from production.models import DailyProductionEntry
from quality.models import DefectMaster, HourlyLineCheck, HourlyLineCheckDetail, HourlyLineSummary


def _schedule_working_minutes(shift, hour_number):
    """Return the working minutes represented by one QA check hour."""
    for item in shift.hourly_schedule or []:
        if str(item.get("hour")) != str(hour_number):
            continue
        try:
            start = time.fromisoformat(str(item.get("start")))
            end = time.fromisoformat(str(item.get("end")))
            start_minutes = start.hour * 60 + start.minute
            end_minutes = end.hour * 60 + end.minute
            if end_minutes <= start_minutes:
                end_minutes += 24 * 60
            minutes = end_minutes - start_minutes
            break_start = item.get("break_start")
            break_end = item.get("break_end")
            if break_start and break_end:
                pause_start = time.fromisoformat(str(break_start))
                pause_end = time.fromisoformat(str(break_end))
                pause_minutes = (pause_end.hour * 60 + pause_end.minute) - (pause_start.hour * 60 + pause_start.minute)
                minutes -= max(pause_minutes, 0)
            return max(minutes, 0)
        except (TypeError, ValueError):
            break
    return 60


def _daily_detail_rows(request):
    """Return dashboard detail rows and summary metrics for the current filters."""
    dashboard = AdminDashboardView()
    dashboard.request = request
    entries = dashboard.get_filtered_entries()
    style_id = request.GET.get("style")
    if style_id and style_id.isdigit():
        entries = entries.filter(assignment__production_order__article_id=style_id)
    grouped = entries.values(
        "production_date",
        "assignment__production_line__line_name",
        "assignment__production_order__article__article_code",
    ).annotate(
        total_available_minutes=Sum("available_minutes"),
        total_produced_minutes=Sum("produced_minutes"),
        target_output=Sum("target_output"),
        actual_output=Sum("actual_output"),
        manpower=Sum("actual_manpower"),
        planned_productive_minutes=Sum(
            ExpressionWrapper(
                F("available_minutes") * F("planned_efficiency"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        ),
        gain_loss_minutes=Sum("gain_loss_minutes"),
    ).order_by("-production_date", "assignment__production_line__line_name")
    rows = []
    for row in grouped:
        available = Decimal(row["total_available_minutes"] or 0)
        produced = Decimal(row["total_produced_minutes"] or 0)
        daily_efficiency = produced / available * 100 if available else Decimal("0")
        actual_efficiency = produced / available * 100 if available else Decimal("0")
        planned_minutes = Decimal(row["planned_productive_minutes"] or 0)
        planned_efficiency = planned_minutes / available * 100 if available else Decimal("0")
        rows.append({
            "Date": row["production_date"],
            "Line": row["assignment__production_line__line_name"],
            "Article": row["assignment__production_order__article__article_code"],
            "Manpower": row["manpower"] or 0,
            "Available Minutes": available,
            "Produced Minutes": produced,
            "Daily Eff %": daily_efficiency,
            "Avg Eff %": actual_efficiency,
            "Target": row["target_output"] or 0,
            "Actual Production": row["actual_output"] or 0,
            "Target vs Actual": (row["actual_output"] or 0) - (row["target_output"] or 0),
            "Gain/Loss Minutes": row["gain_loss_minutes"] or 0,
            "Gain/Loss Eff %": daily_efficiency - planned_efficiency,
        })
    dates = {row["Date"] for row in rows}
    average = lambda key: sum((Decimal(str(row[key])) for row in rows), Decimal("0")) / len(rows) if rows else Decimal("0")
    aggregate = entries.aggregate(
        total_production=Sum("actual_output"),
        average_manpower=Avg("actual_manpower"),
        total_available_minutes=Sum("available_minutes"),
        total_produced_minutes=Sum("produced_minutes"),
    )
    summary = {
        "Total Days": len(dates),
        "Avg. Eff %": (
            Decimal(aggregate["total_produced_minutes"] or 0)
            / Decimal(aggregate["total_available_minutes"] or 1)
            * 100
            if aggregate["total_available_minutes"]
            else Decimal("0")
        ),
        "Total Production": aggregate["total_production"] or 0,
        "Avg. Manpower": Decimal(aggregate["average_manpower"] or 0),
        "Total Available Minutes": aggregate["total_available_minutes"] or 0,
        "Total Produced Minutes": aggregate["total_produced_minutes"] or 0,
        "Average Gain/Loss Eff %": average("Gain/Loss Eff %"),
        "Total Target": sum((row["Target"] for row in rows), Decimal("0")),
    }
    return rows, summary


def _build_line_output_chart(rows):
    output_by_line = {}
    dates = set()
    for row in rows:
        day = row["date"]
        line_name = row["line"]
        dates.add(day)
        line_outputs = output_by_line.setdefault(line_name, {})
        line_outputs[day] = line_outputs.get(day, Decimal("0")) + Decimal(row["output"] or 0)

    sorted_dates = sorted(dates)
    labels = [day.strftime("%d-%b") for day in sorted_dates]
    palette = ["#0d6efd", "#198754", "#dc3545", "#fd7e14", "#6f42c1", "#0dcaf0"]
    datasets = []
    for index, (line_name, line_outputs) in enumerate(sorted(output_by_line.items())):
        color = palette[index % len(palette)]
        datasets.append({
            "label": line_name,
            "data": [
                float(line_outputs[day]) if day in line_outputs else None
                for day in sorted_dates
            ],
            "borderColor": color,
            "backgroundColor": color,
            "tension": 0.25,
            "spanGaps": False,
        })
    return json.dumps(labels), json.dumps(datasets)


@admin_required
def export_daily_detail_excel(request):
    """Download the filtered Admin daily detail with average-efficiency summary."""
    import pandas as pd

    rows, summary = _daily_detail_rows(request)
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f"attachment; filename=daily_production_detail_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(response, engine="openpyxl") as writer:
        pd.DataFrame([summary]).to_excel(writer, index=False, sheet_name="Summary")
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Daily Details")
    return response


@admin_required
def export_daily_detail_pdf(request):
    """Download the filtered Admin daily detail as a PDF."""
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    rows, summary = _daily_detail_rows(request)
    selected_factory_id = request.session.get("selected_factory_id")
    location_name = Factory.objects.filter(
        pk=selected_factory_id
    ).values_list("name", flat=True).first() if selected_factory_id else "All Locations"
    location_name = location_name or "All Locations"
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=0.25 * inch, rightMargin=0.25 * inch)
    data = [["Date", "Line", "Article", "MP", "Avail Min", "Produced Min", "Daily Eff %", "Avg Eff %", "Target", "Actual", "Diff", "Gain/Loss Min", "Gain/Loss Eff %"]]
    for row in rows:
        data.append([row["Date"], row["Line"], row["Article"], row["Manpower"], f'{row["Available Minutes"]:.2f}', f'{row["Produced Minutes"]:.2f}', f'{row["Daily Eff %"]:.2f}', f'{row["Avg Eff %"]:.2f}', f'{row["Target"]:.0f}', f'{row["Actual Production"]:.0f}', f'{row["Target vs Actual"]:.0f}', f'{row["Gain/Loss Minutes"]:.2f}', f'{row["Gain/Loss Eff %"]:.2f}'])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 6)]))
    styles = getSampleStyleSheet()
    logo = Image(str(Path(settings.BASE_DIR) / "static" / "images" / "brand.png"), width=0.55 * inch, height=0.55 * inch)
    header = Table([[logo, Paragraph("<b>ProdVanta</b><br/>Daily Production Detail<br/><font size=9>Location: %s</font>" % location_name, styles["Normal"])]], colWidths=[0.7 * inch, 9.8 * inch])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    document.build([
        header,
        Paragraph(
            f"Total days: {summary['Total Days']} | Avg. Eff %: {summary['Avg. Eff %']:.2f}% | "
            f"Total Production: {summary['Total Production']:.0f} | Avg. Manpower: {summary['Avg. Manpower']:.2f} | "
            f"Total Available Min: {summary['Total Available Minutes']:.2f} | "
            f"Total Produced Min: {summary['Total Produced Minutes']:.2f}",
            styles["Normal"],
        ),
        Spacer(1, 8), table,
    ])
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename=daily_production_detail_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    return response


class BaseDashboardView(LoginRequiredMixin, TemplateView):
    days_back = 14

    def get_scoped_entries(self):
        raise NotImplementedError

    def get_auto_selected_factory_id(self):
        """Choose a valid default factory when the session has no selection."""
        selected_factory_id = self.request.session.get("selected_factory_id")
        if self.request.user.is_superuser:
            allowed_factories = Factory.objects.filter(active_status=True)
        elif hasattr(self.request.user, "profile"):
            allowed_factories = self.request.user.profile.accessible_factories.filter(
                active_status=True
            )
        else:
            allowed_factories = Factory.objects.none()

        if selected_factory_id and allowed_factories.filter(pk=selected_factory_id).exists():
            return selected_factory_id

        factory_id = allowed_factories.order_by("name").values_list("pk", flat=True).first()
        if factory_id:
            self.request.session["selected_factory_id"] = factory_id
        return factory_id

    def get_qa_summary(self, selected_factory_id=None, date_from=None, date_to=None):
        qs = HourlyLineCheck.objects.select_related("factory", "production_line", "article")
        if selected_factory_id:
            qs = qs.filter(factory_id=selected_factory_id)
        if date_from:
            qs = qs.filter(check_date__gte=date_from)
        if date_to:
            qs = qs.filter(check_date__lte=date_to)

        total_inspected = qs.aggregate(total=Sum("inspected_quantity"))["total"] or 0
        total_passed = qs.aggregate(total=Sum("passed_quantity"))["total"] or 0
        total_defects = HourlyLineCheckDetail.objects.filter(
            hourly_check__in=qs
        ).aggregate(total=Sum("defect_quantity"))["total"] or 0
        defect_breakdown = (
            HourlyLineCheckDetail.objects.filter(hourly_check__in=qs)
            .values("defect__defect_code", "defect__defect_name")
            .annotate(total_defects=Sum("defect_quantity"))
            .order_by("-total_defects", "defect__defect_code")
        )
        daily = (
            qs.values("check_date", "shift_name")
            .annotate(
                total_inspected=Sum("inspected_quantity"),
                total_passed=Sum("passed_quantity"),
            )
            .order_by("check_date", "shift_name")
        )
        marked_defects_by_date = {
            (row["hourly_check__check_date"], row["hourly_check__shift_name"]): row["total"] or 0
            for row in HourlyLineCheckDetail.objects.filter(
                hourly_check__in=qs
            ).values("hourly_check__check_date", "hourly_check__shift_name").annotate(total=Sum("defect_quantity"))
        }
        daily = [
            {**row, "total_defects": marked_defects_by_date.get((row["check_date"], row["shift_name"]), 0)}
            for row in daily
        ]
        return {
            "total_inspected": int(total_inspected),
            "total_passed": int(total_passed),
            "total_defects": int(total_defects),
            "defect_breakdown": list(defect_breakdown),
            "daily": list(daily),
        }

    def get_qa_reporting_data(self, selected_factory_id=None, date_from=None, date_to=None, selected_style_id=None):
        checks = HourlyLineCheck.objects.filter(
            factory_id=selected_factory_id,
            check_date__gte=date_from or timezone.localdate() - timedelta(days=self.days_back),
        ).select_related("production_line", "production_order__article", "article")
        if date_to:
            checks = checks.filter(check_date__lte=date_to)
        if selected_style_id and selected_style_id.isdigit():
            checks = checks.filter(article_id=selected_style_id)

        line_rows = {}
        daily_rows = {}
        forecast_rows = {}
        shifts = {
            shift.shift_name: shift
            for shift in FactoryShift.objects.filter(factory_id=selected_factory_id, active=True)
        }
        manpower_rows = LineShiftManpower.objects.filter(
            factory_id=selected_factory_id,
            entry_date__gte=date_from or timezone.localdate() - timedelta(days=self.days_back),
        ).select_related("shift")
        if date_to:
            manpower_rows = manpower_rows.filter(entry_date__lte=date_to)
        manpower_lookup = {
            (row.production_line_id, row.entry_date, row.shift.shift_name): row
            for row in manpower_rows
        }
        counted_manpower = {}
        counted_targets = set()

        for check in checks:
            assignment = LineAssignment.objects.filter(
                production_line=check.production_line,
                production_order=check.production_order,
            ).first() or LineAssignment.objects.filter(
                production_order=check.production_order,
            ).first()
            shift = shifts.get(check.shift_name)
            manpower = manpower_lookup.get((check.production_line_id, check.check_date, check.shift_name))
            working_minutes = _schedule_working_minutes(shift, check.check_hour) if shift else 60
            actual_output = Decimal(check.inspected_quantity or 0)
            produced_minutes = actual_output * check.article.sam
            if assignment:
                day_number = max((check.check_date - assignment.start_date).days + 1, 1)
                planned_efficiency = calc.planned_efficiency(
                    assignment.start_efficiency,
                    assignment.daily_efficiency_growth,
                    day_number,
                    assignment.max_efficiency,
                )
                available_minutes = Decimal(manpower.manpower if manpower else 0) * Decimal(working_minutes)
                target_output = calc.target_output(
                    calc.planned_productive_minutes(available_minutes, planned_efficiency),
                    check.article.sam,
                ) if available_minutes else Decimal("0")
            else:
                planned_efficiency = Decimal("0")
                available_minutes = Decimal(manpower.manpower if manpower else 0) * Decimal(working_minutes)
                target_output = Decimal("0")

            target_key = (check.production_line_id, check.check_date, check.shift_name or "Unassigned")
            line_key = check.production_line_id
            line_row = line_rows.setdefault(line_key, {
                "assignment__production_line__line_name": check.production_line.line_name,
                "planned_output": Decimal("0"),
                "actual_output": Decimal("0"),
            })
            if target_key not in counted_targets:
                line_row["planned_output"] += target_output
                counted_targets.add(target_key)
            line_row["actual_output"] += actual_output

            daily_key = (check.check_date, check.production_line_id, check.article_id, check.shift_name)
            daily_row = daily_rows.setdefault(daily_key, {
                "date": check.check_date,
                "line": check.production_line.line_name,
                "line_id": check.production_line_id,
                "article": check.article.article_code,
                "article_id": check.article_id,
                "shift": check.shift_name or "Unassigned",
                "order_id": check.production_order_id,
                "order_number": check.production_order.order_number,
                "sam": check.article.sam,
                "manpower": 0,
                "available_minutes": Decimal("0"),
                "produced_minutes": Decimal("0"),
                "planned_productive_minutes": Decimal("0"),
                "actual_output": Decimal("0"),
                "target_output": Decimal("0"),
                "count": 0,
                "defect_pieces": 0,
            })
            manpower_key = (check.production_line_id, check.check_date, check.shift_name)
            counted_shifts = counted_manpower.setdefault(daily_key, set())
            if manpower and manpower_key not in counted_shifts:
                daily_row["manpower"] += manpower.manpower
                daily_row["available_minutes"] += Decimal(manpower.manpower) * Decimal(manpower.working_minutes)
                daily_row["planned_productive_minutes"] += (
                    Decimal(manpower.manpower)
                    * Decimal(manpower.working_minutes)
                    * planned_efficiency
                )
                counted_shifts.add(manpower_key)
            daily_row["produced_minutes"] += produced_minutes
            daily_row["actual_output"] += actual_output
            daily_row["count"] += 1
            daily_row["defect_pieces"] += check.details.aggregate(
                total=Sum("defect_quantity")
            )["total"] or 0

            forecast_key = (check.production_line_id, check.production_order_id, check.article_id)
            forecast_row = forecast_rows.setdefault(forecast_key, {
                "line_name": check.production_line.line_name,
                "order_number": check.production_order.order_number,
                "article_code": check.article.article_code,
                "planned_output": Decimal("0"),
                "actual_output": Decimal("0"),
                "forecast_daily_output": Decimal("0"),
                "expected_days_remaining": None,
            })
            forecast_target_key = (check.production_line_id, check.production_order_id, check.article_id, check.check_date, check.shift_name or "Unassigned")
            if forecast_target_key not in counted_targets:
                forecast_row["planned_output"] += target_output
                counted_targets.add(forecast_target_key)
            forecast_row["actual_output"] += actual_output

        line_rows = {}
        forecast_rows = {}
        for row in daily_rows.values():
            row["target_output"] = calc.target_output(
                row["planned_productive_minutes"], row["sam"]
            ) if row["available_minutes"] else Decimal("0")
            line_row = line_rows.setdefault(row["line_id"], {
                "assignment__production_line__line_name": row["line"],
                "planned_output": Decimal("0"),
                "actual_output": Decimal("0"),
            })
            line_row["planned_output"] += row["target_output"]
            line_row["actual_output"] += row["actual_output"]

            forecast_key = (row["line_id"], row["order_id"], row["article_id"])
            forecast_row = forecast_rows.setdefault(forecast_key, {
                "line_name": row["line"],
                "order_number": row["order_number"],
                "article_code": row["article"],
                "planned_output": Decimal("0"),
                "actual_output": Decimal("0"),
                "forecast_daily_output": Decimal("0"),
                "expected_days_remaining": None,
            })
            forecast_row["planned_output"] += row["target_output"]
            forecast_row["actual_output"] += row["actual_output"]

        for row in line_rows.values():
            row["variance"] = row["actual_output"] - row["planned_output"]
        detail_rows = []
        for row in daily_rows.values():
            available = row["available_minutes"]
            detail_rows.append({
                "date": row["date"],
                "line": row["line"],
                "shift": row["shift"],
                "article": row["article"],
                "manpower": row["manpower"],
                "available_minutes": available,
                "produced_minutes": row["produced_minutes"],
                "planned_efficiency": row["planned_productive_minutes"] / available * 100 if available else Decimal("0"),
                "actual_efficiency": row["produced_minutes"] / available * 100 if available else Decimal("0"),
                "daily_efficiency": row["produced_minutes"] / available * 100 if available else Decimal("0"),
                "target_output": row["target_output"],
                "actual_output": row["actual_output"],
                "defect_pieces": row["defect_pieces"],
                "dhu": row["defect_pieces"] / row["actual_output"] * 100 if row["actual_output"] else Decimal("0"),
                "difference": row["actual_output"] - row["target_output"],
                "gain_loss_minutes": row["produced_minutes"] - row["planned_productive_minutes"],
            })
        return list(line_rows.values()), detail_rows, list(forecast_rows.values())

    def get_filtered_entries(self):
        entries = self.get_scoped_entries()

        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if not date_from and not date_to:
            today = timezone.localdate()
            date_from = today.isoformat()
            date_to = today.isoformat()
        if date_from:
            entries = entries.filter(production_date__gte=date_from)
        if date_to:
            entries = entries.filter(production_date__lte=date_to)

        selected_factory_id = self.get_auto_selected_factory_id()
        if selected_factory_id:
            entries = entries.filter(assignment__production_line__factory_id=selected_factory_id)
        return entries

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = self.get_filtered_entries()
        selected_factory_id = self.get_auto_selected_factory_id()
        date_from = self.request.GET.get("date_from") or timezone.localdate().isoformat()
        date_to = self.request.GET.get("date_to") or timezone.localdate().isoformat()
        current_factory = Factory.objects.filter(pk=selected_factory_id).first() if selected_factory_id else None
        current_shift = FactoryShift.get_active_shift_for_factory(current_factory) if current_factory else None
        schedule_status = current_shift.current_schedule_status() if current_shift else None
        context["current_factory_name"] = current_factory.name if current_factory else "No factory selected"
        context["current_shift"] = current_shift.shift_name if current_shift else "Not assigned"
        context["current_hour_label"] = schedule_status["label"] if schedule_status else "Not assigned"
        context["current_hour_window"] = schedule_status["window"] if schedule_status else "Not assigned"
        selected_style_id = self.request.GET.get("style")
        if selected_style_id and selected_style_id.isdigit():
            entries = entries.filter(assignment__production_order__article_id=selected_style_id)
        entries = entries.order_by("production_date")
        context["selected_date_from"] = date_from or ""
        context["selected_date_to"] = date_to or ""
        context["daily_detail_export_query"] = self.request.GET.urlencode()

        selected_line_summary_line = self.request.GET.get("line_summary_line")
        selected_line_summary_date = self.request.GET.get("line_summary_date") or date_from or timezone.localdate().isoformat()
        context["selected_line_summary_line"] = int(selected_line_summary_line) if selected_line_summary_line and selected_line_summary_line.isdigit() else None
        context["selected_line_summary_date"] = selected_line_summary_date
        if selected_factory_id:
            factory_lines = ProductionLine.objects.filter(factory_id=selected_factory_id, active_status=True)
            if not self.request.user.is_superuser and hasattr(self.request.user, "profile"):
                profile = self.request.user.profile
                if getattr(profile, "is_admin", False):
                    factory_lines = factory_lines.filter(factory_id__in=profile.accessible_factories.values_list("pk", flat=True))
                else:
                    factory_lines = factory_lines.filter(pk__in=profile.assigned_lines.values_list("pk", flat=True))
            context["line_summary_lines"] = factory_lines.distinct().order_by("line_name")
        else:
            context["line_summary_lines"] = ProductionLine.objects.none()

        line_summary_qs = HourlyLineCheck.objects.filter(factory_id=selected_factory_id).select_related("production_line", "article", "production_order")
        if selected_line_summary_date:
            line_summary_qs = line_summary_qs.filter(check_date=selected_line_summary_date)
        if context["selected_line_summary_line"]:
            line_summary_qs = line_summary_qs.filter(production_line_id=context["selected_line_summary_line"])

        line_summary_by_line = {}
        counted_line_targets = set()
        for check in line_summary_qs.order_by("production_line__line_name", "check_hour"):
            manpower_entry = LineShiftManpower.objects.filter(
                factory_id=selected_factory_id,
                production_line=check.production_line,
                entry_date=check.check_date,
                shift__shift_name=check.shift_name,
            ).select_related("shift").order_by("-id").first()
            manpower = manpower_entry.manpower if manpower_entry else 0
            working_minutes = manpower_entry.working_minutes if manpower_entry else 60
            available_minutes = Decimal(manpower) * Decimal(working_minutes)
            defect_total = check.details.aggregate(total=Sum("defect_quantity"))["total"] or 0
            produced_minutes = Decimal(check.inspected_quantity or 0) * Decimal(check.article.sam)
            assignment = LineAssignment.objects.filter(
                production_line=check.production_line,
                production_order=check.production_order,
            ).first() or LineAssignment.objects.filter(
                production_order=check.production_order,
            ).first()
            planned_efficiency = Decimal("0")
            target_output = Decimal("0")
            if assignment:
                day_number = max((check.check_date - assignment.start_date).days + 1, 1)
                planned_efficiency = calc.planned_efficiency(
                    assignment.start_efficiency,
                    assignment.daily_efficiency_growth,
                    day_number,
                    assignment.max_efficiency,
                )
                target_output = calc.target_output(
                    calc.planned_productive_minutes(available_minutes, planned_efficiency),
                    check.article.sam,
                ) if available_minutes else Decimal("0")

            line = line_summary_by_line.setdefault(
                check.production_line_id,
                {
                    "summary_date": check.check_date,
                    "production_line": check.production_line,
                    "production_line_id": check.production_line_id,
                    "production_line__line_name": check.production_line.line_name,
                    "shift_name": check.shift_name or "Unassigned",
                    "target_output": Decimal("0"),
                    "actual_output": 0,
                    "defect_quantity": 0,
                    "dhu": Decimal("0"),
                    "hourly_efficiency": Decimal("0"),
                    "manpower": 0,
                    "available_minutes": Decimal("0"),
                    "produced_minutes": Decimal("0"),
                    "hourly_rows": [],
                },
            )
            hourly_planned_output = Decimal("0")
            if manpower_entry:
                hourly_planned_output = calc.hourly_target(
                    manpower_entry.manpower,
                    check.article.sam,
                    planned_efficiency,
                )
            hour_payload = {
                "check_hour": check.check_hour,
                "shift_name": check.shift_name or "Unassigned",
                "target_output": hourly_planned_output,
                "actual_output": check.inspected_quantity,
                "defect_quantity": defect_total,
                "dhu": (Decimal(defect_total) / Decimal(check.inspected_quantity or 0) * Decimal("100")) if check.inspected_quantity else Decimal("0"),
                "hourly_efficiency": (Decimal(check.inspected_quantity or 0) / hourly_planned_output * Decimal("100")) if hourly_planned_output else Decimal("0"),
                "manpower": manpower,
                "available_minutes": Decimal(manpower) * Decimal("60") if manpower else Decimal("0"),
                "produced_minutes": produced_minutes,
            }
            line["hourly_rows"].append(hour_payload)
            target_key = (check.production_line_id, check.check_date, check.shift_name or "Unassigned")
            if target_key not in counted_line_targets:
                line["target_output"] += target_output
                counted_line_targets.add(target_key)
            line["actual_output"] += check.inspected_quantity
            line["defect_quantity"] += defect_total
            if line["actual_output"]:
                line["dhu"] = (Decimal(line["defect_quantity"]) / Decimal(line["actual_output"]) * Decimal("100"))

            if manpower_entry:
                line["manpower"] = manpower_entry.manpower
                line["available_minutes"] = Decimal(manpower_entry.manpower) * Decimal(manpower_entry.working_minutes)
                line["produced_minutes"] = sum(
                    Decimal(hour["produced_minutes"]) for hour in line["hourly_rows"]
                )
                if line["available_minutes"]:
                    line["hourly_efficiency"] = (line["produced_minutes"] / line["available_minutes"] * Decimal("100"))
            else:
                line["manpower"] = 0
                line["available_minutes"] = Decimal("0")
                line["produced_minutes"] = sum(
                    Decimal(hour["produced_minutes"]) for hour in line["hourly_rows"]
                )
                line["hourly_efficiency"] = Decimal("0")

        context["line_summary_report"] = list(line_summary_by_line.values())

        qa_summary = self.get_qa_summary(selected_factory_id=selected_factory_id, date_from=date_from, date_to=date_to)
        qa_daily = qa_summary["daily"]
        if qa_daily:
            qa_line_output, qa_daily_detail, qa_forecast = self.get_qa_reporting_data(
                selected_factory_id=selected_factory_id,
                date_from=date_from,
                date_to=date_to,
                selected_style_id=selected_style_id,
            )
            qa_metrics = {}
            for row in qa_daily_detail:
                metric = qa_metrics.setdefault((row["date"], row["shift"]), {
                    "available_minutes": Decimal("0"),
                    "produced_minutes": Decimal("0"),
                })
                metric["available_minutes"] += row["available_minutes"]
                metric["produced_minutes"] += row["produced_minutes"]
            context["qa_daily_summary"] = []
            for summary_row in qa_daily:
                metric = qa_metrics.get((summary_row["check_date"], summary_row["shift_name"]))
                available = metric["available_minutes"] if metric else Decimal("0")
                produced = metric["produced_minutes"] if metric else Decimal("0")
                context["qa_daily_summary"].append({
                    "date": summary_row["check_date"],
                    "shift": summary_row["shift_name"] or "Unassigned",
                    "inspected": summary_row["total_inspected"] or 0,
                    "defects": summary_row["total_defects"] or 0,
                    "dhu": (Decimal(summary_row["total_defects"] or 0) / Decimal(summary_row["total_inspected"]) * 100)
                    if summary_row["total_inspected"] else Decimal("0"),
                    "efficiency": (produced / available * 100) if available else Decimal("0"),
                })
            daily_efficiency_data = {}
            for row in qa_daily_detail:
                daily = daily_efficiency_data.setdefault(row["date"], {
                    "actual_output": Decimal("0"),
                    "produced_minutes": Decimal("0"),
                    "available_minutes": Decimal("0"),
                })
                daily["actual_output"] += row["actual_output"]
                daily["produced_minutes"] += row["produced_minutes"]
                daily["available_minutes"] += row["available_minutes"]
            dates = sorted(daily_efficiency_data)
            output_series = [float(daily_efficiency_data[day]["actual_output"]) for day in dates]
            efficiency_series = [
                round(float(data["produced_minutes"] / data["available_minutes"] * 100), 2)
                if data["available_minutes"] else 0
                for data in (daily_efficiency_data[day] for day in dates)
            ]
            chart_labels, chart_line_datasets = _build_line_output_chart(
                {
                    "date": row["date"],
                    "line": row["line"],
                    "output": row["actual_output"],
                }
                for row in qa_daily_detail
            )
            total_output = sum(output_series)
            total_defects = qa_summary["total_defects"]
            dhu = (total_defects / total_output * 100) if total_output else 0
            context["chart_labels"] = chart_labels
            context["chart_line_datasets"] = chart_line_datasets
            context["chart_output_series"] = json.dumps(output_series)
            context["chart_efficiency_series"] = json.dumps(efficiency_series)
            context["kpi_total_output"] = total_output
            context["kpi_total_defects"] = total_defects
            context["kpi_dhu"] = round(dhu, 2)
            total_produced_minutes = sum(data["produced_minutes"] for data in daily_efficiency_data.values())
            total_available_minutes = sum(data["available_minutes"] for data in daily_efficiency_data.values())
            context["kpi_avg_efficiency"] = round(
                float(total_produced_minutes / total_available_minutes * 100), 2
            ) if total_available_minutes else 0
            context["qa_defect_breakdown"] = qa_summary["defect_breakdown"]
            context["quality_source"] = "QA"
            context["line_output"] = qa_line_output
            context["forecast_assignments"] = qa_forecast

            try:
                selected_date_start = datetime.fromisoformat(date_from).date() if date_from else timezone.localdate()
                selected_date_end = datetime.fromisoformat(date_to).date() if date_to else selected_date_start
            except ValueError:
                selected_date_start = timezone.localdate()
                selected_date_end = timezone.localdate()
            selected_dates = []
            current_date = selected_date_start
            while current_date <= selected_date_end:
                selected_dates.append(current_date)
                current_date += timedelta(days=1)
            qa_rows_by_date = {}
            for row in qa_daily_detail:
                qa_rows_by_date.setdefault(row["date"], []).append(row)

            fallback_by_date = {}
            fallback_detail_rows = entries.values(
                "production_date",
                "assignment__production_line__line_name",
                "assignment__production_order__article__article_code",
            ).annotate(
                total_available_minutes=Sum("available_minutes"),
                total_produced_minutes=Sum("produced_minutes"),
                target_output=Sum("target_output"),
                actual_output=Sum("actual_output"),
                manpower=Sum("actual_manpower"),
                defect_pieces=Sum("defect_pieces"),
                planned_productive_minutes=Sum(
                    ExpressionWrapper(
                        F("available_minutes") * F("planned_efficiency"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                gain_loss_minutes=Sum("gain_loss_minutes"),
            ).order_by("production_date", "assignment__production_line__line_name", "assignment__production_order__article__article_code")
            for row in fallback_detail_rows:
                available = Decimal(row["total_available_minutes"] or 0)
                produced = Decimal(row["total_produced_minutes"] or 0)
                planned_minutes = Decimal(row["planned_productive_minutes"] or 0)
                planned_efficiency = planned_minutes / available * 100 if available else Decimal("0")
                actual_output = Decimal(row["actual_output"] or 0)
                target_output = Decimal(row["target_output"] or 0)
                row_payload = {
                    "date": row["production_date"],
                    "line": row["assignment__production_line__line_name"],
                    "shift": "Production",
                    "article": row["assignment__production_order__article__article_code"],
                    "manpower": row["manpower"] or 0,
                    "available_minutes": available,
                    "produced_minutes": produced,
                    "planned_efficiency": planned_efficiency,
                    "actual_efficiency": (produced / available * 100) if available else Decimal("0"),
                    "daily_efficiency": (produced / available * 100) if available else Decimal("0"),
                    "target_output": target_output,
                    "actual_output": actual_output,
                    "defect_pieces": row["defect_pieces"] or 0,
                    "dhu": (Decimal(row["defect_pieces"] or 0) / actual_output * 100) if actual_output else Decimal("0"),
                    "difference": actual_output - target_output,
                    "gain_loss_minutes": row["gain_loss_minutes"] or 0,
                    "gain_loss_efficiency": ((produced / available * 100) if available else Decimal("0")) - planned_efficiency,
                }
                fallback_by_date.setdefault(row["production_date"], []).append(row_payload)

            merged_detail = []
            for day in selected_dates:
                merged_detail.extend(qa_rows_by_date.get(day, []))
                if day not in qa_rows_by_date and day in fallback_by_date:
                    merged_detail.extend(fallback_by_date[day])
            context["daily_detail"] = merged_detail
        else:
            context["qa_daily_summary"] = []
            daily = (
                entries.values("production_date")
                .annotate(
                    total_output=Sum("actual_output"),
                    total_produced_minutes=Sum("produced_minutes"),
                    total_available_minutes=Sum("available_minutes"),
                    total_gain_loss=Sum("gain_loss_minutes"),
                )
                .order_by("production_date")
            )

            chart_labels, chart_line_datasets = _build_line_output_chart(
                {
                    "date": row["production_date"],
                    "line": row["assignment__production_line__line_name"],
                    "output": row["total_output"],
                }
                for row in entries.values(
                    "production_date",
                    "assignment__production_line__line_name",
                ).annotate(
                    total_output=Sum("actual_output"),
                ).order_by("production_date", "assignment__production_line__line_name")
            )
            output_series = [float(row["total_output"] or 0) for row in daily]
            efficiency_series = [
                round(float(row["total_produced_minutes"] / row["total_available_minutes"] * 100), 2)
                if row["total_available_minutes"] else 0
                for row in daily
            ]

            context["chart_labels"] = chart_labels
            context["chart_line_datasets"] = chart_line_datasets
            context["chart_output_series"] = json.dumps(output_series)
            context["chart_efficiency_series"] = json.dumps(efficiency_series)
            total_output = sum(output_series)
            total_defects = sum(float(row["defect_pieces"] or 0) for row in entries.values("defect_pieces"))
            dhu = (total_defects / total_output * 100) if total_output else 0
            context["kpi_total_output"] = total_output
            context["kpi_total_defects"] = total_defects
            context["kpi_dhu"] = round(dhu, 2)
            total_produced_minutes = sum(Decimal(row["total_produced_minutes"] or 0) for row in daily)
            total_available_minutes = sum(Decimal(row["total_available_minutes"] or 0) for row in daily)
            context["kpi_avg_efficiency"] = round(
                float(total_produced_minutes / total_available_minutes * 100), 2
            ) if total_available_minutes else 0
            context["qa_defect_breakdown"] = []
            context["quality_source"] = "Production"

        style_ids = self.get_filtered_entries().values_list(
            "assignment__production_order__article_id", flat=True
        ).distinct()
        context["styles"] = Article.objects.filter(pk__in=style_ids).order_by("article_code")
        context["selected_style_id"] = int(selected_style_id) if selected_style_id and selected_style_id.isdigit() else None
        context["selected_style"] = context["styles"].filter(pk=context["selected_style_id"]).first() if context["selected_style_id"] else None
        context["line_output"] = entries.values(
            "assignment__production_line__line_name"
        ).annotate(
            planned_output=Sum("target_output"),
            actual_output=Sum("actual_output"),
        ).annotate(
            variance=ExpressionWrapper(
                F("actual_output") - F("planned_output"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        ).order_by("assignment__production_line__line_name")

        daily_detail_rows = entries.values(
            "production_date",
            "assignment__production_line__line_name",
            "assignment__production_order__article__article_code",
        ).annotate(
            total_available_minutes=Sum("available_minutes"),
            total_produced_minutes=Sum("produced_minutes"),
            target_output=Sum("target_output"),
            actual_output=Sum("actual_output"),
            manpower=Sum("actual_manpower"),
            defect_pieces=Sum("defect_pieces"),
            planned_productive_minutes=Sum(
                ExpressionWrapper(
                    F("available_minutes") * F("planned_efficiency"),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            ),
            gain_loss_minutes=Sum("gain_loss_minutes"),
        ).order_by(
            "-production_date",
            "assignment__production_line__line_name",
            "assignment__production_order__article__article_code",
        )
        if not (qa_daily and context.get("daily_detail")):
            context["daily_detail"] = []
            for row in daily_detail_rows:
                available = Decimal(row["total_available_minutes"] or 0)
                produced = Decimal(row["total_produced_minutes"] or 0)
                daily_efficiency = (produced / available * 100) if available else Decimal("0")
                actual_efficiency = produced / available * 100 if available else Decimal("0")
                planned_minutes = Decimal(row["planned_productive_minutes"] or 0)
                planned_efficiency = planned_minutes / available * 100 if available else Decimal("0")
                target_output = Decimal(row["target_output"] or 0)
                actual_output = Decimal(row["actual_output"] or 0)
                context["daily_detail"].append({
                    "date": row["production_date"],
                    "line": row["assignment__production_line__line_name"],
                    "article": row["assignment__production_order__article__article_code"],
                    "manpower": row["manpower"] or 0,
                    "available_minutes": available,
                    "produced_minutes": produced,
                    "planned_efficiency": planned_efficiency,
                    "actual_efficiency": actual_efficiency,
                    "daily_efficiency": daily_efficiency,
                    "target_output": target_output,
                    "actual_output": actual_output,
                    "defect_pieces": row["defect_pieces"] or 0,
                    "dhu": (Decimal(row["defect_pieces"] or 0) / actual_output * 100) if actual_output else Decimal("0"),
                    "difference": actual_output - target_output,
                    "gain_loss_minutes": row["gain_loss_minutes"] or 0,
                    "gain_loss_efficiency": daily_efficiency - planned_efficiency,
                })

        forecast_entries = self.get_filtered_entries()
        # Apply style filter to forecast entries (same filter as main entries)
        if selected_style_id and selected_style_id.isdigit():
            forecast_entries = forecast_entries.filter(assignment__production_order__article_id=selected_style_id)

        forecast_summary = forecast_entries.aggregate(
            planned_output=Sum("target_output"),
            actual_output=Sum("actual_output"),
            average_efficiency=Avg("actual_efficiency"),
        )
        forecast_assignments = LineAssignment.objects.filter(
            pk__in=forecast_entries.values_list("assignment_id", flat=True).distinct()
        ).select_related("production_order__article", "production_line")
        forecast_data = {}
        forecast_total = Decimal("0")
        for assignment in forecast_assignments:
            forecast_amount = assignment.forecast_daily_output()
            forecast_total += forecast_amount
            key = (
                assignment.production_line.line_name,
                assignment.production_order.article.article_code,
            )
            totals = forecast_entries.filter(assignment=assignment).aggregate(
                planned_output=Sum("target_output"),
                actual_output=Sum("actual_output"),
            )
            row = forecast_data.setdefault(key, {
                "line_name": assignment.production_line.line_name,
                "order_number": assignment.production_order.order_number,
                "article_code": assignment.production_order.article.article_code,
                "planned_output": Decimal("0"),
                "actual_output": Decimal("0"),
                "forecast_daily_output": Decimal("0"),
                "expected_days_remaining": None,
            })
            row["planned_output"] += totals["planned_output"] or Decimal("0")
            row["actual_output"] += totals["actual_output"] or Decimal("0")
            row["forecast_daily_output"] += forecast_amount
            days_remaining = assignment.expected_days_remaining()
            if days_remaining is not None:
                if row["expected_days_remaining"] is None:
                    row["expected_days_remaining"] = days_remaining
                else:
                    row["expected_days_remaining"] = max(row["expected_days_remaining"], days_remaining)
        forecast_rows = list(forecast_data.values())
        forecast_days_values = [row["expected_days_remaining"] for row in forecast_rows if row["expected_days_remaining"] is not None]
        context["forecast_summary"] = forecast_summary
        context["forecast_average_efficiency"] = round(float(forecast_summary["average_efficiency"] or 0) * 100, 2)
        context["forecast_total"] = forecast_total
        context["forecast_days_to_complete"] = max(forecast_days_values) if forecast_days_values else None
        context["forecast_assignments"] = forecast_rows

        # QA is the live output source whenever checks exist in the selected range.
        if qa_daily:
            qa_line_output, qa_daily_detail, qa_forecast = self.get_qa_reporting_data(
                selected_factory_id=selected_factory_id,
                date_from=date_from,
                date_to=date_to,
                selected_style_id=selected_style_id,
            )
            if qa_line_output or qa_daily_detail:
                context["line_output"] = qa_line_output
                context["forecast_assignments"] = qa_forecast
                context["styles"] = Article.objects.filter(
                    hourly_line_checks__factory_id=selected_factory_id,
                    hourly_line_checks__check_date__gte=date_from or timezone.localdate() - timedelta(days=self.days_back),
                ).distinct().order_by("article_code")
                context["selected_style"] = context["styles"].filter(
                    pk=context["selected_style_id"]
                ).first() if context["selected_style_id"] else None
        return context


class AdminDashboardView(AdminRequiredMixin, BaseDashboardView):
    template_name = "dashboard/admin_dashboard.html"

    def get_scoped_entries(self):
        user = self.request.user
        if user.is_superuser:
            return DailyProductionEntry.objects.all()
        profile = getattr(user, "profile", None)
        if profile is None:
            return DailyProductionEntry.objects.none()
        if profile.is_admin:
            return DailyProductionEntry.objects.filter(
                assignment__production_line__factory__in=profile.accessible_factories.all()
            )
        return DailyProductionEntry.objects.none()


class UserDashboardView(BaseDashboardView):
    template_name = "dashboard/user_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        profile = getattr(request.user, "profile", None)
        if profile and profile.role == "QA_PERMISSION":
            return redirect("dashboard:qa_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_scoped_entries(self):
        user = self.request.user
        profile = getattr(user, "profile", None)
        if profile and profile.is_admin:
            return DailyProductionEntry.objects.filter(
                assignment__production_line__factory__in=profile.accessible_factories.all()
            )
        if profile and not profile.is_admin:
            entries = DailyProductionEntry.objects.filter(
                assignment__production_line__in=profile.assigned_lines.all()
            )
            if profile.factory_id:
                entries = entries.filter(assignment__production_line__factory_id=profile.factory_id)
            return entries
        return DailyProductionEntry.objects.none()


class QADashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/qa_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect("dashboard:admin_dashboard")
        profile = getattr(request.user, "profile", None)
        if profile is None or profile.role != "QA_PERMISSION":
            return redirect("dashboard:user_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = getattr(user, "profile", None)
        lines = list(profile.assigned_lines.select_related("factory").order_by("factory__name", "line_name")) if profile else []
        context["assigned_lines"] = lines
        context["primary_line"] = lines[0] if lines else None
        context["factory_name"] = profile.factory.name if profile and profile.factory else "Line Access"
        context["qa_user"] = user.get_full_name() or user.get_username()

        running_articles = Article.objects.filter(
            orders__status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS],
            orders__assignments__production_line__in=lines,
        ).annotate(
            qa_line_id=Min("orders__assignments__production_line_id")
        ).distinct().order_by("article_code") if lines else Article.objects.none()
        if "article" in self.request.GET:
            selected_article_id = self.request.GET.get("article")
        else:
            selected_article_id = self.request.session.get("qa_article_id")
        selected_article = running_articles.filter(pk=selected_article_id).first() if selected_article_id else None
        if selected_article:
            self.request.session["qa_article_id"] = selected_article.pk
        else:
            self.request.session.pop("qa_article_id", None)
        selected_line = next(
            (line for line in lines if selected_article and line.pk == selected_article.qa_line_id),
            None,
        )
        context["selected_line"] = selected_line
        context["selected_line_id"] = selected_line.pk if selected_line else None
        context["running_articles"] = running_articles
        context["selected_article"] = selected_article
        factory = selected_line.factory if selected_line else (profile.factory if profile else None)
        current_shift = FactoryShift.get_active_shift_for_factory(factory) if factory else None
        context["current_shift"] = current_shift.shift_name if current_shift else "Not assigned"
        schedule_status = current_shift.current_schedule_status() if current_shift else None
        context["current_hour_label"] = schedule_status["window"] if schedule_status else "Not assigned"
        context["current_hour_number"] = schedule_status["label"] if schedule_status else "Not assigned"
        today_checks = HourlyLineCheck.objects.filter(
            production_line__in=profile.assigned_lines.all(),
            check_date=timezone.localdate(),
        ) if profile else HourlyLineCheck.objects.none()
        dashboard_checks = (
            today_checks.filter(article=selected_article)
            if selected_article
            else today_checks
        )
        current_hour_number = schedule_status["number"] if schedule_status else timezone.localtime().hour
        current_hour_checks = dashboard_checks.filter(
            check_hour=current_hour_number,
            shift_name=current_shift.shift_name if current_shift else "",
        )
        current_inspected, current_passed, _ = _effective_qa_totals(current_hour_checks)
        shift_inspected, shift_passed, _ = _effective_qa_totals(dashboard_checks)
        current_defects = _marked_qa_defects(current_hour_checks)
        shift_defects = _marked_qa_defects(dashboard_checks)
        context["qa_current_hour_inspected"] = current_inspected
        context["qa_current_hour_passed"] = current_passed
        context["qa_current_hour_alter"] = current_defects
        context["qa_shift_inspected"] = shift_inspected
        context["qa_shift_passed"] = shift_passed
        context["qa_shift_defects"] = shift_defects
        hourly_report = []
        summary_keys = set(dashboard_checks.values_list("check_date", "shift_name", "check_hour", "production_line_id"))
        for check_date, shift_name, check_hour, line_id in sorted(summary_keys, key=lambda item: (item[0], item[1], item[2], item[3])):
            summary_checks = dashboard_checks.filter(
                check_date=check_date,
                shift_name=shift_name,
                check_hour=check_hour,
                production_line_id=line_id,
            )
            summary_inspected, summary_passed, _ = _effective_qa_totals(summary_checks)
            summary_defects = _marked_qa_defects(summary_checks)
            hourly_report.append({
                "date": check_date.isoformat(),
                "shift": shift_name or "Legacy shift",
                "hour": check_hour,
                "line": summary_checks.first().production_line.line_name,
                "passed": summary_passed,
                "alter": summary_defects,
                "reject": 0,
                "inspected": summary_inspected,
                "current": check_date == timezone.localdate() and shift_name == (current_shift.shift_name if current_shift else "") and check_hour == current_hour_number,
            })
        context["hourly_report"] = hourly_report
        repair_details = (
            HourlyLineCheckDetail.objects.filter(
                hourly_check__production_line__in=profile.assigned_lines.all(),
                resolved=False,
            )
            .filter(repaired_quantity__lt=F("defect_quantity"))
            .select_related(
                "defect",
                "hourly_check",
                "hourly_check__production_line",
                "hourly_check__article",
            )
            .order_by("hourly_check__check_date", "hourly_check__check_hour", "defect__defect_code")
        ) if profile else HourlyLineCheckDetail.objects.none()
        if selected_article:
            repair_details = repair_details.filter(hourly_check__article=selected_article)
        context["repair_queue"] = [
            {
                "id": detail.pk,
                "tracking_code": detail.tracking_code,
                "marked_at": detail.marked_at.isoformat(),
                "resolved_at": detail.resolved_at.isoformat() if detail.resolved_at else None,
                "code": detail.defect.defect_code,
                "name": detail.defect.defect_name,
                "qty": detail.defect_quantity - detail.repaired_quantity,
                "line": detail.hourly_check.production_line.line_name,
                "article": detail.hourly_check.article.article_code,
                "shift": detail.hourly_check.shift_name,
                "date": detail.hourly_check.check_date.isoformat(),
                "hour": detail.hourly_check.check_hour,
            }
            for detail in repair_details
        ]
        context["defect_options"] = (
            DefectMaster.objects.filter(active=True).order_by("defect_code")
        )
        return context


def _qa_profile(request):
    profile = getattr(request.user, "profile", None)
    if not profile or profile.role != "QA_PERMISSION":
        return None
    return profile


def _effective_qa_totals(checks):
    """Calculate inspected and passed pieces from pass and defect records."""
    inspected = passed = defects = 0
    for check in checks:
        detail_totals = check.details.aggregate(
            defects=Sum("defect_quantity"),
            repaired=Sum("repaired_quantity"),
        )
        recorded_defects = detail_totals["defects"] or 0
        repaired = detail_totals["repaired"] or 0
        if recorded_defects:
            # Older repair actions already added repaired pieces to the stored
            # passed value. Remove them before applying the canonical formula.
            original_passed = max(check.passed_quantity - repaired, 0)
            effective_passed = original_passed + repaired
            effective_inspected = original_passed + recorded_defects
            defects += recorded_defects
        else:
            original_passed = check.passed_quantity
            effective_passed = original_passed
            effective_inspected = max(check.inspected_quantity, original_passed + check.defective_quantity)
            defects += check.defective_quantity
        inspected += effective_inspected
        passed += effective_passed
    return inspected, passed, defects


def _marked_qa_defects(checks):
    """Count all defect pieces marked, including defects later repaired."""
    total = 0
    for check in checks:
        marked = check.details.aggregate(total=Sum("defect_quantity"))["total"]
        total += marked if marked is not None else check.defective_quantity
    return total


@require_GET
def qa_dashboard_data(request):
    profile = _qa_profile(request)
    if profile is None:
        return JsonResponse({"error": "QA access is required."}, status=403)

    dashboard = QADashboardView()
    dashboard.request = request
    context = dashboard.get_context_data()
    return JsonResponse({
        "factory": context["factory_name"],
        "shift": context["current_shift"],
        "current_hour": {
            "number": context["current_hour_number"],
            "label": context["current_hour_label"],
            "inspected": context["qa_current_hour_inspected"],
            "passed": context["qa_current_hour_passed"],
            "alter": context["qa_current_hour_alter"],
        },
        "shift_totals": {
            "inspected": context["qa_shift_inspected"],
            "passed": context["qa_shift_passed"],
            "alter": context["qa_shift_defects"],
            "reject": 0,
        },
        "hourly_report": context["hourly_report"],
        "pending_repairs": context["repair_queue"],
    })


@require_POST
def qa_mark_defect(request):
    profile = _qa_profile(request)
    if profile is None:
        return JsonResponse({"error": "QA access is required."}, status=403)

    try:
        payload = json.loads(request.body or "{}")
        line_id = int(payload.get("line_id"))
        quantity = int(payload.get("quantity"))
        article_id = int(payload.get("article_id")) if payload.get("article_id") else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Running article, line, and quantity are required."}, status=400)

    if quantity < 1:
        return JsonResponse({"error": "Quantity must be at least 1."}, status=400)

    line = profile.assigned_lines.filter(pk=line_id).select_related("factory").first()
    defect = DefectMaster.objects.filter(
        defect_code=payload.get("defect_code"), active=True
    ).first()
    if line is None or defect is None:
        return JsonResponse({"error": "Select an assigned line and active defect."}, status=400)
    article = Article.objects.filter(
        pk=article_id,
        orders__status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS],
        orders__assignments__production_line=line,
    ).distinct().first() if article_id else None
    if article is None and article_id is None:
        legacy_order = ProductionOrder.objects.filter(
            status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS],
            assignments__production_line=line,
        ).select_related("article").order_by("delivery_date").first()
        article = legacy_order.article if legacy_order else None
    if article is None:
        return JsonResponse({"error": "Select a running article from this factory."}, status=400)

    today = timezone.localdate()
    shift = FactoryShift.get_active_shift_for_factory(line.factory)
    schedule_status = shift.current_schedule_status() if shift else None
    check_hour = schedule_status["number"] if schedule_status else timezone.localtime().hour
    check = HourlyLineCheck.objects.filter(
        production_line=line,
        article=article,
        check_date=today,
        check_hour=check_hour,
    ).order_by("-created_at").first()
    if check is None:
        order = ProductionOrder.objects.filter(
            article=article,
            status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS],
            assignments__production_line=line,
        ).order_by("delivery_date").first()
        if order is None:
            return JsonResponse({"error": "No running order exists for this article in the factory."}, status=400)
        check = HourlyLineCheck.objects.create(
            factory=line.factory,
            production_line=line,
            production_order=order,
            article=article,
            check_date=today,
            check_hour=check_hour,
            shift_name=shift.shift_name if shift else "",
            checker=request.user,
            created_by=request.user,
            status="SUBMITTED",
        )

    tracking_code = None
    while tracking_code is None or HourlyLineCheckDetail.objects.filter(tracking_code=tracking_code).exists():
        tracking_code = str(secrets.randbelow(900000) + 100000)
    detail = HourlyLineCheckDetail.objects.create(
        hourly_check=check,
        defect=defect,
        tracking_code=tracking_code,
        defect_quantity=quantity,
        created_by=request.user,
    )
    check.inspected_quantity += quantity
    check.defective_quantity += quantity
    check.save(update_fields=["inspected_quantity", "defective_quantity", "updated_at"])

    return JsonResponse({
        "id": detail.pk,
        "tracking_code": detail.tracking_code,
        "marked_at": detail.marked_at.isoformat(),
        "code": defect.defect_code,
        "name": defect.defect_name,
        "qty": detail.defect_quantity - detail.repaired_quantity,
        "line": line.line_name,
        "article": check.article.article_code,
        "shift": check.shift_name,
        "date": check.check_date.isoformat(),
        "hour": check.check_hour,
    })


@require_POST
def qa_mark_pass(request):
    profile = _qa_profile(request)
    if profile is None:
        return JsonResponse({"error": "QA access is required."}, status=403)

    try:
        line_id = int(request.POST.get("line_id"))
        article_id = int(request.POST.get("article_id")) if request.POST.get("article_id") else None
    except (TypeError, ValueError):
        return JsonResponse({"error": "A running article and assigned line are required."}, status=400)

    line = profile.assigned_lines.filter(pk=line_id).select_related("factory").first()
    if line is None:
        return JsonResponse({"error": "Select an assigned production line."}, status=400)
    article = Article.objects.filter(
        pk=article_id,
        orders__status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS],
        orders__assignments__production_line=line,
    ).distinct().first() if article_id else None
    if article is None and article_id is None:
        legacy_order = ProductionOrder.objects.filter(
            status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS],
            assignments__production_line=line,
        ).select_related("article").order_by("delivery_date").first()
        article = legacy_order.article if legacy_order else None
    if article is None:
        return JsonResponse({"error": "Select a running article from this factory."}, status=400)

    today = timezone.localdate()
    shift = FactoryShift.get_active_shift_for_factory(line.factory)
    schedule_status = shift.current_schedule_status() if shift else None
    check_hour = schedule_status["number"] if schedule_status else timezone.localtime().hour
    check = HourlyLineCheck.objects.filter(
        production_line=line,
        article=article,
        check_date=today,
        check_hour=check_hour,
    ).order_by("-created_at").first()
    if check is None:
        order = ProductionOrder.objects.filter(
            article=article,
            status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS],
            assignments__production_line=line,
        ).order_by("delivery_date").first()
        if order is None:
            return JsonResponse({"error": "No running order exists for this article in the factory."}, status=400)
        check = HourlyLineCheck.objects.create(
            factory=line.factory,
            production_line=line,
            production_order=order,
            article=article,
            check_date=today,
            check_hour=check_hour,
            shift_name=shift.shift_name if shift else "",
            checker=request.user,
            created_by=request.user,
            status="SUBMITTED",
        )

    check.inspected_quantity += 1
    check.passed_quantity += 1
    check.save(update_fields=["inspected_quantity", "passed_quantity", "updated_at"])
    shift_checks = HourlyLineCheck.objects.filter(
        production_line__in=profile.assigned_lines.all(),
        check_date=today,
        shift_name=check.shift_name,
    )
    current_checks = shift_checks.filter(check_hour=check_hour)
    shift_inspected, shift_passed, _ = _effective_qa_totals(shift_checks)
    current_inspected, current_passed, _ = _effective_qa_totals(current_checks)
    return JsonResponse({
        "inspected_quantity": shift_inspected,
        "passed_quantity": shift_passed,
        "current_hour_inspected": current_inspected,
        "current_hour_passed": current_passed,
    })


@require_POST
def qa_repair_defect(request):
    profile = _qa_profile(request)
    if profile is None:
        return JsonResponse({"error": "QA access is required."}, status=403)

    try:
        detail_id = int(request.POST.get("detail_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "A defect detail is required."}, status=400)

    detail = (
        HourlyLineCheckDetail.objects.filter(
            pk=detail_id,
            hourly_check__production_line__in=profile.assigned_lines.all(),
            resolved=False,
        )
        .select_related("hourly_check")
        .first()
    )
    if detail is None:
        return JsonResponse({"error": "This defect is no longer awaiting repair."}, status=404)

    repaired_quantity = detail.defect_quantity - detail.repaired_quantity
    detail.repaired_quantity = detail.defect_quantity
    detail.resolved = True
    detail.resolved_at = timezone.now()
    detail.save(update_fields=["repaired_quantity", "resolved", "resolved_at", "updated_at"])
    check = detail.hourly_check
    check.passed_quantity += repaired_quantity
    check.defective_quantity = max(check.defective_quantity - repaired_quantity, 0)
    check.save(update_fields=["passed_quantity", "defective_quantity", "updated_at"])

    today = timezone.localdate()
    shift_checks = HourlyLineCheck.objects.filter(
        production_line__in=profile.assigned_lines.all(),
        check_date=today,
        shift_name=check.shift_name,
    )
    current_checks = shift_checks.filter(check_hour=check.check_hour)
    shift_inspected, shift_passed, _ = _effective_qa_totals(shift_checks)
    current_inspected, current_passed, _ = _effective_qa_totals(current_checks)
    shift_defects = _marked_qa_defects(shift_checks)
    current_defects = _marked_qa_defects(current_checks)

    return JsonResponse({
        "repaired_quantity": repaired_quantity,
        "detail_id": detail.pk,
        "tracking_code": detail.tracking_code,
        "marked_at": detail.marked_at.isoformat(),
        "resolved_at": detail.resolved_at.isoformat(),
        "shift_totals": {
            "inspected": shift_inspected,
            "passed": shift_passed,
            "defects": shift_defects,
        },
        "current_hour_totals": {
            "inspected": current_inspected,
            "passed": current_passed,
            "defects": current_defects,
        },
    })


class FactorySelectorView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/factory_selector.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_superuser:
            factories = Factory.objects.filter(active_status=True)
        else:
            factories = Factory.objects.filter(
                active_status=True,
                authorized_users=self.request.user,
            )
        context["factories"] = factories.distinct()
        context["selected_factory_id"] = self.request.session.get("selected_factory_id")
        return context

    def post(self, request, *args, **kwargs):
        factory_id = request.POST.get("factory_id")
        factories = Factory.objects.filter(pk=factory_id, active_status=True)
        if not request.user.is_superuser:
            factories = factories.filter(authorized_users=request.user)
        factory = factories.first()
        if factory is None:
            from django.contrib import messages

            messages.error(request, "Select an active factory.")
            return self.get(request, *args, **kwargs)

        request.session["selected_factory_id"] = factory.pk
        from django.contrib import messages

        messages.success(request, f"Factory selected: {factory.name}")
        from django.shortcuts import redirect

        return redirect("dashboard:admin_dashboard")


class ShiftManagementView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/shift_management.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return redirect("dashboard:user_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        factories = Factory.objects.filter(active_status=True).order_by("name")
        context["factories"] = factories
        selected_factory_id = self.request.GET.get("factory") or self.request.session.get("selected_factory_id")
        if selected_factory_id and factories.filter(pk=selected_factory_id).exists():
            factory = factories.get(pk=selected_factory_id)
        else:
            factory = factories.first()
        context["selected_factory"] = factory
        context["selected_factory_id"] = factory.pk if factory else None
        context["factory_shifts"] = (
            FactoryShift.objects.filter(factory=factory).order_by("start_time") if factory else FactoryShift.objects.none()
        )
        return context

    def post(self, request, *args, **kwargs):
        factory_id = request.POST.get("factory")
        factory = Factory.objects.filter(pk=factory_id, active_status=True).first()
        if factory is None:
            from django.contrib import messages

            messages.error(request, "Select a valid active factory.")
            return redirect("dashboard:shift_management")

        shift_name = (request.POST.get("shift_name") or "").strip()
        start_time = request.POST.get("start_time")
        end_time = request.POST.get("end_time")
        lunch_break_start = request.POST.get("lunch_break_start")
        lunch_break_end = request.POST.get("lunch_break_end")
        if not shift_name or not start_time or not end_time:
            from django.contrib import messages

            messages.error(request, "Shift name, start time, and end time are required.")
            return redirect("dashboard:shift_management")

        try:
            standard_hours = int(request.POST.get("standard_hours") or 8)
            overtime_hours = int(request.POST.get("overtime_hours") or 0)
        except (TypeError, ValueError):
            from django.contrib import messages

            messages.error(request, "Shift hours must be whole numbers.")
            return redirect("dashboard:shift_management")
        if not 1 <= standard_hours <= 12 or not 0 <= overtime_hours <= 8:
            from django.contrib import messages

            messages.error(request, "Regular hours must be 1-12 and overtime hours must be 0-8.")
            return redirect("dashboard:shift_management")

        if lunch_break_start and lunch_break_end:
            try:
                if time.fromisoformat(lunch_break_start) >= time.fromisoformat(lunch_break_end):
                    raise ValueError
            except ValueError:
                from django.contrib import messages

                messages.error(request, "Lunch break start must be earlier than lunch break end.")
                return redirect("dashboard:shift_management")

        temp_shift = FactoryShift(
            factory=factory,
            shift_name=shift_name,
            start_time=start_time,
            end_time=end_time,
            standard_hours=standard_hours,
            overtime_hours=overtime_hours,
            overtime_start_time=request.POST.get("overtime_start_time") or None,
            active=request.POST.get("active") == "on",
            assigned_by=request.user,
        )
        hourly_schedule = temp_shift.build_hourly_schedule(
            start_time=start_time,
            end_time=end_time,
            lunch_break_start=lunch_break_start,
            lunch_break_end=lunch_break_end,
        )

        if not hourly_schedule:
            from django.contrib import messages

            messages.error(request, "The shift schedule could not be generated. Please review the selected times.")
            return redirect("dashboard:shift_management")

        description = request.POST.get("description") or ""
        if lunch_break_start and lunch_break_end:
            description = json.dumps({
                "lunch_break": {"start": lunch_break_start, "end": lunch_break_end},
                "overtime_start": request.POST.get("overtime_start_time") or "",
                "overtime_hours": overtime_hours,
            }, ensure_ascii=True)

        FactoryShift.objects.create(
            factory=factory,
            shift_name=shift_name,
            start_time=start_time,
            end_time=end_time,
            standard_hours=standard_hours,
            overtime_hours=overtime_hours,
            overtime_start_time=request.POST.get("overtime_start_time") or None,
            description=description,
            hourly_schedule=hourly_schedule,
            active=request.POST.get("active") == "on",
            assigned_by=request.user,
        )

        request.session["selected_factory_id"] = factory.pk
        from django.contrib import messages

        messages.success(request, f"Shift '{shift_name}' saved for {factory.name}. Break schedule updated.")
        return redirect("dashboard:shift_management")
