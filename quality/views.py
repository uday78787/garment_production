from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from master_data.models import Article, Factory, FactoryShift, ProductionLine, Size
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
from .permissions import (
    ensure_quality_access,
    quality_scope,
    user_can_access_quality_line,
)
from .uploads import sanitize_defect_image


DEFECT_IMAGE_CONTENT_TYPES = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _quality_querysets(request):
    factories, production_lines, production_orders, articles = quality_scope(request.user)
    return (
        factories.order_by("name"),
        production_lines.select_related("factory").order_by("factory__name", "line_name"),
        production_orders.select_related("article").order_by("-delivery_date"),
        articles.order_by("article_code"),
    )


@require_GET
@login_required
def defect_image(request, image_path):
    ensure_quality_access(request.user)
    detail = get_object_or_404(
        HourlyLineCheckDetail.objects.select_related(
            "hourly_check__production_line",
        ),
        defect_image=image_path,
    )
    if not user_can_access_quality_line(
        request.user, detail.hourly_check.production_line
    ):
        raise PermissionDenied("You do not have access to this production line.")

    content_type = DEFECT_IMAGE_CONTENT_TYPES.get(
        PurePosixPath(detail.defect_image.name).suffix.lower()
    )
    if content_type is None:
        raise Http404

    if settings.DEBUG:
        response = FileResponse(
            detail.defect_image.open("rb"),
            content_type=content_type,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    response = HttpResponse()
    protected_path = quote(detail.defect_image.name, safe="/")
    response["X-Accel-Redirect"] = f"/_protected_media/{protected_path}"
    response["Content-Type"] = content_type
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


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
        "production_line", "production_order", "article", "factory", "size"
    ).filter(production_line__in=lines).order_by("-inspection_date")
    return render(request, "quality/inspection_list.html", {"inspections": inspections})


