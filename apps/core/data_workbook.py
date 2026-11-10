"""Readable category worksheets for full-data export, editing and replacement."""
import base64
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
import hashlib
import io
import json
import re
import uuid
from zipfile import ZipFile, BadZipFile

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter


class WorkbookError(ValueError):
    pass


VERSION = "distribution-workbook-v1"
CHUNK = 28000
TITLES = {
    "accounts.user": "Users", "core.brand": "Brands", "core.brandlogo": "Brand Logos",
    "core.dataversion": "Cache Version", "products.product": "Products",
    "products.productcategory": "Product Categories", "manufacturers.manufacturer": "Manufacturers",
    "manufacturers.manufacturerorder": "Purchase Orders", "manufacturers.manufacturerorderitem": "Purchase Order Items",
    "audit.auditevent": "Audit History", "auth.group": "User Groups", "auth.permission": "Permissions",
    "contenttypes.contenttype": "Record Types", "admin.logentry": "Admin History",
}
PRIORITY = ("name", "description", "body_html", "generic_name", "email", "sku", "barcode", "po_number")
FILE_HEADER = ["S.No.", "File Path", "Part", "Total Parts", "SHA256", "Content (Base64)"]
TEXT_HEADER = ["S.No.", "Key", "Part", "Total Parts", "Text"]
SCHEMA_HEADER = ["Sheet", "Model", "Field", "Column", "Type"]


def append_row(sheet, values):
    sheet.append(values)
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


def style_sheet(sheet):
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = "1:1"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="174E57")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 32
    for col in sheet.columns:
        length = max((len(str(cell.value or "")) for cell in col[:50]), default=10)
        sheet.column_dimensions[col[0].column_letter].width = min(max(length + 2, 14), 48)
        for cell in col[1:]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.column_dimensions["A"].width = 9


def layout(model_list):
    used = {"read me", "_schema", "files", "long text"}
    result = {}
    for model in model_list:
        title = TITLES.get(model._meta.label_lower, str(model._meta.verbose_name_plural).title())
        title = re.sub(r"[\\/*?:\[\]]", " ", title)[:31]
        original, suffix = title, 2
        while title.lower() in used:
            title = original[:27] + f" {suffix}"
            suffix += 1
        used.add(title.lower())
        fields = [f for f in model._meta.local_fields if not f.primary_key]
        fields.sort(key=lambda f: (PRIORITY.index(f.name) if f.name in PRIORITY else len(PRIORITY) + (1 if f.name in {"created_at", "updated_at", "created_by", "updated_by"} else 0), f.creation_counter))
        fields.append(model._meta.pk)
        columns = []
        for field in fields:
            if field.primary_key:
                header = "Record ID"
            else:
                header = "Description" if model._meta.label_lower == "products.product" and field.name == "body_html" else str(field.verbose_name).replace("_", " ").title()
                if field.is_relation:
                    header += " ID"
            columns.append((field, header))
        result[model._meta.label_lower] = (model, title, columns)
    return result


def display_name(record):
    fields = record["fields"]
    for name in ("name", "email", "po_number", "batch_number", "sku", "action", "object_label"):
        if fields.get(name):
            return str(fields[name])
    if fields.get("first_name") or fields.get("last_name"):
        return f'{fields.get("first_name", "")} {fields.get("last_name", "")}'.strip()
    return str(record["pk"])


