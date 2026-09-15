from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from master_data.models import Article, Factory, FactoryShift, ProductionLine
from planning.models import ProductionOrder

from .calculations import calculate_defect_rate, calculate_dhu, calculate_fpy
from .models import (
    DefectMaster,
    HourlyLineCheck,
    HourlyLineCheckDetail,
    InspectionDefect,
    QualityHold,
    QualityInspection,
)
from .reports import build_quality_excel_response
from .permissions import quality_scope


def _quality_querysets(request):
    factories, production_lines, production_orders, articles = quality_scope(request.user)
    return (
        factories.order_by("name"),
        production_lines.select_related("factory").order_by("factory__name", "line_name"),
        production_orders.select_related("article").order_by("-delivery_date"),
        articles.order_by("article_code"),
    )


@login_required
def quality_dashboard(request):
    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.filter(production_line__in=lines).select_related("production_line", "article", "factory")
    total_inspected = sum(item.inspected_quantity for item in inspections)
    total_passed = sum(item.passed_quantity for item in inspections)
    total_rejected = sum(item.rejected_quantity for item in inspections)
    total_rework = sum(item.rework_quantity for item in inspections)
    total_defects = sum(item.total_defects for item in inspections)

    return render(
        request,
        "quality/dashboard.html",
        {
            "total_inspected": total_inspected,
            "total_passed": total_passed,
            "total_rejected": total_rejected,
            "total_rework": total_rework,
            "total_defects": total_defects,
            "dhu": calculate_dhu(total_defects, total_inspected) if total_inspected else 0,
            "fpy": calculate_fpy(total_passed, total_inspected) if total_inspected else 0,
            "defect_rate": calculate_defect_rate(total_defects, total_inspected) if total_inspected else 0,
            "inspections": inspections.order_by("-inspection_date")[:10],
        },
    )


@login_required
def inspection_list(request):
    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.select_related(
        "production_line", "production_order", "article", "factory"
    ).filter(production_line__in=lines).order_by("-inspection_date")
    return render(request, "quality/inspection_list.html", {"inspections": inspections})


@login_required
def inspection_create(request):
    factories, production_lines, production_orders, articles = _quality_querysets(request)
    defects = DefectMaster.objects.filter(active=True).order_by("defect_code")

    if request.method == "POST":
        inspection_number = request.POST.get("inspection_number") or f"QI-{datetime.now():%Y%m%d%H%M%S}"
        factory_id = request.POST.get("factory")
        line_id = request.POST.get("production_line")
        line = production_lines.filter(pk=line_id, factory_id=factory_id).first()
        order = production_orders.filter(pk=request.POST.get("production_order"), assignments__production_line=line).first()
        article = articles.filter(pk=request.POST.get("article"), orders=order).first() if order else None
        if not line or not order or not article:
            messages.error(request, "Select a factory, line, order, and article within your assigned access.")
            return render(request, "quality/inspection_form.html", {"factories": factories, "production_lines": production_lines, "production_orders": production_orders, "articles": articles, "defects": defects})
        inspection = QualityInspection.objects.create(
            inspection_number=inspection_number,
            inspection_date=request.POST.get("inspection_date") or datetime.now().date(),
            factory=line.factory,
            production_line=line,
            production_order=order,
            article=article,
            buyer=request.POST.get("buyer") or "",
            inspection_type=request.POST.get("inspection_type") or "END_LINE",
            inspector=request.user,
            inspected_quantity=int(request.POST.get("inspected_quantity") or 0),
            passed_quantity=int(request.POST.get("passed_quantity") or 0),
            rejected_quantity=int(request.POST.get("rejected_quantity") or 0),
            rework_quantity=int(request.POST.get("rework_quantity") or 0),
            remarks=request.POST.get("remarks") or "",
            status="SUBMITTED",
            created_by=request.user,
        )

        defect_codes = request.POST.getlist("defect_code")
        defect_quantities = request.POST.getlist("defect_quantity")
        for defect_code, defect_quantity in zip(defect_codes, defect_quantities):
            if not defect_code or not defect_quantity:
                continue
            defect = DefectMaster.objects.filter(defect_code=defect_code).first()
            if defect is None:
                continue
            InspectionDefect.objects.create(
                inspection=inspection,
                defect=defect,
                defect_quantity=int(defect_quantity),
                affected_piece_quantity=int(defect_quantity),
                created_by=request.user,
            )

        messages.success(request, "Inspection created successfully.")
        return render(request, "quality/inspection_success.html", {"inspection": inspection})

    return render(
        request,
        "quality/inspection_form.html",
        {
            "factories": factories,
            "production_lines": production_lines,
            "production_orders": production_orders,
            "articles": articles,
            "defects": defects,
        },
    )


