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
from datetime import timedelta
from decimal import Decimal
from datetime import datetime
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.mixins import AdminRequiredMixin, admin_required
from master_data.models import Article, Factory
from planning.models import LineAssignment
from planning.services.calculations import forecast_daily_output
from production.models import DailyProductionEntry


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
        context["kpi_total_output"] = sum(output_series)
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