def write_workbook(records, files, model_list):
    book = Workbook()
    readme = book.active
    readme.title = "Read Me"
    append_row(readme, ["Data workbook", VERSION])
    instructions = [
        ("Created", timezone.now().isoformat()),
        ("How to use", "Each category has its own sheet. One row is one record; each field has its own column."),
        ("Edit or create", "Edit values in existing rows. To create records, add rows and leave Record ID blank; IDs and default values are generated on import."),
        ("Serial numbers", "S.No. is for reading and sorting only. Record ID preserves links between records."),
        ("Linked records", "Use the ID columns to link records. For new links you may leave the ID blank and enter a unique matching Name from the related sheet."),
        ("Reference names", "Name columns beside linked IDs are for reference; when an ID is present it is authoritative."),
        ("Dates and money", "Dates use ISO format. Decimal amounts are text to preserve exact precision; ordinary Excel numbers are also accepted."),
        ("Blank values", "For new records blank cells use model defaults. For existing records blank cells clear optional fields. Required values must be supplied."),
        ("Users", "User passwords are stored password hashes. Keep existing hashes when restoring user accounts; do not type plaintext passwords in these columns."),
        ("Relations", "Relationship sheets contain one link per row. IDs must match records in category sheets."),
        ("Attachments", "Files stores complete attachments in numbered parts. Keep file paths, hashes and parts intact. Long Text holds values too long for one Excel cell."),
        ("Replacement", "Import replaces current records after a dated backup. Removing rows removes those records. Keep every category sheet and the headers."),
        ("Compatibility", "Use this workbook with the matching app schema. Sessions and API tokens are excluded. Do not change the hidden _Schema sheet."),
        ("Privacy", "Contains confidential business data, attachments and account password hashes. Store privately."),
        ("Category", "Record count"),
    ]
    for row in instructions:
        append_row(readme, row)
    schema = book.create_sheet("_Schema")
    append_row(schema, SCHEMA_HEADER)
    append_row(schema, [VERSION, "", "", "", ""])
    plans = layout(model_list)
    lookup = {(r["model"], str(r["pk"])): display_name(r) for r in records}
    grouped = defaultdict(list)
    for record in records:
        grouped[record["model"]].append(record)
    long_values, binaries = {}, {}

    def cell_value(value, key):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False)
        elif isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif isinstance(value, int) and not isinstance(value, bool) and len(str(abs(value))) > 15:
            value = str(value)
        elif isinstance(value, Decimal):
            value = str(value)
        if isinstance(value, str) and (value.startswith("@text:") or len(value) > 32000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)):
            # Excel cannot hold these values directly. JSON escaping keeps
            # control characters lossless in the auxiliary text sheet.
            long_values[key] = json.dumps(value, ensure_ascii=True)
            return "@text:" + key
        return value

    for label, (model, title, columns) in plans.items():
        sheet = book.create_sheet(title)
        headers = ["S.No."]
        for field, header in columns:
            headers.append(header)
            append_row(schema, [title, label, field.name, header, field.get_internal_type()])
            if field.is_relation and not field.primary_key:
                headers.append(header[:-3] + " (Name)")
        append_row(sheet, headers)
        for serial, record in enumerate(grouped[label], 1):
            row = [serial]
            for field, header in columns:
                value = record["pk"] if field.primary_key else record["fields"].get(field.name)
                key = f'{label}/{record["pk"]}/{field.name}'
                if isinstance(field, models.BinaryField) and value is not None:
                    binaries[key] = base64.b64decode(value)
                    value = "@binary:" + key
                row.append(cell_value(value, key))
                if field.is_relation and not field.primary_key:
                    row.append(lookup.get((field.related_model._meta.label_lower, str(value)), ""))
            append_row(sheet, row)
        append_row(readme, [title, len(grouped[label])])
        style_sheet(sheet)
        column_number = 2
        for field, header in columns:
            guidance = str(field.help_text or "")
            if field.choices:
                guidance += " Choices: " + "; ".join(f"{key} = {value}" for key, value in field.flatchoices)
                choices = ",".join(str(key) for key, _ in field.flatchoices)
                if len(choices) < 250 and all("," not in str(key) for key, _ in field.flatchoices):
                    validation = DataValidation(type="list", formula1='"' + choices + '"', allow_blank=True)
                    validation.error = "Choose a value from the list."
                    validation.showErrorMessage = True
                    sheet.add_data_validation(validation)
                    validation.add(f"{get_column_letter(column_number)}2:{get_column_letter(column_number)}1048576")
            if not (field.blank or field.null or field.has_default() or field.primary_key or getattr(field, "auto_now_add", False) or getattr(field, "auto_now", False)):
                guidance = "Required. " + guidance
            if field.primary_key:
                guidance = "Keep existing IDs. Leave blank on new rows to generate an ID where supported."
            if guidance:
                sheet.cell(1, column_number).comment = Comment(guidance, "Distribution App")
            column_number += 2 if field.is_relation and not field.primary_key else 1
        for field in model._meta.local_many_to_many:
            if not field.remote_field.through._meta.auto_created:
                continue
            relation_title = (title[:17] + " - " + str(field.verbose_name).title()[:11])[:31]
            if relation_title in book.sheetnames:
                relation_title = title[:15] + " - " + field.name[:12]
            relation = book.create_sheet(relation_title)
            append_row(relation, ["S.No.", "Record ID", "Record Name", "Related ID", "Related Name"])
            append_row(schema, [relation_title, label, field.name, "", "ManyToManyField"])
            serial = 0
            for record in grouped[label]:
                for other in record["fields"].get(field.name, []):
                    serial += 1
                    append_row(relation, [serial, str(record["pk"]), display_name(record), str(other),
                                          lookup.get((field.related_model._meta.label_lower, str(other)), "")])
            style_sheet(relation)
    attachments = book.create_sheet("Files")
    append_row(attachments, FILE_HEADER)
    for key, content in sorted({**files, **{"@binary:" + k: v for k, v in binaries.items()}}.items()):
        encoded = base64.b64encode(content).decode("ascii")
        parts = [encoded[i:i + CHUNK] for i in range(0, len(encoded), CHUNK)] or [""]
        checksum = hashlib.sha256(content).hexdigest()
        for part, data in enumerate(parts, 1):
            append_row(attachments, [attachments.max_row, key, part, len(parts), checksum, data])
    texts = book.create_sheet("Long Text")
    append_row(texts, TEXT_HEADER)
    for key, value in sorted(long_values.items()):
        parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)]
        for part, text in enumerate(parts, 1):
            append_row(texts, [texts.max_row, key, part, len(parts), text])
    style_sheet(attachments)
    style_sheet(texts)
    style_sheet(readme)
    readme.column_dimensions["A"].width = 24
    readme.column_dimensions["B"].width = 110
    for number in range(2, len(instructions) + 2):
        readme.row_dimensions[number].height = 36
    schema.sheet_state = "hidden"
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def read_workbook(payload, model_list, max_bytes):
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            if sum(item.file_size for item in archive.infolist()) > max_bytes * 5:
                raise WorkbookError("Workbook expands beyond the supported size.")
        book = load_workbook(io.BytesIO(payload), read_only=True, data_only=False)
        try:
            return _read_book(book, model_list)
        finally:
            book.close()
    except WorkbookError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, BadZipFile, OSError) as exc:
        raise WorkbookError("Invalid Excel workbook. Use the workbook exported by this app and keep its sheets and headers.") from exc


