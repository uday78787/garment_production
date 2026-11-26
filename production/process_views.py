from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from planning.models import (
    ArticleProcessFlow,
    OrderProcess,
    OrderProcessStep,
    ProcessBatch,
    ProcessQualityCheck,
)

from .process_access import (
    OPERATION_ROLES,
    can_manage_processes,
    require_process_access,
    visible_processes,
)
from .process_forms import (
    OrderProcessSetupForm,
    ProcessBatchForm,
    ProcessQualityCheckForm,
)


def _processes_page_allowed(user):
    profile = getattr(user, "profile", None)
    if user.is_superuser or (profile and profile.is_admin):
        return True
    return bool(profile and profile.role in OPERATION_ROLES)


def _process_for_user(request, process_id):
    if not _processes_page_allowed(request.user):
        raise PermissionDenied("You do not have permission to use department process flows.")
    process = get_object_or_404(visible_processes(request.user), pk=process_id)
    require_process_access(request.user, process.factory_id)
    return process


@login_required
def process_list(request):
    if not _processes_page_allowed(request.user):
        raise PermissionDenied("You do not have permission to use department process flows.")
    processes = visible_processes(request.user).order_by(
        "factory__name", "-created_at"
    )
    return render(
        request,
        "production/process_list.html",
        {
            "processes": processes,
            "can_manage_processes": can_manage_processes(request.user),
        },
    )


@login_required
def process_setup(request):
    if not can_manage_processes(request.user):
        raise PermissionDenied("Only a superuser or Factory Admin can assign an order process.")
    form = OrderProcessSetupForm(
        request.POST or None,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                source_flow = ArticleProcessFlow.objects.get(
                    factory=form.cleaned_data["factory"],
                    article=form.cleaned_data["production_order"].article,
                )
                process = OrderProcess(
                    factory=form.cleaned_data["factory"],
                    production_order=form.cleaned_data["production_order"],
                    source_flow=source_flow,
                    created_by=request.user,
                )
                process.full_clean()
                process.save()
                process.create_steps(source_flow)
        except ArticleProcessFlow.DoesNotExist:
            form.add_error(
                None,
                "The Article Process Flow is no longer available. Review its setup and try again.",
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "The order process flow has been assigned.")
            return redirect("production:process_detail", process_id=process.pk)
    return render(request, "production/process_setup.html", {"form": form})


@login_required
def process_detail(request, process_id):
    process = _process_for_user(request, process_id)
    steps = process.steps.select_related("section").prefetch_related(
        "batches__quality_checks"
    )
    return render(
        request,
        "production/process_detail.html",
        {
            "process": process,
            "steps": steps,
            "can_manage_processes": can_manage_processes(request.user),
        },
    )


@login_required
def process_batch_create(request, process_id, step_id):
    process = _process_for_user(request, process_id)
    step = get_object_or_404(
        OrderProcessStep.objects.select_related("process__production_order", "section"),
        pk=step_id,
        process=process,
    )
    if request.method == "POST":
        with transaction.atomic():
            OrderProcess.objects.select_for_update().get(pk=process.pk)
            step.refresh_from_db()
            form = ProcessBatchForm(request.POST, step=step)
            if form.is_valid():
                batch = form.save(commit=False)
                batch.entered_by = request.user
                try:
                    batch.save()
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))
                else:
                    messages.success(request, "Section output recorded.")
                    return redirect("production:process_detail", process_id=process.pk)
    else:
        form = ProcessBatchForm(step=step)

    return render(
        request,
        "production/process_batch_form.html",
        {"process": process, "step": step, "form": form},
    )


@login_required
def process_quality_check_create(request, process_id, batch_id):
    process = _process_for_user(request, process_id)
    batch = get_object_or_404(
        ProcessBatch.objects.select_related("step__process", "step__section"),
        pk=batch_id,
        step__process=process,
    )
    if request.method == "POST":
        with transaction.atomic():
            OrderProcess.objects.select_for_update().get(pk=process.pk)
            batch.refresh_from_db()
            form = ProcessQualityCheckForm(request.POST, batch=batch)
            if form.is_valid():
                check = form.save(commit=False)
                check.batch = batch
                check.checked_by = request.user
                try:
                    check.save()
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))
                else:
                    messages.success(
                        request,
                        f"Quality result recorded. {check.passed_quantity} pcs passed; "
                        f"{check.rejected_quantity} pcs scrapped.",
                    )
                    return redirect("production:process_detail", process_id=process.pk)
    else:
        form = ProcessQualityCheckForm(batch=batch)

    return render(
        request,
        "production/process_quality_form.html",
        {"process": process, "batch": batch, "form": form},
    )