@login_required
def defect_master_list(request):
    defects = DefectMaster.objects.filter(active=True).order_by("defect_code")
    return render(request, "quality/defect_master_list.html", {"defects": defects})


@login_required
def add_defect_master(request):
    profile = getattr(request.user, "profile", None)
    if not request.user.is_superuser and not (profile and profile.is_admin):
        messages.error(request, "Only a superuser or factory admin can add defect definitions.")
        return redirect("quality:defect_master_list")

    if request.method == "POST":
        defect_code = (request.POST.get("defect_code") or "").strip()
        defect_name = (request.POST.get("defect_name") or "").strip()
        category = request.POST.get("category") or DefectMaster.Category.OTHER
        operation = (request.POST.get("operation") or "").strip()
        severity = request.POST.get("severity") or DefectMaster.Severity.MAJOR
        description = (request.POST.get("description") or "").strip()
        department = (request.POST.get("department") or "").strip()

        if defect_code and defect_name:
            DefectMaster.objects.get_or_create(
                defect_code=defect_code,
                defaults={
                    "defect_name": defect_name,
                    "category": category,
                    "operation": operation,
                    "severity": severity,
                    "description": description,
                    "department": department,
                    "active": True,
                },
            )
            messages.success(request, f"Defect {defect_code} saved successfully.")
        else:
            messages.error(request, "Defect code and defect name are required.")
        return redirect("quality:defect_master_list")

    return render(request, "quality/defect_master_form.html", {"categories": DefectMaster.Category, "severities": DefectMaster.Severity})


@login_required
def hourly_check_list(request):
    _, lines, _, _ = quality_scope(request.user)
    checks = HourlyLineCheck.objects.filter(production_line__in=lines).select_related("production_line", "article", "production_order", "factory", "checker").order_by("-check_date", "-check_hour")
    return render(request, "quality/hourly_check_list.html", {"checks": checks})


