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
from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Sum
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.mixins import AdminRequiredMixin, admin_required
from master_data.models import Article, Factory, FactoryShift, ProductionLineShiftAssignment
from planning.models import LineAssignment
from planning.services.calculations import forecast_daily_output
from production.models import DailyProductionEntry
from quality.models import DefectMaster, HourlyLineCheck, HourlyLineCheckDetail


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
        average_efficiency=Avg("actual_efficiency"),
        average_planned_efficiency=Avg("planned_efficiency"),
        gain_loss_minutes=Sum("gain_loss_minutes"),
    ).order_by("-production_date", "assignment__production_line__line_name")
    rows = []
    for row in grouped:
        available = Decimal(row["total_available_minutes"] or 0)
        produced = Decimal(row["total_produced_minutes"] or 0)
        daily_efficiency = produced / available * 100 if available else Decimal("0")
        average_efficiency = Decimal(row["average_efficiency"] or 0) * 100
        planned_efficiency = Decimal(row["average_planned_efficiency"] or 0) * 100
        rows.append({
            "Date": row["production_date"],
            "Line": row["assignment__production_line__line_name"],
            "Article": row["assignment__production_order__article__article_code"],
            "Manpower": row["manpower"] or 0,
            "Available Minutes": available,
            "Produced Minutes": produced,
            "Daily Eff %": daily_efficiency,
            "Avg Eff %": average_efficiency,
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
        average_efficiency=Avg("actual_efficiency"),
    )
    summary = {
        "Total Days": len(dates),
        "Avg. Eff %": Decimal(aggregate["average_efficiency"] or 0) * 100,
        "Total Production": aggregate["total_production"] or 0,
        "Avg. Manpower": Decimal(aggregate["average_manpower"] or 0),
        "Total Available Minutes": aggregate["total_available_minutes"] or 0,
        "Total Produced Minutes": aggregate["total_produced_minutes"] or 0,
        "Average Gain/Loss Eff %": average("Gain/Loss Eff %"),
        "Total Target": sum((row["Target"] for row in rows), Decimal("0")),
    }
    return rows, summary


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

    def get_filtered_entries(self):
        entries = self.get_scoped_entries()

        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if date_from:
            entries = entries.filter(production_date__gte=date_from)
        elif not date_to:
            entries = entries.filter(production_date__gte=timezone.localdate() - timedelta(days=self.days_back))

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
        current_factory = Factory.objects.filter(pk=selected_factory_id).first() if selected_factory_id else None
        current_shift = FactoryShift.get_active_shift_for_factory(current_factory) if current_factory else None
        context["current_factory_name"] = current_factory.name if current_factory else "No factory selected"
        context["current_shift"] = current_shift.shift_name if current_shift else "Not assigned"
        context["current_hour_label"] = current_shift.current_hour_label() if current_shift else "Not assigned"
        selected_style_id = self.request.GET.get("style")
        if selected_style_id and selected_style_id.isdigit():
            entries = entries.filter(assignment__production_order__article_id=selected_style_id)
        entries = entries.order_by("production_date")
        context["selected_date_from"] = self.request.GET.get("date_from") or ""
        context["selected_date_to"] = self.request.GET.get("date_to") or ""
        context["daily_detail_export_query"] = self.request.GET.urlencode()

        daily = (
            entries.values("production_date")
            .annotate(
                total_output=Sum("actual_output"),
                avg_efficiency=Avg("actual_efficiency"),
                total_gain_loss=Sum("gain_loss_minutes"),
            )
            .order_by("production_date")
        )

        labels = [row["production_date"].strftime("%d-%b") for row in daily]
        output_series = [float(row["total_output"] or 0) for row in daily]
        efficiency_series = [round(float(row["avg_efficiency"] or 0) * 100, 2) for row in daily]

        context["chart_labels"] = json.dumps(labels)
        context["chart_output_series"] = json.dumps(output_series)
        context["chart_efficiency_series"] = json.dumps(efficiency_series)
        total_output = sum(output_series)
        total_defects = sum(float(row["defect_pieces"] or 0) for row in entries.values("defect_pieces"))
        dhu = (total_defects / total_output * 100) if total_output else 0
        context["kpi_total_output"] = total_output
        context["kpi_total_defects"] = total_defects
        context["kpi_dhu"] = round(dhu, 2)
        context["kpi_avg_efficiency"] = round(sum(efficiency_series) / len(efficiency_series), 2) if efficiency_series else 0

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
            average_efficiency=Avg("actual_efficiency"),
            average_planned_efficiency=Avg("planned_efficiency"),
            gain_loss_minutes=Sum("gain_loss_minutes"),
        ).order_by(
            "-production_date",
            "assignment__production_line__line_name",
            "assignment__production_order__article__article_code",
        )
        context["daily_detail"] = []
        for row in daily_detail_rows:
            available = Decimal(row["total_available_minutes"] or 0)
            produced = Decimal(row["total_produced_minutes"] or 0)
            daily_efficiency = (produced / available * 100) if available else Decimal("0")
            actual_efficiency = Decimal(row["average_efficiency"] or 0) * 100
            planned_efficiency = Decimal(row["average_planned_efficiency"] or 0) * 100
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
        return context


