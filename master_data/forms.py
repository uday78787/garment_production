from django import forms
from django.templatetags.static import static
from django.utils.html import format_html

from .models import Article, Size


ARTICLE_BULLETIN_SAMPLES = {
    "measurement_bulletin": (
        "Measurement bulletin",
        "templates/article_bulletins/measurement_bulletin_sample.csv",
    ),
    "operation_bulletin": (
        "Operation bulletin",
        "templates/article_bulletins/operation_bulletin_sample.csv",
    ),
}


class ArticleAdminForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["size"].queryset = Size.objects.order_by("name")
        for field_name, (label, sample_path) in ARTICLE_BULLETIN_SAMPLES.items():
            field = self.fields[field_name]
            field.help_text = format_html(
                "{} Structured bulletin rows, manual editing, and CSV import/export are "
                "available from Article Bulletin Settings. Sample format: "
                "<a href=\"{}\" download>Download {} CSV</a>.",
                field.help_text,
                static(sample_path),
                label,
            )
