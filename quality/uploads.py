from io import BytesIO

from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_DEFECT_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DEFECT_IMAGE_PIXELS = 20_000_000
ALLOWED_DEFECT_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def sanitize_defect_image(uploaded_file):
    if uploaded_file.size > MAX_DEFECT_IMAGE_BYTES:
        raise ValidationError("Defect images must be no larger than 10 MB.")

    try:
        with Image.open(uploaded_file) as image:
            if image.format not in ALLOWED_DEFECT_IMAGE_FORMATS:
                raise ValidationError("Upload a valid PNG, JPEG, or WebP defect image.")
            if image.width * image.height > MAX_DEFECT_IMAGE_PIXELS:
                raise ValidationError("Defect images exceed the maximum pixel dimensions.")
            image.verify()

        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image = ImageOps.exif_transpose(image)
            mode = "RGBA" if "A" in image.getbands() else "RGB"
            output = BytesIO()
            image.convert(mode).save(output, format="PNG", optimize=True)
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as error:
        raise ValidationError("Upload a valid PNG, JPEG, or WebP defect image.") from error
    finally:
        uploaded_file.seek(0)

    image_bytes = output.getvalue()
    if len(image_bytes) > MAX_DEFECT_IMAGE_BYTES:
        raise ValidationError("Processed defect images must be no larger than 10 MB.")
    return image_bytes