@login_required
def inspection_create(request):
    factories, production_lines, production_orders, articles = _quality_querysets(request)
    defects = DefectMaster.objects.filter(active=True).order_by("defect_code")
    sizes = Size.objects.order_by("name")
    context = {
        "factories": factories,
        "production_lines": production_lines,
        "production_orders": production_orders,
        "articles": articles,
        "defects": defects,
        "sizes": sizes,
    }

    if request.method == "POST":
        inspection_number = request.POST.get("inspection_number") or f"QI-{datetime.now():%Y%m%d%H%M%S}"
        factory_id = request.POST.get("factory")
        line_id = request.POST.get("production_line")
        line = production_lines.filter(pk=line_id, factory_id=factory_id).first()
        order = production_orders.filter(pk=request.POST.get("production_order"), assignments__production_line=line).first()
        article = articles.filter(pk=request.POST.get("article"), orders=order).first() if order else None
        size = sizes.filter(pk=request.POST.get("size")).first()
        if not line or not order or not article or not size:
            messages.error(
                request,
                "Select a factory, line, order, article, and size within your assigned access.",
            )
            return render(request, "quality/inspection_form.html", context)
        inspection = QualityInspection.objects.create(
            inspection_number=inspection_number,
            inspection_date=request.POST.get("inspection_date") or datetime.now().date(),
            factory=line.factory,
            production_line=line,
            production_order=order,
            article=article,
            size=size,
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

    return render(request, "quality/inspection_form.html", context)


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
    checks = list(HourlyLineCheck.objects.filter(production_line__in=lines).select_related(
        "production_line", "article", "production_order", "factory", "checker", "size"
    ).order_by("-check_date", "-check_hour"))
    _attach_hour_labels(checks)
    return render(request, "quality/hourly_check_list.html", {"checks": checks})


def _attach_hour_labels(checks):
    checks = list(checks)
    if not checks:
        return checks

    factory_ids = {check.factory_id for check in checks}
    shift_names = {check.shift_name for check in checks if check.shift_name}
    shifts = {
        (shift.factory_id, shift.shift_name): shift
        for shift in FactoryShift.objects.filter(
            factory_id__in=factory_ids,
            shift_name__in=shift_names,
        )
    }
    for check in checks:
        shift = shifts.get((check.factory_id, check.shift_name))
        check.hour_label = (
            shift.scheduled_hour_label(check.check_hour)
            if shift else f"Hour {check.check_hour}"
        )
    return checks


@login_required
def hourly_check_create(request):
    factories, production_lines, production_orders, articles = _quality_querysets(request)
    running_orders = production_orders.filter(
        status__in=[ProductionOrder.Status.PENDING, ProductionOrder.Status.IN_PROGRESS]
    )
    running_articles = articles.filter(orders__in=running_orders).distinct().order_by("article_code")
    profile = getattr(request.user, "profile", None)
    assigned_lines = production_lines.filter(pk__in=profile.assigned_lines.values("pk")) if profile else production_lines.none()
    selected_line = assigned_lines.first() if assigned_lines.count() == 1 else None
    defects = DefectMaster.objects.filter(active=True).order_by("defect_code")
    sizes = Size.objects.order_by("name")
    form_context = {
        "factories": factories,
        "production_lines": production_lines,
        "production_orders": running_orders,
        "articles": running_articles,
        "running_articles": running_articles,
        "defects": defects,
        "sizes": sizes,
        "assigned_lines": assigned_lines,
        "selected_line": selected_line,
    }
    if request.method == "POST":
        factory_id = profile.factory_id if profile else None
        line_id = selected_line.pk if selected_line else request.POST.get("production_line")
        article_id = request.POST.get("article")
        defect_code_values = request.POST.getlist("defect_code")
        defect_quantity_values = request.POST.getlist("defect_quantity")
        repaired_quantity_values = request.POST.getlist("repaired_quantity")
        resolved_values = request.POST.getlist("resolved")

        line = production_lines.filter(pk=line_id, factory_id=factory_id).first()
        article = running_articles.filter(pk=article_id).first()
        order = running_orders.filter(article=article).order_by("delivery_date").first() if article else None
        size = sizes.filter(pk=request.POST.get("size")).first()
        if not line or not order or not article or not size:
            messages.error(request, "Select an assigned line, running article, and valid size.")
            return render(request, "quality/hourly_check_form.html", form_context)

        current_shift = FactoryShift.get_active_shift_for_factory(line.factory)
        schedule_status = current_shift.current_schedule_status() if current_shift else None
        if (
            current_shift is None
            or schedule_status is None
            or not current_shift.has_current_manpower([line])
        ):
            messages.error(
                request,
                "A scheduled current hour and updated IE manpower are required before recording a check.",
            )
            return render(request, "quality/hourly_check_form.html", form_context)
        if schedule_status["on_break"]:
            messages.error(
                request,
                f"Quality checks cannot be recorded during {schedule_status['break_label']}.",
            )
            return render(request, "quality/hourly_check_form.html", form_context)

        try:
            inspected_quantity = int(request.POST.get("inspected_quantity") or 0)
            passed_quantity = int(request.POST.get("passed_quantity") or 0)
        except (TypeError, ValueError):
            messages.error(request, "Inspected and passed quantities must be whole numbers.")
            return render(request, "quality/hourly_check_form.html", form_context)

        defect_rows = []
        for index, defect_code in enumerate(defect_code_values):
            if not defect_code:
                continue
            try:
                defect_quantity = int(defect_quantity_values[index] or 0)
                repaired_quantity = int(repaired_quantity_values[index] or 0)
            except (IndexError, TypeError, ValueError):
                messages.error(request, "Defect and repair quantities must be whole numbers.")
                return render(request, "quality/hourly_check_form.html", form_context)
            if defect_quantity < 0 or repaired_quantity < 0 or repaired_quantity > defect_quantity:
                messages.error(request, "Repaired quantity cannot exceed defective quantity.")
                return render(request, "quality/hourly_check_form.html", form_context)
            is_resolved = index < len(resolved_values) and resolved_values[index] == "on"
            defect_rows.append((defect_code, defect_quantity, repaired_quantity, is_resolved))

        total_defective = sum(row[1] for row in defect_rows)
        if passed_quantity + total_defective > inspected_quantity:
            messages.error(request, "Passed plus defective pieces cannot exceed inspected pieces.")
            return render(request, "quality/hourly_check_form.html", form_context)

        total_repaired = sum(row[2] for row in defect_rows)
        defect_images = {}
        try:
            for defect_code, defect_quantity, _, _ in defect_rows:
                if not defect_quantity or defect_code in defect_images:
                    continue
                image_file = request.FILES.get(
                    f"defect_image_{defect_code}"
                ) or request.FILES.get("defect_image")
                if image_file:
                    defect_images[defect_code] = sanitize_defect_image(image_file)
        except ValidationError:
            messages.error(
                request,
                "Defect images must be valid PNG, JPEG, or WebP images no larger than 10 MB.",
            )
            return render(request, "quality/hourly_check_form.html", form_context)

        check = HourlyLineCheck.objects.create(
            factory=line.factory,
            production_line=line,
            production_order=order,
            article=article,
            size=size,
            check_date=timezone.localdate(),
            check_hour=schedule_status["number"],
            shift_name=current_shift.shift_name,
            inspected_quantity=inspected_quantity,
            passed_quantity=passed_quantity + total_repaired,
            defective_quantity=max(total_defective - total_repaired, 0),
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
            HourlyLineCheckDetail.objects.create(
                hourly_check=check,
                defect=defect,
                defect_quantity=defect_quantity,
                repaired_quantity=repaired_quantity,
                resolved=resolved or repaired_quantity == defect_quantity,
                defect_image=(
                    SimpleUploadedFile(
                        "defect.png",
                        defect_images[defect_code],
                        content_type="image/png",
                    )
                    if defect_code in defect_images
                    else None
                ),
                created_by=request.user,
            )

        messages.success(request, "Hourly end-of-line quality check saved.")
        return redirect("quality:hourly_check_list")

    return render(request, "quality/hourly_check_form.html", form_context)


@login_required
def article_defect_bulletin(request):
    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.filter(production_line__in=lines).select_related(
        "production_line", "production_order", "article", "factory", "size"
    )
    return render(
        request,
        "quality/article_defect_bulletin.html",
        {"inspections": inspections.order_by("-inspection_date")[:20], "generated_at": datetime.now()},
    )


@login_required
def export_article_defect_bulletin_excel(request):
    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.filter(production_line__in=lines).select_related(
        "production_line", "production_order", "article", "factory", "size"
    )
    return build_quality_excel_response(inspections, filename_prefix="quality_bulletin")


@login_required
def export_article_defect_bulletin_pdf(request):
    from django.template.loader import render_to_string
    from weasyprint import HTML

    _, lines, _, _ = quality_scope(request.user)
    inspections = QualityInspection.objects.filter(production_line__in=lines).select_related(
        "production_line", "production_order", "article", "factory", "size"
    )
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


@login_required
def defect_database(request):
    """Filtered defect register for superusers and factory admins."""
    profile = getattr(request.user, "profile", None)
    if not request.user.is_superuser and not (profile and profile.is_admin):
        raise PermissionDenied("Only administrators can access the defect database.")
    _, lines, _, articles = quality_scope(request.user)
    defects = HourlyLineCheckDetail.objects.filter(
        hourly_check__production_line__in=lines,
    ).select_related(
        "defect",
        "hourly_check__article",
        "hourly_check__size",
        "hourly_check__size",
        "hourly_check__production_line",
        "hourly_check__factory",
        "created_by",
    ).order_by("-hourly_check__check_date", "-hourly_check__check_hour", "-marked_at")

    article_id = request.GET.get("article")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    time_from = request.GET.get("time_from")
    time_to = request.GET.get("time_to")
    defect_code = request.GET.get("defect_type")
    hour = request.GET.get("hour")
    line_id = request.GET.get("line")
    user_id = request.GET.get("user")
    tracking_code = (request.GET.get("tracking_code") or "").strip()

    if article_id and article_id.isdigit():
        defects = defects.filter(hourly_check__article_id=article_id)
    if date_from:
        defects = defects.filter(hourly_check__check_date__gte=date_from)
    if date_to:
        defects = defects.filter(hourly_check__check_date__lte=date_to)
    if time_from:
        defects = defects.filter(marked_at__time__gte=time_from)
    if time_to:
        defects = defects.filter(marked_at__time__lte=time_to)
    if defect_code:
        defects = defects.filter(defect__defect_code=defect_code)
    if hour and hour.isdigit():
        defects = defects.filter(hourly_check__check_hour=int(hour))
    if line_id and line_id.isdigit():
        defects = defects.filter(hourly_check__production_line_id=int(line_id))
    if user_id and user_id.isdigit():
        defects = defects.filter(created_by_id=int(user_id))
    if tracking_code:
        defects = defects.filter(tracking_code__icontains=tracking_code)
    defect_records = list(defects)
    _attach_hour_labels(record.hourly_check for record in defect_records)

    user_model = get_user_model()
    context = {
        "defect_records": defect_records,
        "articles": articles.order_by("article_code"),
        "lines": lines.order_by("line_name"),
        "defect_types": DefectMaster.objects.filter(active=True).order_by("defect_code"),
        "users": user_model.objects.filter(
            pk__in=HourlyLineCheckDetail.objects.filter(hourly_check__production_line__in=lines).values("created_by_id")
        ).order_by("username"),
        "filters": request.GET,
    }
    return render(request, "quality/defect_database.html", context)


@login_required
def export_defect_database_excel(request):
    profile = getattr(request.user, "profile", None)
    if not request.user.is_superuser and not (profile and profile.is_admin):
        raise PermissionDenied("Only administrators can export the defect database.")
    _, lines, _, _ = quality_scope(request.user)
    records = HourlyLineCheckDetail.objects.filter(
        hourly_check__production_line__in=lines,
    ).select_related(
        "defect",
        "hourly_check__article",
        "hourly_check__size",
        "hourly_check__size",
        "hourly_check__production_line",
        "created_by",
    ).order_by("-hourly_check__check_date", "-hourly_check__check_hour", "-marked_at")
    filters = request.GET
    if filters.get("article", "").isdigit():
        records = records.filter(hourly_check__article_id=filters["article"])
    if filters.get("date_from"):
        records = records.filter(hourly_check__check_date__gte=filters["date_from"])
    if filters.get("date_to"):
        records = records.filter(hourly_check__check_date__lte=filters["date_to"])
    if filters.get("time_from"):
        records = records.filter(marked_at__time__gte=filters["time_from"])
    if filters.get("time_to"):
        records = records.filter(marked_at__time__lte=filters["time_to"])
    if filters.get("defect_type"):
        records = records.filter(defect__defect_code=filters["defect_type"])
    if filters.get("hour", "").isdigit():
        records = records.filter(hourly_check__check_hour=filters["hour"])
    if filters.get("line", "").isdigit():
        records = records.filter(hourly_check__production_line_id=filters["line"])
    if filters.get("user", "").isdigit():
        records = records.filter(created_by_id=filters["user"])
    if filters.get("tracking_code"):
        records = records.filter(tracking_code__icontains=filters["tracking_code"])

    import pandas as pd

    records = list(records)
    _attach_hour_labels(record.hourly_check for record in records)
    rows = [{
        "Defect Code": record.tracking_code,
        "Article": record.hourly_check.article.article_code,
        "Size": record.hourly_check.size.name if record.hourly_check.size_id else "",
        "Date": record.hourly_check.check_date,
        "Time": timezone.localtime(record.marked_at).strftime("%H:%M:%S"),
        "Defect Type": record.defect.defect_code,
        "Defect Name": record.defect.defect_name,
        "Hour": record.hourly_check.hour_label,
        "Line": record.hourly_check.production_line.line_name,
        "User ID": record.created_by.username if record.created_by else "",
        "Defect Qty": record.defect_quantity,
        "Repaired Qty": record.repaired_quantity,
        "Status": "Resolved" if record.resolved else "Pending",
    } for record in records]
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f"attachment; filename=defect_database_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    with pd.ExcelWriter(response, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Defect Database")
    return response