class AdminDashboardView(AdminRequiredMixin, BaseDashboardView):
    template_name = "dashboard/admin_dashboard.html"

    def get_scoped_entries(self):
        return DailyProductionEntry.objects.all()


class UserDashboardView(BaseDashboardView):
    template_name = "dashboard/user_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        profile = getattr(request.user, "profile", None)
        if profile and profile.role == "QA_PERMISSION":
            return redirect("dashboard:qa_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_scoped_entries(self):
        user = self.request.user
        if hasattr(user, "profile") and not user.profile.is_admin:
            entries = DailyProductionEntry.objects.filter(
                assignment__production_line__in=user.profile.assigned_lines.all()
            )
            if user.profile.factory_id:
                entries = entries.filter(assignment__production_line__factory_id=user.profile.factory_id)
            return entries
        return DailyProductionEntry.objects.all()


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
        lines = profile.assigned_lines.select_related("factory").order_by("factory__name", "line_name") if profile else []
        context["assigned_lines"] = lines
        context["factory_name"] = profile.factory.name if profile and profile.factory else "Line Access"
        context["qa_user"] = user.get_full_name() or user.get_username()

        factory = profile.factory if profile else None
        current_shift = FactoryShift.get_active_shift_for_factory(factory) if factory else None
        context["current_shift"] = current_shift.shift_name if current_shift else "Not assigned"
        schedule_status = current_shift.current_schedule_status() if current_shift else None
        context["current_hour_label"] = schedule_status["window"] if schedule_status else "Not assigned"
        context["current_hour_number"] = schedule_status["label"] if schedule_status else "Not assigned"
        today_checks = HourlyLineCheck.objects.filter(
            production_line__in=profile.assigned_lines.all(),
            check_date=timezone.localdate(),
        ) if profile else HourlyLineCheck.objects.none()
        dashboard_checks = today_checks
        current_hour_number = schedule_status["number"] if schedule_status else timezone.localtime().hour
        current_hour_checks = dashboard_checks.filter(
            check_hour=current_hour_number,
            shift_name=current_shift.shift_name if current_shift else "",
        )
        context["qa_current_hour_inspected"] = sum(check.inspected_quantity for check in current_hour_checks)
        context["qa_current_hour_passed"] = sum(check.passed_quantity for check in current_hour_checks)
        context["qa_current_hour_alter"] = sum(
            detail.defect_quantity
            for detail in HourlyLineCheckDetail.objects.filter(hourly_check__in=current_hour_checks)
        )
        context["qa_shift_inspected"] = sum(check.inspected_quantity for check in dashboard_checks)
        context["qa_shift_passed"] = sum(check.passed_quantity for check in dashboard_checks)
        context["qa_shift_defects"] = sum(check.defective_quantity for check in dashboard_checks)
        hourly_report = []
        summary_keys = set(dashboard_checks.values_list("check_date", "shift_name", "check_hour", "production_line_id"))
        for check_date, shift_name, check_hour, line_id in sorted(summary_keys, key=lambda item: (item[0], item[1], item[2], item[3])):
            summary_checks = dashboard_checks.filter(
                check_date=check_date,
                shift_name=shift_name,
                check_hour=check_hour,
                production_line_id=line_id,
            )
            summary_defects = HourlyLineCheckDetail.objects.filter(
                hourly_check__in=summary_checks
            ).aggregate(total=Sum("defect_quantity"))["total"] or 0
            hourly_report.append({
                "date": check_date.isoformat(),
                "shift": shift_name or "Legacy shift",
                "hour": check_hour,
                "line": summary_checks.first().production_line.line_name,
                "passed": sum(check.passed_quantity for check in summary_checks),
                "alter": summary_defects,
                "reject": 0,
                "inspected": sum(check.inspected_quantity for check in summary_checks),
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
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Line and quantity are required."}, status=400)

    if quantity < 1:
        return JsonResponse({"error": "Quantity must be at least 1."}, status=400)

    line = profile.assigned_lines.filter(pk=line_id).select_related("factory").first()
    defect = DefectMaster.objects.filter(
        defect_code=payload.get("defect_code"), active=True
    ).first()
    if line is None or defect is None:
        return JsonResponse({"error": "Select an assigned line and active defect."}, status=400)

    today = timezone.localdate()
    shift = FactoryShift.get_active_shift_for_factory(line.factory)
    schedule_status = shift.current_schedule_status() if shift else None
    check_hour = schedule_status["number"] if schedule_status else timezone.localtime().hour
    check = HourlyLineCheck.objects.filter(
        production_line=line,
        check_date=today,
        check_hour=check_hour,
    ).order_by("-created_at").first()
    if check is None:
        assignment = (
            LineAssignment.objects.filter(
                production_line=line,
                start_date__lte=today,
            )
            .exclude(status__in=[LineAssignment.Status.COMPLETED, LineAssignment.Status.CLOSED])
            .select_related("production_order__article")
            .order_by("-start_date")
            .first()
        )
        if assignment is None:
            return JsonResponse({"error": "No active production order is assigned to this line."}, status=400)
        check = HourlyLineCheck.objects.create(
            factory=line.factory,
            production_line=line,
            production_order=assignment.production_order,
            article=assignment.production_order.article,
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
    except (TypeError, ValueError):
        return JsonResponse({"error": "An assigned line is required."}, status=400)

    line = profile.assigned_lines.filter(pk=line_id).select_related("factory").first()
    if line is None:
        return JsonResponse({"error": "Select an assigned production line."}, status=400)

    today = timezone.localdate()
    shift = FactoryShift.get_active_shift_for_factory(line.factory)
    schedule_status = shift.current_schedule_status() if shift else None
    check_hour = schedule_status["number"] if schedule_status else timezone.localtime().hour
    check = HourlyLineCheck.objects.filter(
        production_line=line,
        check_date=today,
        check_hour=check_hour,
    ).order_by("-created_at").first()
    if check is None:
        assignment = (
            LineAssignment.objects.filter(
                production_line=line,
                start_date__lte=today,
            )
            .exclude(status__in=[LineAssignment.Status.COMPLETED, LineAssignment.Status.CLOSED])
            .select_related("production_order__article")
            .order_by("-start_date")
            .first()
        )
        if assignment is None:
            return JsonResponse({"error": "No active production order is assigned to this line."}, status=400)
        check = HourlyLineCheck.objects.create(
            factory=line.factory,
            production_line=line,
            production_order=assignment.production_order,
            article=assignment.production_order.article,
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
    return JsonResponse({
        "inspected_quantity": sum(item.inspected_quantity for item in shift_checks),
        "passed_quantity": sum(item.passed_quantity for item in shift_checks),
        "current_hour_inspected": sum(item.inspected_quantity for item in current_checks),
        "current_hour_passed": sum(item.passed_quantity for item in current_checks),
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
    check.save(update_fields=["passed_quantity", "updated_at"])

    return JsonResponse({
        "repaired_quantity": repaired_quantity,
        "detail_id": detail.pk,
        "tracking_code": detail.tracking_code,
        "marked_at": detail.marked_at.isoformat(),
        "resolved_at": detail.resolved_at.isoformat(),
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
        if not 1 <= standard_hours <= 8 or not 0 <= overtime_hours <= 8:
            from django.contrib import messages

            messages.error(request, "Regular hours must be 1-8 and overtime hours must be 0-8.")
            return redirect("dashboard:shift_management")

        hourly_schedule = []
        for hour in range(1, 9):
            start = request.POST.get(f"hour_{hour}_start") or ""
            end = request.POST.get(f"hour_{hour}_end") or ""
            break_start = request.POST.get(f"hour_{hour}_break_start") or ""
            break_end = request.POST.get(f"hour_{hour}_break_end") or ""
            try:
                if start and end and time.fromisoformat(start) >= time.fromisoformat(end):
                    raise ValueError
                if bool(break_start) != bool(break_end) or (break_start and time.fromisoformat(break_start) >= time.fromisoformat(break_end)):
                    raise ValueError
            except ValueError:
                from django.contrib import messages

                messages.error(request, f"Check the start, end, and break times for Hour {hour}.")
                return redirect("dashboard:shift_management")
            hourly_schedule.append({"hour": hour, "start": start, "end": end, "break_start": break_start, "break_end": break_end})

        breaks_payload = request.POST.get("break_blocks") or "[]"
        try:
            breaks_value = json.loads(breaks_payload) if breaks_payload else []
        except json.JSONDecodeError:
            breaks_value = []

        if isinstance(breaks_value, list):
            break_items = []
            for block in breaks_value:
                if not isinstance(block, dict):
                    continue
                start = (block.get("start") or "").strip()
                end = (block.get("end") or "").strip()
                label = (block.get("label") or "Break").strip() or "Break"
                if start and end:
                    break_items.append({"label": label, "start": start, "end": end})
        else:
            break_items = []

        description = request.POST.get("description") or ""
        if break_items:
            for block in break_items:
                for schedule in hourly_schedule:
                    if str(schedule["hour"]) == str(block.get("hour")):
                        schedule["break_start"] = block["start"]
                        schedule["break_end"] = block["end"]
            description = json.dumps({"breaks": break_items}, ensure_ascii=True)

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
