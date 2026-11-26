from django.core.exceptions import ValidationError


ARTICLE_FILE_MAX_BYTES = 10 * 1024 * 1024


def validate_article_file_size(uploaded_file):
    if uploaded_file.size > ARTICLE_FILE_MAX_BYTES:
        raise ValidationError("Article images and documents must be no larger than 10 MB.")
