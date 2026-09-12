"""
production/forms.py

ModelForm used for daily production entry, restricted server-side to the
assignments a given user is allowed to enter data for (see the view, which
passes `user` into the form and filters the `assignment` queryset).
"""

from datetime import datetime
from decimal import Decimal

from django import forms

from planning.models import LineAssignment
from .models import DailyProductionEntry


class DailyProductionEntryForm(forms.ModelForm):
    assignment = forms.ModelChoiceField(
        queryset=LineAssignment.objects.none(),
        label="Article Search / Assignment",
        widget=forms.Select(attrs={"class": "form-select", "data-searchable-assignment": "true"}),
    )

    planned_efficiency = forms.DecimalField(
        label="Planned efficiency (%)",
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        help_text="Supervisor-entered planned efficiency for this day.",
    )

    working_minutes = forms.IntegerField(
        label="Working minutes per operator",
        min_value=0,
        required=False,
        help_text="Use the actual minutes spent on this article (for example, 60 or 420).",
    )

    defect_pieces = forms.IntegerField(
        label="Defect pieces found",
        min_value=0,
        required=False,
        help_text="Number of defective pieces identified during end-of-line inspection for this output.",
    )

    class Meta:
        model = DailyProductionEntry
        fields = [
            "assignment",
            "production_date",
            "actual_manpower",
            "working_minutes",
            "actual_output",
            "defect_pieces",
            "planned_efficiency",
            "remarks",
        ]
        widgets = {
            "production_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "actual_manpower": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "working_minutes": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "actual_output": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "defect_pieces": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "planned_efficiency": forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100}),
            "remarks": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(self, *args, user=None, selected_factory_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial.setdefault(
                "planned_efficiency",
                self.instance.planned_efficiency * Decimal("100"),
            )

        queryset = LineAssignment.objects.select_related(
            "production_order__article", "production_line"
        ).all()

        selected_factory = selected_factory_id
        if user is not None and hasattr(user, "profile"):
            if user.is_superuser:
                if selected_factory is None:
                    selected_factory = user.profile.factory_id if getattr(user.profile, "factory_id", None) else None
            elif user.profile.is_admin:
                selected_factory = selected_factory or user.profile.factory_id
                queryset = queryset.filter(
                    production_line__factory__in=user.profile.accessible_factories.all()
                )
            else:
                queryset = queryset.filter(production_line__in=user.profile.assigned_lines.all())
                if user.profile.factory_id:
                    queryset = queryset.filter(production_line__factory_id=user.profile.factory_id)

        if selected_factory:
            queryset = queryset.filter(production_line__factory_id=selected_factory)

        available_assignment_ids = []
        for assignment in queryset:
            if assignment.production_order.pending_production > 0:
                available_assignment_ids.append(assignment.pk)
            elif self.instance and self.instance.pk and assignment.pk == self.instance.assignment_id:
                available_assignment_ids.append(assignment.pk)

        self.fields["assignment"].queryset = queryset.filter(pk__in=available_assignment_ids)
        self.fields["assignment"].label_from_instance = lambda obj: (
            f"{obj.production_order.article.article_code} | {obj.production_order.article.article_name} | "
            f"{obj.production_line} | {obj.production_order.order_number}"
        )

    def clean_production_date(self):
        production_date = self.cleaned_data.get("production_date")
        assignment = self.cleaned_data.get("assignment") or getattr(self.instance, "assignment", None)
        if assignment and production_date and assignment.start_date:
            start_date = assignment.start_date
            if isinstance(start_date, str):
                start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            if production_date < start_date:
                raise forms.ValidationError(
                    "Production date cannot be before the assignment's start date."
                )
        return production_date

    def clean_actual_manpower(self):
        value = self.cleaned_data.get("actual_manpower")
        if value is None:
            return value
        assignment = self.cleaned_data.get("assignment") or getattr(self.instance, "assignment", None)
        if assignment and value > assignment.production_line.capacity_manpower:
            raise forms.ValidationError(
                "Actual manpower exceeds the line's capacity manpower."
            )
        return value

    def clean_planned_efficiency(self):
        value = self.cleaned_data["planned_efficiency"]
        return value / Decimal("100")

    def clean_working_minutes(self):
        value = self.cleaned_data.get("working_minutes")
        assignment = self.cleaned_data.get("assignment") or getattr(self.instance, "assignment", None)
        if value is None:
            return assignment.working_minutes_per_operator if assignment else 480
        if assignment and value > assignment.working_minutes_per_operator:
            raise forms.ValidationError(
                "Working minutes cannot exceed the assignment's configured "
                f"daily minutes ({assignment.working_minutes_per_operator})."
            )
        return value

    def clean_defect_pieces(self):
        value = self.cleaned_data.get("defect_pieces")
        if value is None:
            return 0
        actual_output = self.cleaned_data.get("actual_output")
        if actual_output is not None and value > actual_output:
            raise forms.ValidationError("Defective pieces cannot exceed checked output pieces.")
        return value

    def clean_actual_output(self):
        """Ensure the entered actual_output does not push the order past its
        total `order_quantity`. This accounts for existing entries when
        updating an entry.
        """
        value = self.cleaned_data["actual_output"]
        assignment = self.cleaned_data.get("assignment") or getattr(self.instance, "assignment", None)
        if not assignment:
            return value

        order = assignment.production_order

        previous_value = 0
        if self.instance and self.instance.pk:
            try:
                previous_value = type(self.instance).objects.get(pk=self.instance.pk).actual_output
            except type(self.instance).DoesNotExist:
                previous_value = 0

            if value == previous_value:
                return value

        produced_so_far = order.total_produced_quantity
        if self.instance and self.instance.pk:
            produced_so_far -= previous_value

        remaining = max(order.order_quantity - produced_so_far, 0)
        if value > remaining:
            raise forms.ValidationError(
                f"You cannot produce more than the total order quantity of {order.order_quantity} pcs. "
                f"Actual production is {order.total_produced_quantity} pcs, so pending production is {order.pending_production} pcs."
            )

        return value