def _read_book(book, model_list):
    plans = layout(model_list)
    if "_Schema" not in book or "Read Me" not in book:
        raise WorkbookError("This workbook is missing its format information. Export a workbook from the app first.")
    schema_rows = list(book["_Schema"].values)
    if list(schema_rows[0]) != SCHEMA_HEADER or schema_rows[1][0] != VERSION:
        raise WorkbookError("Unsupported workbook version.")
    expected_schema = []
    for label, (model, title, columns) in plans.items():
        for field, header in columns:
            expected_schema.append((title, label, field.name, header, field.get_internal_type()))
        for field in model._meta.local_many_to_many:
            if field.remote_field.through._meta.auto_created:
                relation_title = (title[:17] + " - " + str(field.verbose_name).title()[:11])[:31]
                expected_schema.append((relation_title, label, field.name, None, "ManyToManyField"))
    if set(schema_rows[2:]) != set(expected_schema):
        raise WorkbookError("Workbook categories or fields differ from this app version. Export a fresh workbook.")

    allowed_sheets = {"Read Me", "_Schema", "Files", "Long Text"} | {row[0] for row in expected_schema}
    unknown_sheets = set(book.sheetnames) - allowed_sheets
    if any(not title.startswith("Archive ") for title in unknown_sheets):
        raise WorkbookError("Unknown category sheets: " + ", ".join(sorted(unknown_sheets)))

    def rows(title, headers):
        if title not in book:
            raise WorkbookError(f'Missing sheet: {title}.')
        if book[title].max_row * book[title].max_column > 2000000:
            raise WorkbookError(f'{title}: sheet exceeds the supported cell count.')
        iterator = iter(book[title].iter_rows())
        first = next(iterator, ())
        if [cell.value for cell in first] != headers:
            raise WorkbookError(f'Headers were changed in {title}. Keep the exported column names.')
        for number, cells in enumerate(iterator, 2):
            if any(c.data_type == "f" for c in cells):
                raise WorkbookError(f'{title}, row {number}: formulas are not accepted; paste their values instead.')
            values = [c.value for c in cells]
            if any(value is not None for value in values[1:]):
                yield number, values

    def chunks(title, headers, binary=False):
        grouped = defaultdict(dict)
        totals, hashes = {}, {}
        for number, row in rows(title, headers):
            _, key, part, total, *tail = row
            if not isinstance(key, str) or not isinstance(part, int) or not isinstance(total, int) or not 1 <= part <= total:
                raise WorkbookError(f'{title}, row {number}: invalid attachment/text part.')
            if part in grouped[key] or (key in totals and totals[key] != total):
                raise WorkbookError(f'{title}: duplicate or inconsistent parts for {key}.')
            totals[key] = total
            if binary:
                checksum, value = tail
                if key in hashes and hashes[key] != checksum:
                    raise WorkbookError(f'{title}: inconsistent file checksum.')
                hashes[key] = checksum
            else:
                value = tail[0]
            grouped[key][part] = str(value or "")
        result = {}
        for key, parts in grouped.items():
            if len(parts) != totals[key]:
                raise WorkbookError(f'{title}: missing parts for {key}.')
            value = "".join(parts[i] for i in range(1, totals[key] + 1))
            if binary:
                value = base64.b64decode(value, validate=True)
                if hashlib.sha256(value).hexdigest() != hashes[key]:
                    raise WorkbookError(f'Files: attachment checksum failed for {key}.')
            else:
                value = json.loads(value)
            result[key] = value
        return result

    files = chunks("Files", FILE_HEADER, binary=True)
    long_text = chunks("Long Text", TEXT_HEADER)
    records, by_id, names, pending = [], {}, defaultdict(list), []
    for label, (model, title, columns) in plans.items():
        headers = ["S.No."]
        for field, header in columns:
            headers.append(header)
            if field.is_relation and not field.primary_key:
                headers.append(header[:-3] + " (Name)")
        raw_rows = list(rows(title, headers))
        id_column = headers.index("Record ID")
        max_id = max([int(row[id_column]) for _, row in raw_rows if row[id_column] is not None] or [0]) if isinstance(model._meta.pk, models.AutoField) else 0
        for number, row in raw_rows:
            pk = row[id_column]
            is_new = pk is None
            if is_new:
                if isinstance(model._meta.pk, models.AutoField):
                    max_id += 1
                    pk = max_id
                elif model._meta.pk.has_default():
                    pk = model._meta.pk.get_default()
                else:
                    raise WorkbookError(f'{title}, row {number}: Record ID is required for this category.')
            pk = str(model._meta.pk.to_python(pk))
            key = (label, pk)
            if key in by_id:
                raise WorkbookError(f'{title}, row {number}: duplicate Record ID.')
            record = {"model": label, "pk": pk, "fields": {}}
            index = 1
            for field, header in columns:
                value = row[index]
                index += 1
                reference_name = None
                if field.is_relation and not field.primary_key:
                    reference_name = row[index]
                    index += 1
                if field.primary_key:
                    continue
                if isinstance(value, str) and value.startswith("@text:"):
                    if value[6:] not in long_text:
                        raise WorkbookError(f'{title}, row {number}: missing long text.')
                    value = long_text[value[6:]]
                if isinstance(field, models.BinaryField) and value is not None:
                    if value not in files or not str(value).startswith("@binary:"):
                        raise WorkbookError(f'{title}, row {number}: missing binary attachment.')
                    value = base64.b64encode(files[value]).decode("ascii")
                if value is None:
                    if is_new and field.has_default():
                        value = field.get_default()
                    elif is_new and (getattr(field, "auto_now_add", False) or getattr(field, "auto_now", False)):
                        value = timezone.now()
                    elif not field.null and not field.is_relation and isinstance(field, (models.CharField, models.TextField, models.FileField)):
                        value = ""
                if field.is_relation:
                    pending.append((record, field, value, reference_name, title, number))
                elif isinstance(field, models.JSONField) and isinstance(value, str):
                    value = json.loads(value)
                elif isinstance(field, models.DecimalField) and value is not None:
                    value = str(value)
                elif isinstance(value, (datetime, date)):
                    if isinstance(value, datetime) and timezone.is_naive(value):
                        value = timezone.make_aware(value)
                    value = value.isoformat()
                if isinstance(value, uuid.UUID):
                    value = str(value)
                record["fields"][field.name] = value
            if is_new and label == "products.product":
                from django.utils.text import slugify
                values = record["fields"]
                values["name"] = (values.get("name") or "").strip()
                values["sku"] = (values.get("sku") or "").strip().upper() or "BV-" + uuid.UUID(pk).hex.upper()
                values["handle"] = values.get("handle") or slugify(values["name"]) or slugify(values["sku"])
                values["currency"] = (values.get("currency") or "PKR").upper()
            for field in model._meta.local_many_to_many:
                if field.remote_field.through._meta.auto_created:
                    record["fields"][field.name] = []
            if label == "products.product":
                from apps.products.description import sanitize_description
                record["fields"]["body_html"] = sanitize_description(record["fields"].get("body_html", ""))
            records.append(record)
            by_id[key] = record
            names[(label, display_name(record))].append(pk)
    for record, field, value, name, title, number in pending:
        label = field.related_model._meta.label_lower
        if value is None and name:
            matches = names.get((label, str(name)), [])
            if len(matches) != 1:
                raise WorkbookError(f'{title}, row {number}: linked name "{name}" is missing or not unique. Use its ID.')
            value = matches[0]
        if value is not None:
            value = str(field.target_field.to_python(value))
            if (label, value) not in by_id:
                raise WorkbookError(f'{title}, row {number}: linked {field.verbose_name} ID does not exist in the workbook.')
        elif not field.null:
            raise WorkbookError(f'{title}, row {number}: {field.verbose_name} is required.')
        record["fields"][field.name] = value
    for title, label, name, _, kind in expected_schema:
        if kind != "ManyToManyField":
            continue
        field = plans[label][0]._meta.get_field(name)
        linked_label = field.related_model._meta.label_lower
        seen = set()
        for number, row in rows(title, ["S.No.", "Record ID", "Record Name", "Related ID", "Related Name"]):
            _, pk, _, other, _ = row
            pk, other = str(pk), str(other)
            if (label, pk) not in by_id or (linked_label, other) not in by_id or (pk, other) in seen:
                raise WorkbookError(f'{title}, row {number}: invalid or duplicate relationship IDs.')
            seen.add((pk, other))
            by_id[(label, pk)]["fields"][name].append(other)
    # Validate scalar field values without querying the current database:
    # linked/new objects are validated against the workbook above instead.
    from django.core import serializers
    from django.core.exceptions import ValidationError
    try:
        decoded = list(serializers.deserialize("json", json.dumps(records, cls=DjangoJSONEncoder)))
        for item in decoded:
            for field in item.object._meta.local_fields:
                if field.primary_key or field.is_relation:
                    continue
                value = getattr(item.object, field.attname)
                field.clean(value, item.object)
    except (ValidationError, serializers.base.DeserializationError) as exc:
        raise WorkbookError("A workbook field has an invalid or missing value: " + str(exc)) from exc
    database_logos = {r["pk"] for r in records if r["model"] == "core.brandlogo"}
    referenced = set()
    for record in records:
        for field in plans[record["model"]][0]._meta.fields:
            if not isinstance(field, models.FileField):
                continue
            if record["model"] == "core.brand" and field.name == "logo" and record["pk"] in database_logos:
                continue
            if record["fields"].get(field.name):
                referenced.add(record["fields"][field.name])
    return records, {key: value for key, value in files.items() if key in referenced}
