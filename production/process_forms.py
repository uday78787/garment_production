from django import forms
from django.db.models import Q

from master_data.models import Factory
from planning.models import (
    ArticleProcessFlow,
    ProcessBatch,
    ProcessQualityCheck,
    ProductionOrder,
)

from .process_access import manageable_factories


class OrderProcessSetupForm(forms.Form):
    factory = forms.ModelChoiceField(
        queryset=Factory.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    production_order = forms.ModelChoiceField(
        queryset=ProductionOrder.objects.none(),
        label="Production Order",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        factories = manageable_factories(user)
        self.fields["factory"].queryset = factories.order_by("name")

        orders = ProductionOrder.objects.filter(process_flow__isnull=True)
        if not user.is_superuser:
            orders = orders.filter(
                Q(assignments__isnull=True)
                | Q(assignments__production_line__factory__in=factories)
            )
        self.fields["production_order"].queryset = orders.select_related(
            "article"
        ).distinct().order_by("order_number")

    def clean(self):
        cleaned = super().clean()
        factory = cleaned.get("factory")
        order = cleaned.get("production_order")
        if factory and order:
            assigned_factory_ids = set(
                order.assignments.values_list("production_line__factory_id", flat=True)
            )
            if assigned_factory_ids and assigned_factory_ids != {factory.pk}:
                self.add_error(
                    "factory",
                    "The selected factory must match all production lines assigned to this order.",
                )
            if not ArticleProcessFlow.objects.filter(
                factory=factory,
                article=order.article,
                steps__isnull=False,
            ).exists():
                self.add_error(
                    None,
                    "Create an Article Process Flow with at least one operation for this factory first.",
                )
        return cleaned


class ProcessBatchForm(forms.ModelForm):
    class Meta:
        model = ProcessBatch
        fields = [
            "input_quantity",
            "output_quantity",
            "actual_manpower",
            "working_minutes",
            "notes",
        ]
        widgets = {
            "input_quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "output_quantity": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "actual_manpower": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "working_minutes": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, step, **kwargs):
        self.step = step
        super().__init__(*args, **kwargs)
        self.fields["actual_manpower"].required = True
        self.fields["working_minutes"].required = True
        available = step.available_input_quantity
        self.fields["input_quantity"].help_text = (
            f"Maximum available from the previous section: {available} pcs."
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

        for field_schema in step.field_schema:
            field_name = f"field_{field_schema['key']}"
            required = field_schema.get("required", False)
            label = field_schema["label"]
            field_type = field_schema["type"]
            if field_type == "NUMBER":
                field = forms.DecimalField(required=required, label=label)
            elif field_type == "DATE":
                field = forms.DateField(
                    required=required,
                    label=label,
                    widget=forms.DateInput(attrs={"type": "date"}),
                )
            elif field_type == "YES_NO":
                field = forms.ChoiceField(
                    required=required,
                    label=label,
                    choices=(("", "---------"), ("yes", "Yes"), ("no", "No")),
                )
            elif field_type == "CHOICE":
                field = forms.ChoiceField(
                    required=required,
                    label=label,
                    choices=[("", "---------")]
                    + [(choice, choice) for choice in field_schema.get("choices", [])],
                )
            else:
                field = forms.CharField(required=required, label=label)
            field.widget.attrs.setdefault("class", "form-control")
            self.fields[field_name] = field

    def clean(self):
        cleaned = super().clean()
        input_quantity = cleaned.get("input_quantity")
        output_quantity = cleaned.get("output_quantity")
        if input_quantity is not None and input_quantity > self.step.available_input_quantity:
            self.add_error(
                "input_quantity",
                f"Only {self.step.available_input_quantity} pcs are available from the previous section.",
            )
        if input_quantity is not None and output_quantity is not None and output_quantity > input_quantity:
            self.add_error("output_quantity", "Output quantity cannot exceed input quantity.")
        return cleaned

    def save(self, commit=True):
        batch = super().save(commit=False)
        batch.step = self.step
        batch.field_values = {}
        for field_schema in self.step.field_schema:
            field_name = f"field_{field_schema['key']}"
            value = self.cleaned_data.get(field_name)
            if value is not None:
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                elif field_schema["type"] == "NUMBER":
                    value = str(value)
                batch.field_values[field_schema["key"]] = value
        if commit:
            batch.save()
            self.save_m2m()
        return batch


class ProcessQualityCheckForm(forms.ModelForm):
    class Meta:
        model = ProcessQualityCheck
        fields = ["inspected_quantity", "passed_quantity", "remarks"]
        widgets = {
            "inspected_quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "passed_quantity": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "remarks": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, batch, **kwargs):
        self.batch = batch
        super().__init__(*args, **kwargs)
        self.fields["inspected_quantity"].help_text = (
            f"Uninspected output remaining in this batch: {batch.uninspected_quantity} pcs."
        )

    def clean(self):
        cleaned = super().clean()
        inspected = cleaned.get("inspected_quantity")
        passed = cleaned.get("passed_quantity")
        if inspected is not None and inspected > self.batch.uninspected_quantity:
            self.add_error(
                "inspected_quantity",
                f"Only {self.batch.uninspected_quantity} uninspected output pcs remain.",
            )
        if inspected is not None and passed is not None and passed > inspected:
            self.add_error("passed_quantity", "Passed quantity cannot exceed inspected quantity.")
        return cleaned
