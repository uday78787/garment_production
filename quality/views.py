from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from master_data.models import Article, Factory, ProductionLine
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


@login_required
def quality_dashboard(request):
    inspections = QualityInspection.objects.select_related("production_line", "article", "factory")
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
    inspections = QualityInspection.objects.select_related(
        "production_line", "production_order", "article", "factory"
    ).order_by("-inspection_date")
    return render(request, "quality/inspection_list.html", {"inspections": inspections})


@login_required
def inspection_create(request):
    factories = Factory.objects.filter(active_status=True).order_by("name")
    production_lines = ProductionLine.objects.filter(active_status=True).select_related("factory").order_by("factory__name", "line_name")
    production_orders = ProductionOrder.objects.select_related("article").order_by("-delivery_date")
    articles = Article.objects.order_by("article_code")
    defects = DefectMaster.objects.filter(active=True).order_by("defect_code")

    if request.method == "POST":
        inspection_number = request.POST.get("inspection_number") or f"QI-{datetime.now():%Y%m%d%H%M%S}"
        inspection = QualityInspection.objects.create(
            inspection_number=inspection_number,
            inspection_date=request.POST.get("inspection_date") or datetime.now().date(),
            factory_id=request.POST.get("factory"),
            production_line_id=request.POST.get("production_line"),
            production_order_id=request.POST.get("production_order"),
            article_id=request.POST.get("article"),
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
    if not request.user.is_superuser:
        messages.error(request, "Only the superuser can add defect definitions.")
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
    checks = HourlyLineCheck.objects.select_related("production_line", "article", "production_order", "factory", "checker").order_by("-check_date", "-check_hour")
    return render(request, "quality/hourly_check_list.html", {"checks": checks})


@login_required
def hourly_check_create(request):
    if request.method == "POST":
        factory_id = request.POST.get("factory")
        line_id = request.POST.get("production_line")
        production_order_id = request.POST.get("production_order")
        article_id = request.POST.get("article")
        defect_code_values = request.POST.getlist("defect_code")
        defect_quantity_values = request.POST.getlist("defect_quantity")
        defect_remarks = request.POST.getlist("defect_remarks")

        check = HourlyLineCheck.objects.create(
            factory_id=factory_id,
            production_line_id=line_id,
            production_order_id=production_order_id,
            article_id=article_id,
            check_date=request.POST.get("check_date") or datetime.now().date(),
            check_hour=int(request.POST.get("check_hour") or 9),
            inspected_quantity=int(request.POST.get("inspected_quantity") or 0),
            passed_quantity=int(request.POST.get("passed_quantity") or 0),
            defective_quantity=int(request.POST.get("defective_quantity") or 0),
            checker=request.user,
            remarks=request.POST.get("remarks") or "",
            status="SUBMITTED",
            created_by=request.user,
        )

        for defect_code, defect_quantity, defect_remark in zip(defect_code_values, defect_quantity_values, defect_remarks):
            if not defect_code or not defect_quantity:
                continue
            defect = DefectMaster.objects.filter(defect_code=defect_code).first()
            if defect is None:
                continue
            image_file = request.FILES.get(f"defect_image_{defect_code}") or request.FILES.get("defect_image")
            HourlyLineCheckDetail.objects.create(
                hourly_check=check,
                defect=defect,
                defect_quantity=int(defect_quantity),
                defect_image=image_file,
                remarks=defect_remark or "",
                created_by=request.user,
            )

        messages.success(request, "Hourly end-of-line quality check saved.")
        return redirect("quality:hourly_check_list")

    return render(
        request,
        "quality/hourly_check_form.html",
        {
            "factories": Factory.objects.filter(active_status=True).order_by("name"),
            "production_lines": ProductionLine.objects.filter(active_status=True).select_related("factory").order_by("factory__name", "line_name"),
            "production_orders": ProductionOrder.objects.select_related("article").order_by("-delivery_date"),
            "articles": Article.objects.order_by("article_code"),
            "defects": DefectMaster.objects.filter(active=True).order_by("defect_code"),
        },
    )


@login_required
def article_defect_bulletin(request):
    inspections = QualityInspection.objects.select_related("production_line", "production_order", "article", "factory")
    return render(
        request,
        "quality/article_defect_bulletin.html",
        {"inspections": inspections.order_by("-inspection_date")[:20], "generated_at": datetime.now()},
    )


@login_required
def export_article_defect_bulletin_excel(request):
    inspections = QualityInspection.objects.select_related("production_line", "production_order", "article", "factory")
    return build_quality_excel_response(inspections, filename_prefix="quality_bulletin")


@login_required
def export_article_defect_bulletin_pdf(request):
    from django.template.loader import render_to_string
    from weasyprint import HTML

    inspections = QualityInspection.objects.select_related("production_line", "production_order", "article", "factory")
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
    holds = QualityHold.objects.select_related("production_order", "line", "article", "created_by").order_by("-hold_date")
    return render(request, "quality/quality_hold_list.html", {"holds": holds})
