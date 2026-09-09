from decimal import Decimal

from django import forms

from .models import LineAssignment


class LineAssignmentAdminForm(forms.ModelForm):
    start_efficiency = forms.DecimalField(
        label="Start efficiency (%)",
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        help_text="Starting efficiency on day 1, entered as a percentage.",
    )
    daily_efficiency_growth = forms.DecimalField(
        label="Daily efficiency growth (%)",
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        help_text="Daily efficiency increase as a percentage.",
    )
    max_efficiency = forms.DecimalField(
        label="Max efficiency (%)",
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        help_text="Maximum efficiency ceiling, entered as a percentage.",
    )
    forecast_efficiency_override = forms.DecimalField(
        label="Forecast efficiency override (%)",
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        required=False,
        help_text="Optional percentage from 0 to 100.",
    )

    class Meta:
        model = LineAssignment
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in (
            "start_efficiency",
            "daily_efficiency_growth",
            "max_efficiency",
            "forecast_efficiency_override",
        ):
            value = self.initial.get(field_name)
            if value is None and self.instance is not None:
                value = getattr(self.instance, field_name, None)
            if value is not None:
                self.initial[field_name] = Decimal(str(value)) * 100

    def clean(self):
        cleaned_data = super().clean()
        # Convert all percentage inputs (0-100) back to decimal format (0-1) for storage
        for field_name in (
            "start_efficiency",
            "daily_efficiency_growth",
            "max_efficiency",
            "forecast_efficiency_override",
        ):
            value = cleaned_data.get(field_name)
            if value is not None:
                cleaned_data[field_name] = value / Decimal("100")
        return cleaned_data