@login_required
def hourly_check_create(request):
    factories, production_lines, production_orders, articles = _quality_querysets(request)
    profile = getattr(request.user, "profile", None)
    assigned_lines = production_lines.filter(pk__in=profile.assigned_lines.values("pk")) if profile else production_lines.none()
    selected_line = assigned_lines.first() if assigned_lines.count() == 1 else None
    current_shift = FactoryShift.get_active_shift_for_factory(profile.factory) if profile and profile.factory else None
    schedule_status = current_shift.current_schedule_status() if current_shift else None
    if request.method == "POST":
        factory_id = profile.factory_id if profile else None
        line_id = selected_line.pk if selected_line else request.POST.get("production_line")
        production_order_id = request.POST.get("production_order")
        article_id = request.POST.get("article")
        defect_code_values = request.POST.getlist("defect_code")
        defect_quantity_values = request.POST.getlist("defect_quantity")
        repaired_quantity_values = request.POST.getlist("repaired_quantity")
        resolved_values = request.POST.getlist("resolved")

        line = production_lines.filter(pk=line_id, factory_id=factory_id).first()
        order = production_orders.filter(pk=production_order_id, assignments__production_line=line).first()
        article = articles.filter(pk=article_id, orders=order).first() if order else None
        if not line or not order or not article:
            messages.error(request, "Select a factory, line, order, and article within your assigned access.")
            return render(request, "quality/hourly_check_form.html", {"factories": factories, "production_lines": production_lines, "production_orders": production_orders, "articles": articles, "defects": DefectMaster.objects.filter(active=True).order_by("defect_code")})

        try:
            inspected_quantity = int(request.POST.get("inspected_quantity") or 0)
            passed_quantity = int(request.POST.get("passed_quantity") or 0)
        except (TypeError, ValueError):
            messages.error(request, "Inspected and passed quantities must be whole numbers.")
            return render(request, "quality/hourly_check_form.html", {"factories": factories, "production_lines": production_lines, "production_orders": production_orders, "articles": articles, "defects": DefectMaster.objects.filter(active=True).order_by("defect_code")})

        defect_rows = []
        for index, defect_code in enumerate(defect_code_values):
            if not defect_code:
                continue
            try:
                defect_quantity = int(defect_quantity_values[index] or 0)
                repaired_quantity = int(repaired_quantity_values[index] or 0)
            except (IndexError, TypeError, ValueError):
                messages.error(request, "Defect and repair quantities must be whole numbers.")
                return render(request, "quality/hourly_check_form.html", {"factories": factories, "production_lines": production_lines, "production_orders": production_orders, "articles": articles, "defects": DefectMaster.objects.filter(active=True).order_by("defect_code")})
            if defect_quantity < 0 or repaired_quantity < 0 or repaired_quantity > defect_quantity:
                messages.error(request, "Repaired quantity cannot exceed defective quantity.")
                return render(request, "quality/hourly_check_form.html", {"factories": factories, "production_lines": production_lines, "production_orders": production_orders, "articles": articles, "defects": DefectMaster.objects.filter(active=True).order_by("defect_code")})
            is_resolved = index < len(resolved_values) and resolved_values[index] == "on"
            defect_rows.append((defect_code, defect_quantity, repaired_quantity, is_resolved))

        total_defective = sum(row[1] for row in defect_rows)
        if passed_quantity + total_defective > inspected_quantity:
            messages.error(request, "Passed plus defective pieces cannot exceed inspected pieces.")
            return render(request, "quality/hourly_check_form.html", {"factories": factories, "production_lines": production_lines, "production_orders": production_orders, "articles": articles, "defects": DefectMaster.objects.filter(active=True).order_by("defect_code")})

        check = HourlyLineCheck.objects.create(
            factory=line.factory,
            production_line=line,
            production_order=order,
            article=article,
            check_date=timezone.localdate(),
            check_hour=schedule_status["number"] if schedule_status else timezone.localtime().hour,
            inspected_quantity=inspected_quantity,
            passed_quantity=passed_quantity,
            defective_quantity=total_defective,
            checker=request.user,
            status="SUBMITTED",
            created_by=request.user,
        )

        for defect_code, defect_quantity, repaired_quantity, resolved in defect_rows:
            if not defect_quantity:
                continue
            defect = DefectMaster.objects.filter(defect_code=defect_code).first()
            if defect is None:
                continue
            image_file = request.FILES.get(f"defect_image_{defect_code}") or request.FILES.get("defect_image")
            HourlyLineCheckDetail.objects.create(
                hourly_check=check,
                defect=defect,
                defect_quantity=defect_quantity,
                repaired_quantity=repaired_quantity,
                resolved=resolved or repaired_quantity == defect_quantity,
                defect_image=image_file,
                created_by=request.user,
            )

        messages.success(request, "Hourly end-of-line quality check saved.")
        return redirect("quality:hourly_check_list")

    return render(
        request,
        "quality/hourly_check_form.html",
        {
            "factories": factories,
            "production_lines": production_lines,
            "production_orders": production_orders,
            "articles": articles,
            "defects": DefectMaster.objects.filter(active=True).order_by("defect_code"),
            "assigned_lines": assigned_lines,
            "selected_line": selected_line,
        },
    )


@login_required
def article_defect_bulletin(request):
    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.filter(production_line__in=lines).select_related("production_line", "production_order", "article", "factory")
    return render(
        request,
        "quality/article_defect_bulletin.html",
        {"inspections": inspections.order_by("-inspection_date")[:20], "generated_at": datetime.now()},
    )


@login_required
def export_article_defect_bulletin_excel(request):
    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.filter(production_line__in=lines).select_related("production_line", "production_order", "article", "factory")
    return build_quality_excel_response(inspections, filename_prefix="quality_bulletin")


@login_required
def export_article_defect_bulletin_pdf(request):
    from django.template.loader import render_to_string
    from weasyprint import HTML

    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.filter(production_line__in=lines).select_related("production_line", "production_order", "article", "factory")
    html = render_to_string(
        "quality/article_defect_bulletin_pdf.html",
        {"inspections": inspections, "generated_at": datetime.now()},
    )
    pdf_bytes = HTML(string=html).write_pdf()
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f"attachment; filename=article_defect_bulletin_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    )
    return response


@login_required
def quality_hold_list(request):
    _, lines, _, _ = quality_scope(request.user)
    holds = QualityHold.objects.filter(line__in=lines).select_related("production_order", "line", "article", "created_by").order_by("-hold_date")
    return render(request, "quality/quality_hold_list.html", {"holds": holds})
