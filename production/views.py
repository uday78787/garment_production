"""
production/views.py

Daily production entry CRUD, protected by:
- LoginRequiredMixin: must be logged in.
- Line-level filtering: Production Users only see/enter entries for their
  assigned lines; Admins see everything.
- common.utils.write_audit_log(): every create/update is recorded.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from common.models import AuditLog
from common.utils import write_audit_log

from .forms import DailyProductionEntryForm
from .models import DailyProductionEntry
from django.core.exceptions import ValidationError
from datetime import date
from master_data.models import Article, ProductionLine
from planning.models import LineAssignment


class ScopedQuerysetMixin(LoginRequiredMixin):
    """Restricts the queryset to the user's assigned lines and factory unless Admin."""

    def get_base_queryset(self):
        qs = DailyProductionEntry.objects.select_related(
            "assignment__production_line", "assignment__production_order__article"
        )
        user = self.request.user
        selected_factory_id = self.request.session.get("selected_factory_id")

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


class DailyProductionEntryListView(ScopedQuerysetMixin, ListView):
    model = DailyProductionEntry
    template_name = "production/entry_list.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        qs = self.get_base_queryset().order_by(
            "-production_date", "assignment__production_line__line_name", "pk"
        )
        line_id = self.request.GET.get("line")
        if line_id:
            qs = qs.filter(assignment__production_line_id=line_id)

        article_id = self.request.GET.get("article")
        if article_id:
            qs = qs.filter(assignment__production_order__article_id=article_id)

        date_value = self.request.GET.get("date")
        if date_value:
            try:
                qs = qs.filter(production_date=date.fromisoformat(date_value))
            except ValueError:
                pass

        month_value = self.request.GET.get("month")
        if month_value and not date_value:
            try:
                month_date = date.fromisoformat(f"{month_value}-01")
                qs = qs.filter(
                    production_date__year=month_date.year,
                    production_date__month=month_date.month,
                )
            except ValueError:
                pass

        search = self.request.GET.get("q")
        if search:
            qs = qs.filter(
                Q(assignment__production_order__order_number__icontains=search)
                | Q(assignment__production_line__line_name__icontains=search)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_factory_id = self.request.session.get("selected_factory_id")
        visible_assignments = LineAssignment.objects.filter(
            pk__in=self.get_base_queryset().values("assignment_id")
        )
        context["articles"] = Article.objects.filter(
            orders__assignments__in=visible_assignments
        ).distinct().order_by("article_code")
        line_ids = self.get_base_queryset().values_list(
            "assignment__production_line_id", flat=True
        ).distinct()
        context["lines"] = ProductionLine.objects.filter(pk__in=line_ids).order_by("line_name")
        context["selected_article_id"] = self.request.GET.get("article") or ""
        context["selected_line_id"] = self.request.GET.get("line") or ""
        context["selected_date"] = self.request.GET.get("date") or ""
        context["selected_month"] = self.request.GET.get("month") or ""
        context["selected_factory_id"] = selected_factory_id
        return context


class DailyProductionEntryCreateView(ScopedQuerysetMixin, CreateView):
    model = DailyProductionEntry
    form_class = DailyProductionEntryForm
    template_name = "production/entry_form.html"
    success_url = reverse_lazy("production:entry_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["selected_factory_id"] = self.request.session.get("selected_factory_id")
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        # Save with model-level validation guarded: catch ValidationError and
        # return the form with an error message so the user sees a popup.
        try:
            self.object = form.save()
        except ValidationError as exc:
            # attach errors to the form and show a popup message
            form.add_error(None, exc)
            messages.error(self.request, "Cannot save entry: " + str(exc))
            return self.form_invalid(form)
        response = super().form_valid(form)
        write_audit_log(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            instance=self.object,
            new_value={
                "actual_manpower": self.object.actual_manpower,
                "working_minutes": self.object.working_minutes,
                "actual_output": self.object.actual_output,
            },
        )
        messages.success(self.request, "Production entry saved.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx.get("form")
        assignment = None
        if form is not None:
            if form.is_bound:
                assignment_id = form.data.get("assignment")
                if assignment_id:
                    assignment = LineAssignment.objects.filter(
                        pk=assignment_id,
                        pk__in=self.get_base_queryset().values("assignment_id"),
                    ).select_related("production_order").first()

        if assignment is not None:
            order = assignment.production_order
            ctx["order_remaining"] = order.remaining_quantity
            ctx["pending_production"] = order.pending_production
            ctx["actual_produced"] = order.total_produced_quantity
            ctx["order_total"] = order.order_quantity
        return ctx


class DailyProductionEntryUpdateView(ScopedQuerysetMixin, UpdateView):
    model = DailyProductionEntry
    form_class = DailyProductionEntryForm
    template_name = "production/entry_form.html"
    success_url = reverse_lazy("production:entry_list")

    def get_queryset(self):
        return self.get_base_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["selected_factory_id"] = self.request.session.get("selected_factory_id")
        return kwargs

    def form_valid(self, form):
        old_value = {
            "actual_manpower": self.get_object().actual_manpower,
            "working_minutes": self.get_object().working_minutes,
            "actual_output": self.get_object().actual_output,
        }
        form.instance.updated_by = self.request.user
        try:
            self.object = form.save()
        except ValidationError as exc:
            form.add_error(None, exc)
            messages.error(self.request, "Cannot update entry: " + str(exc))
            return self.form_invalid(form)
        response = super().form_valid(form)
        write_audit_log(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            instance=self.object,
            old_value=old_value,
            new_value={
                "actual_manpower": self.object.actual_manpower,
                "working_minutes": self.object.working_minutes,
                "actual_output": self.object.actual_output,
            },
        )
        messages.success(self.request, "Production entry updated.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Show remaining order quantity to the user when available
        form = ctx.get("form")
        assignment = None
        if form is not None:
            # Try to find assignment from POSTed data or the instance
            if form.is_bound:
                assignment_id = form.data.get("assignment")
                if assignment_id:
                    assignment = LineAssignment.objects.filter(
                        pk=assignment_id,
                        pk__in=self.get_base_queryset().values("assignment_id"),
                    ).select_related("production_order").first()
            if assignment is None and getattr(form.instance, "assignment", None):
                assignment = form.instance.assignment

        if assignment is not None:
            order = assignment.production_order
            ctx["order_remaining"] = order.remaining_quantity
            ctx["pending_production"] = order.pending_production
            ctx["actual_produced"] = order.total_produced_quantity
            ctx["order_total"] = order.order_quantity
        return ctx


class DailyProductionEntryDeleteView(ScopedQuerysetMixin, DeleteView):
    model = DailyProductionEntry
    template_name = "production/entry_confirm_delete.html"
    success_url = reverse_lazy("production:entry_list")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only a Superuser can delete production entries.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        entry = self.get_object()
        old_value = {
            "production_date": str(entry.production_date),
            "actual_output": entry.actual_output,
        }
        write_audit_log(
            user=self.request.user,
            action=AuditLog.Action.DELETE,
            instance=entry,
            old_value=old_value,
        )
        response = super().form_valid(form)
        messages.success(self.request, "Production entry deleted.")
        return response
