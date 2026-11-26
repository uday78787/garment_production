import csv
import io
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.text import slugify

from master_data.models import ArticleMeasurementPoint, Size
from planning.models import ArticleProcessFlow, ArticleProcessStep, FactorySection
from quality.models import ArticleDefectBulletin


class BulletinImportError(ValueError):
    pass


EXPORT_FIELDS = {
    "measurement": (
        "POM",
        "Description",
        "Size",
        "Target Measurement",
        "Tolerance (+/-)",
        "Unit",
        "Remark",
    ),
    "operation": (
        "Article Code",
        "Article Name",
        "PO No.",
        "Sequence",
        "Department/Section",
        "Operation",
        "Operation Description",
        "Machine/Equipment",
        "Attachment/Foot",
        "Standard Time (Minutes)",
        "Quality Checkpoint",
        "Skill Level",
        "Remarks",
    ),
    "defect": (
        "Article Code",
        "Article Name",
        "PO No.",
        "Defect Code",
        "Defect Name",
        "Category",
        "Operation",
        "Description",
        "Severity",
        "Identification Method",
        "Prevention / Corrective Action",
        "Reference Photo",
        "Remarks",
    ),
}


def _normalize_header(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())


def _read_csv(upload):
    if upload.size > 5 * 1024 * 1024:
        raise BulletinImportError("CSV file must be 5 MB or smaller.")
    try:
        content = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BulletinImportError("CSV file must use UTF-8 encoding.") from exc

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise BulletinImportError("CSV file must include a header row.")
    headers = {_normalize_header(header): header for header in reader.fieldnames}
    rows = []
    for row_number, raw_row in enumerate(reader, start=2):
        row = {
            _normalize_header(key): (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        if any(row.values()):
            rows.append((row_number, row))
    if not rows:
        raise BulletinImportError("CSV file contains no data rows.")
    return headers, rows


def _value(row, *headers):
    for header in headers:
        normalized = _normalize_header(header)
        if normalized in row:
            return row[normalized]
    return ""


def _require_columns(headers, *columns):
    missing = [
        column
        for column in columns
        if not any(_normalize_header(alias) in headers for alias in column.split("|"))
    ]
    if missing:
        raise BulletinImportError(
            "Missing required CSV columns: " + ", ".join(missing) + "."
        )


def _validate_article(row, article, row_number):
    code = _value(row, "Article Code")
    name = _value(row, "Article Name")
    if code and code.casefold() != article.article_code.casefold():
        raise BulletinImportError(
            f"Row {row_number}: Article Code does not match {article.article_code}."
        )
    if name and name.casefold() != article.article_name.casefold():
        raise BulletinImportError(
            f"Row {row_number}: Article Name does not match {article.article_name}."
        )


def _decimal(
    value,
    label,
    row_number,
    *,
    required=False,
    non_negative=False,
    max_digits=None,
    decimal_places=None,
):
    if not value:
        if required:
            raise BulletinImportError(f"Row {row_number}: {label} is required.")
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise BulletinImportError(
            f"Row {row_number}: {label} must be a number."
        ) from exc
    if not number.is_finite():
        raise BulletinImportError(f"Row {row_number}: {label} must be finite.")
    if non_negative and number < 0:
        raise BulletinImportError(f"Row {row_number}: {label} cannot be negative.")
    if max_digits is not None and decimal_places is not None:
        try:
            quantized = number.quantize(Decimal(1).scaleb(-decimal_places))
        except InvalidOperation as exc:
            raise BulletinImportError(
                f"Row {row_number}: {label} exceeds the supported precision."
            ) from exc
        if quantized != number or abs(number) >= Decimal(10) ** (
            max_digits - decimal_places
        ):
            raise BulletinImportError(
                f"Row {row_number}: {label} supports at most "
                f"{max_digits - decimal_places} digits before and "
                f"{decimal_places} digits after the decimal point."
            )
    return number


def _text(value, label, row_number, max_length):
    if len(value) > max_length:
        raise BulletinImportError(
            f"Row {row_number}: {label} must be {max_length} characters or fewer."
        )
    return value


def _import_measurements(upload, article, factory):
    headers, rows = _read_csv(upload)
    _require_columns(
        headers,
        "POM|Measurement Point",
        "Target Measurement",
    )
    sizes = {size.name.casefold(): size for size in Size.objects.all()}
    entries = []
    seen = set()
    for row_number, row in rows:
        _validate_article(row, article, row_number)
        pom = _text(_value(row, "POM", "Measurement Point"), "POM", row_number, 100)
        if not pom:
            raise BulletinImportError(f"Row {row_number}: POM is required.")
        description = _text(
            _value(row, "Description"),
            "Description",
            row_number,
            200,
        )
        unit = _text(_value(row, "Unit"), "Unit", row_number, 30)
        size_name = _value(row, "Size")
        size = None
        if size_name:
            size = sizes.get(size_name.casefold())
            if size is None:
                raise BulletinImportError(
                    f"Row {row_number}: Size '{size_name}' is not in the master Size catalog."
                )
        key = (pom.casefold(), size.pk if size else None)
        if key in seen:
            raise BulletinImportError(
                f"Row {row_number}: POM and Size duplicate an earlier row."
            )
        seen.add(key)

        tolerance_value = _value(row, "Tolerance (+/-)", "Tolerance")
        tolerance_value = tolerance_value.replace("+/-", "").replace("±", "").strip()
        entries.append(
            ArticleMeasurementPoint(
                article=article,
                factory=factory,
                pom=pom,
                description=description,
                size=size,
                target_measurement=_decimal(
                    _value(row, "Target Measurement"),
                    "Target Measurement",
                    row_number,
                    required=True,
                    non_negative=True,
                    max_digits=10,
                    decimal_places=3,
                ),
                tolerance=_decimal(
                    tolerance_value,
                    "Tolerance (+/-)",
                    row_number,
                    non_negative=True,
                    max_digits=10,
                    decimal_places=3,
                ),
                unit=unit,
                remark=_value(row, "Remark", "Remarks"),
            )
        )

    with transaction.atomic():
        ArticleMeasurementPoint.objects.filter(
            article=article,
            factory=factory,
        ).delete()
        ArticleMeasurementPoint.objects.bulk_create(entries)
    return len(entries)


def _import_operations(upload, article, factory, user):
    headers, rows = _read_csv(upload)
    _require_columns(
        headers,
        "Sequence",
        "Department/Section|Section",
        "Operation",
    )
    sections = {}
    for section in FactorySection.objects.filter(factory=factory, active=True):
        key = section.name.casefold()
        if key in sections:
            raise BulletinImportError(
                f"{factory.name} has ambiguous section names that differ only by case."
            )
        sections[key] = section
    entries = []
    seen_sequences = set()
    for row_number, row in rows:
        _validate_article(row, article, row_number)
        sequence_value = _value(row, "Sequence")
        try:
            sequence = int(sequence_value)
        except ValueError as exc:
            raise BulletinImportError(
                f"Row {row_number}: Sequence must be a positive whole number."
            ) from exc
        if sequence < 1:
            raise BulletinImportError(
                f"Row {row_number}: Sequence must be a positive whole number."
            )
        if sequence > 32767:
            raise BulletinImportError(
                f"Row {row_number}: Sequence must be 32767 or less."
            )
        if sequence in seen_sequences:
            raise BulletinImportError(
                f"Row {row_number}: Sequence {sequence} is duplicated."
            )
        seen_sequences.add(sequence)

        section_name = _value(row, "Department/Section", "Section")
        section = sections.get(section_name.casefold())
        if not section:
            raise BulletinImportError(
                f"Row {row_number}: Section '{section_name}' is not an active section "
                f"of {factory.name}."
            )
        operation_name = _text(
            _value(row, "Operation"),
            "Operation",
            row_number,
            150,
        )
        if not operation_name:
            raise BulletinImportError(f"Row {row_number}: Operation is required.")
        machine_equipment = _text(
            _value(row, "Machine/Equipment"),
            "Machine/Equipment",
            row_number,
            150,
        )
        attachment_foot = _text(
            _value(row, "Attachment/Foot"),
            "Attachment/Foot",
            row_number,
            150,
        )
        skill_level = _text(
            _value(row, "Skill Level"),
            "Skill Level",
            row_number,
            80,
        )
        entries.append(
            {
                "sequence": sequence,
                "section": section,
                "operation_name": operation_name,
                "operation_description": _value(
                    row, "Operation Description", "Description"
                ),
                "machine_equipment": machine_equipment,
                "attachment_foot": attachment_foot,
                "standard_time_minutes": _decimal(
                    _value(row, "Standard Time (Minutes)", "Standard Time"),
                    "Standard Time (Minutes)",
                    row_number,
                    non_negative=True,
                    max_digits=8,
                    decimal_places=2,
                ),
                "quality_checkpoint": _value(row, "Quality Checkpoint"),
                "skill_level": skill_level,
                "remarks": _value(row, "Remarks"),
            }
        )

    with transaction.atomic():
        flow, _ = ArticleProcessFlow.objects.get_or_create(
            article=article,
            factory=factory,
            defaults={"created_by": user},
        )
        flow.steps.all().delete()
        ArticleProcessStep.objects.bulk_create(
            [ArticleProcessStep(flow=flow, **entry) for entry in entries]
        )
    return len(entries)


def _import_defects(upload, article, factory):
    headers, rows = _read_csv(upload)
    _require_columns(headers, "Defect Code", "Defect Name")
    entries = []
    seen_codes = set()
    for row_number, row in rows:
        _validate_article(row, article, row_number)
        code = _text(_value(row, "Defect Code"), "Defect Code", row_number, 50)
        name = _text(_value(row, "Defect Name"), "Defect Name", row_number, 120)
        if not code:
            raise BulletinImportError(f"Row {row_number}: Defect Code is required.")
        if not name:
            raise BulletinImportError(f"Row {row_number}: Defect Name is required.")
        if code.casefold() in seen_codes:
            raise BulletinImportError(
                f"Row {row_number}: Defect Code '{code}' is duplicated."
            )
        seen_codes.add(code.casefold())
        category = _text(_value(row, "Category"), "Category", row_number, 80)
        operation = _text(_value(row, "Operation"), "Operation", row_number, 150)
        severity = _text(_value(row, "Severity"), "Severity", row_number, 30)
        identification_method = _text(
            _value(row, "Identification Method"),
            "Identification Method",
            row_number,
            150,
        )
        reference_photo = _text(
            _value(row, "Reference Photo"),
            "Reference Photo",
            row_number,
            255,
        )
        entries.append(
            ArticleDefectBulletin(
                article=article,
                factory=factory,
                defect_code=code,
                defect_name=name,
                category=category,
                operation=operation,
                description=_value(row, "Description"),
                severity=severity,
                identification_method=identification_method,
                prevention_corrective_action=_value(
                    row, "Prevention / Corrective Action", "Prevention Corrective Action"
                ),
                reference_photo=reference_photo,
                remarks=_value(row, "Remarks"),
            )
        )

    with transaction.atomic():
        ArticleDefectBulletin.objects.filter(
            article=article,
            factory=factory,
        ).delete()
        ArticleDefectBulletin.objects.bulk_create(entries)
    return len(entries)


def import_article_bulletin(bulletin_type, upload, article, factory, user):
    importers = {
        "measurement": _import_measurements,
        "operation": _import_operations,
        "defect": _import_defects,
    }
    importer = importers.get(bulletin_type)
    if importer is None:
        raise BulletinImportError("Unknown bulletin type.")
    if bulletin_type == "operation":
        return importer(upload, article, factory, user)
    return importer(upload, article, factory)


def export_article_bulletin(bulletin_type, article, factory):
    fields = EXPORT_FIELDS.get(bulletin_type)
    if fields is None:
        raise BulletinImportError("Unknown bulletin type.")

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    if bulletin_type == "measurement":
        records = ArticleMeasurementPoint.objects.filter(
            article=article,
            factory=factory,
        ).select_related("size")
        for entry in records:
            writer.writerow(
                {
                    "POM": entry.pom,
                    "Description": entry.description,
                    "Size": entry.size.name if entry.size_id else "",
                    "Target Measurement": entry.target_measurement,
                    "Tolerance (+/-)": (
                        entry.tolerance if entry.tolerance is not None else ""
                    ),
                    "Unit": entry.unit,
                    "Remark": entry.remark,
                }
            )
    elif bulletin_type == "operation":
        records = ArticleProcessStep.objects.filter(
            flow__article=article,
            flow__factory=factory,
        ).select_related("section").order_by("sequence")
        for entry in records:
            writer.writerow(
                {
                    "Sequence": entry.sequence,
                    "Article Code": article.article_code,
                    "Article Name": article.article_name,
                    "PO No.": "",
                    "Department/Section": entry.section.name,
                    "Operation": entry.operation_name,
                    "Operation Description": entry.operation_description,
                    "Machine/Equipment": entry.machine_equipment,
                    "Attachment/Foot": entry.attachment_foot,
                    "Standard Time (Minutes)": (
                        entry.standard_time_minutes
                        if entry.standard_time_minutes is not None
                        else ""
                    ),
                    "Quality Checkpoint": entry.quality_checkpoint,
                    "Skill Level": entry.skill_level,
                    "Remarks": entry.remarks,
                }
            )
    else:
        records = ArticleDefectBulletin.objects.filter(
            article=article,
            factory=factory,
        )
        for entry in records:
            writer.writerow(
                {
                    "Article Code": article.article_code,
                    "Article Name": article.article_name,
                    "PO No.": "",
                    "Defect Code": entry.defect_code,
                    "Defect Name": entry.defect_name,
                    "Category": entry.category,
                    "Operation": entry.operation,
                    "Description": entry.description,
                    "Severity": entry.severity,
                    "Identification Method": entry.identification_method,
                    "Prevention / Corrective Action": entry.prevention_corrective_action,
                    "Reference Photo": entry.reference_photo,
                    "Remarks": entry.remarks,
                }
            )

    filename = "-".join(
        part
        for part in (
            slugify(article.article_code),
            slugify(factory.code),
            slugify(bulletin_type),
        )
        if part
    )
    return f"{filename}.csv", output.getvalue()
