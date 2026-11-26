from pathlib import PurePosixPath

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import Article


ARTICLE_FILE_FIELDS = (
    "article_image",
    "measurement_bulletin",
    "operation_bulletin",
)


@require_GET
@staff_member_required
def article_file(request, file_path):
    file_filter = Q()
    for field_name in ARTICLE_FILE_FIELDS:
        file_filter |= Q(**{field_name: file_path})
    article = get_object_or_404(Article.objects.filter(file_filter))
    article_file_field = next(
        getattr(article, field_name)
        for field_name in ARTICLE_FILE_FIELDS
        if getattr(article, field_name).name == file_path
    )
    response = FileResponse(
        article_file_field.open("rb"),
        as_attachment=True,
        filename=PurePosixPath(article_file_field.name).name,
        content_type="application/octet-stream",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
