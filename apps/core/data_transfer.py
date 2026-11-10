"""Versioned full-data workbook snapshots. Backups live outside the replaced database."""
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import uuid

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.serializers.json import DjangoJSONEncoder
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.color import no_style
from django.core.cache import cache
from django.db import connection, models, transaction
from django.utils import timezone
from botocore.exceptions import BotoCoreError, ClientError
from storages.backends.s3 import S3Storage


HEADER = ["kind", "model", "id", "data"]
MAX_BYTES = 100 * 1024 * 1024
EXCLUDED = {"sessions.session", "authtoken.token"}
csv.field_size_limit(MAX_BYTES)


class SnapshotError(ValueError):
    pass


class ImportBackup(PurePosixPath):
    """Backup path with attachment counts for the import confirmation."""



def snapshot_models():
    return sorted(
        (m for m in apps.get_models() if m._meta.managed
         and not m._meta.proxy and m._meta.label_lower not in EXCLUDED),
        key=lambda m: m._meta.label_lower,
    )


def schema():
    return {
        m._meta.label_lower: {
            f.name: f.get_internal_type()
            for f in (*m._meta.local_fields, *m._meta.local_many_to_many)
        } for m in snapshot_models()
    }


def digest(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def external_file_fields(model):
    return [field for field in model._meta.fields if isinstance(field, models.FileField)]


def backup_storage():
    if settings.USE_S3_STORAGE:
        return default_storage
    return FileSystemStorage(location=settings.DATA_BACKUP_ROOT,
                             file_permissions_mode=0o600, directory_permissions_mode=0o700)


def save_backup(payload):
    name = timezone.localtime().strftime("before-import-%Y%m%d-%H%M%S-") + uuid.uuid4().hex + ".xlsx"
    storage = backup_storage()
    if settings.USE_S3_STORAGE:
        return storage.save("data-backups/" + name, ContentFile(payload))
    directory = settings.DATA_BACKUP_ROOT
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / name
    with path.open("xb") as target:
        path.chmod(0o600)
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())
    return name


def export_snapshot():
    with transaction.atomic():
        if connection.vendor == "postgresql":
            # SHARE locks give all exported tables a consistent snapshot and
            # allow readers; imports already hold stronger locks.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s'")
                cursor.execute("LOCK TABLE " + ", ".join(connection.ops.quote_name(t) for t in transfer_tables())
                               + " IN SHARE MODE")
        return _export_snapshot()


def _export_snapshot():
    from .models import BrandLogo
    database_logos = set(BrandLogo.objects.values_list("brand_id", flat=True))
    rows = []
    media = {}
    missing_files = {}
    directory_cache = {}
    for model in snapshot_models():
        for obj in model._base_manager.order_by("pk").iterator():
            record = json.loads(serializers.serialize("json", [obj]))[0]
            rows.append(["record", record["model"], str(record["pk"]), json.dumps(record["fields"], ensure_ascii=True)])
            for field in external_file_fields(model):
                if model._meta.label_lower == "core.brand" and field.name == "logo" and obj.pk in database_logos:
                    continue
                file = getattr(obj, field.name)
                if file and file.name not in media and file.name not in missing_files:
                    try:
                        # Directory listings confirm absence without relying
                        # on HEAD responses (some providers return 400 for
                        # missing non-ASCII keys). Never infer absence from a
                        # permission error or a network failure.
                        if isinstance(file.storage, S3Storage):
                            directory = str(PurePosixPath(file.name).parent)
                            directory = "" if directory == "." else directory
                            cache_key = (id(file.storage), directory)
                            if cache_key not in directory_cache:
                                try:
                                    _, names = file.storage.listdir(directory)
                                    directory_cache[cache_key] = set(names)
                                except ClientError as exc:
                                    if str(exc.response.get("Error", {}).get("Code")) in {"AccessDenied", "403"}:
                                        directory_cache[cache_key] = None
                                    else:
                                        raise
                            names = directory_cache[cache_key]
                            if names is not None and PurePosixPath(file.name).name not in names:
                                raise FileNotFoundError(file.name)
                        with file.storage.open(file.name, "rb") as source:
                            media[file.name] = base64.b64encode(source.read()).decode("ascii")
                    except FileNotFoundError:
                        missing_files[file.name] = "Already missing from storage; original record reference preserved."
                    except (OSError, BotoCoreError, ClientError) as exc:
                        raise SnapshotError(f"Cannot read {model._meta.verbose_name} {obj.pk}, {field.verbose_name}: {file.name}. Check storage access and try again; current records are unchanged.") from exc
    rows.extend(["file", "", name, data] for name, data in sorted(media.items()))
    records = [{"model": row[1], "pk": row[2], "fields": json.loads(row[3])}
               for row in rows if row[0] == "record"]
    from .data_workbook import write_workbook
    return write_workbook(records, {name: base64.b64decode(data) for name, data in media.items()}, snapshot_models(), missing_files=missing_files)


def parse_snapshot(payload):
    if len(payload) > MAX_BYTES:
        raise SnapshotError("The workbook must be 100 MB or smaller.")
    if payload.startswith(b"PK"):
        from .data_workbook import WorkbookError, read_workbook
        try:
            records, files = read_workbook(payload, snapshot_models(), MAX_BYTES)
            if getattr(records, "business", False):
                return records, files
        except (WorkbookError, ValueError, TypeError, KeyError) as exc:
            raise SnapshotError(str(exc)) from exc
        # Reuse the complete legacy validator, preserving support for old
        # backups without exposing JSON records in the new workbook.
        rows = [["record", r["model"], str(r["pk"]), json.dumps(r["fields"], cls=DjangoJSONEncoder)] for r in records]
        rows += [["file", "", name, base64.b64encode(content).decode("ascii")] for name, content in sorted(files.items())]
        manifest = {"format": "distribution-data", "version": 1, "schema": schema(), "rows": len(rows), "sha256": digest(rows)}
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(HEADER)
        writer.writerow(["manifest", "", "", json.dumps(manifest)])
        writer.writerows(rows)
        return _parse_csv_snapshot(output.getvalue().encode("utf-8-sig"), check_size=False, missing_files=getattr(records, "missing_files", {}))
    return _parse_csv_snapshot(payload)


def _parse_csv_snapshot(payload, check_size=True, missing_files=None):
    missing_files = dict(missing_files or {})
    if check_size and len(payload) > MAX_BYTES:
        raise SnapshotError("The file must be 100 MB or smaller.")
    try:
        reader = csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
        if next(reader) != HEADER:
            raise ValueError()
        head = next(reader)
        if len(head) != 4 or head[:3] != ["manifest", "", ""]:
            raise ValueError()
        manifest = json.loads(head[3])
        rows = list(reader)
        if (manifest["format"] != "distribution-data" or manifest["version"] != 1
                or manifest["schema"] != schema() or manifest["rows"] != len(rows)
                or manifest["sha256"] != digest(rows)):
            raise ValueError()
        records, files, seen = [], {}, set()
        current_schema = schema()
        allowed = {m._meta.label_lower: m for m in snapshot_models()}
        for row in rows:
            if len(row) != 4:
                raise ValueError()
            kind, label, pk, data = row
            if kind == "record":
                if label not in allowed or (label, pk) in seen:
                    raise ValueError()
                seen.add((label, pk))
                fields = json.loads(data)
                expected = set(current_schema[label]) - {allowed[label]._meta.pk.name}
                if not isinstance(fields, dict) or set(fields) != expected:
                    raise ValueError()
                records.append({"model": label, "pk": pk, "fields": fields})
            elif kind == "file":
                path = PurePosixPath(pk)
                if label or not pk or path.is_absolute() or ".." in path.parts or "\\" in pk or pk in files:
                    raise ValueError()
                files[pk] = base64.b64decode(data, validate=True)
            else:
                raise ValueError()
        if not any(r["model"] == "accounts.user" and r["fields"].get("role") == "OWNER"
                   and r["fields"].get("is_active") and r["fields"].get("password")
                   and not r["fields"]["password"].startswith("!") for r in records):
            raise SnapshotError("The workbook must contain an active owner account with a usable password.")
        referenced = set()
        database_logos = {r["pk"] for r in records if r["model"] == "core.brandlogo"}
        for record in records:
            for field in external_file_fields(allowed[record["model"]]):
                if record["model"] == "core.brand" and field.name == "logo" and record["pk"] in database_logos:
                    continue
                name = record["fields"].get(field.name)
                if name:
                    referenced.add(name)
        if referenced != set(files) | set(missing_files):
            raise ValueError()
        # Convert field types before touching the current database.
        list(serializers.deserialize("json", json.dumps(records)))
        from .data_workbook import WorkbookRecords
        return WorkbookRecords(records, missing_files=missing_files), files
    except SnapshotError:
        raise
    except (ValueError, TypeError, KeyError, StopIteration, UnicodeError, csv.Error,
            serializers.base.DeserializationError) as exc:
        raise SnapshotError("Invalid or incomplete data file. Use an export from this app version and keep the required fields and attachments.") from exc


def transfer_tables():
    tables = set()
    for model in [*snapshot_models(), *(apps.get_model(label) for label in EXCLUDED)]:
        tables.add(model._meta.db_table)
        for field in model._meta.local_many_to_many:
            tables.add(field.remote_field.through._meta.db_table)
    return sorted(tables)


def import_snapshot(payload, *, business_only=False, actor=None):
    records, files = parse_snapshot(payload)
    if business_only and not getattr(records, "business", False):
        raise SnapshotError("Choose the business workbook from Export Data. Full system backups use a separate restoration format.")
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if pending:
        raise SnapshotError("Apply pending database migrations before importing: " + ", ".join(m.app_label + "." + m.name for m, _ in pending))
    saved_files = []
    missing_backup_count = 0
    try:
        # SQLite FK checks must be disabled before the transaction begins.
        with connection.constraint_checks_disabled(), transaction.atomic():
            tables = transfer_tables()
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '15s'")
                    cursor.execute("LOCK TABLE " + ", ".join(connection.ops.quote_name(t) for t in tables)
                                   + " IN ACCESS EXCLUSIVE MODE")
                    cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            elif connection.vendor == "sqlite":
                # Acquire the writer lock before reading the backup, even if
                # the test/local database uses DEFERRED transactions.
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE core_dataversion SET number = number WHERE id = 1")
            else:
                raise SnapshotError("Data replacement supports PostgreSQL and SQLite databases.")
            from .models import DataVersion
            previous_version = DataVersion.objects.filter(pk=1).values_list("number", flat=True).first() or 0
            backup_payload = export_snapshot()
            backup_name = save_backup(backup_payload)
            current_records, current_files = parse_snapshot(backup_payload)
            missing_backup_count = len(getattr(current_records, "missing_files", {}))
            if getattr(records, "business", False):
                records, files = _merge_business_records(records, files, current_records, current_files, actor=actor)
            replacements = {}
            prefix = "data-imports/" + uuid.uuid4().hex
            for name, content in files.items():
                saved = default_storage.save(prefix + "/" + PurePosixPath(name).name[-48:], ContentFile(content))
                saved_files.append(saved)
                replacements[name] = saved
            allowed = {m._meta.label_lower: m for m in snapshot_models()}
            for record in records:
                for field in external_file_fields(allowed[record["model"]]):
                    if record["fields"].get(field.name) in replacements:
                        record["fields"][field.name] = replacements[record["fields"][field.name]]
            # Replace protected ledger rows and automatic M2M tables without
            # model cascades/signals; all relationships are checked below.
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute("DELETE FROM " + connection.ops.quote_name(table))
            for obj in serializers.deserialize("json", json.dumps(records)):
                obj.save()
            # A restored cache version must never revive pages saved before
            # import, including pages held by other application workers.
            import time
            DataVersion.objects.update_or_create(pk=1, defaults={"number": max(previous_version + 1, time.time_ns())})
            connection.check_constraints()
            with connection.cursor() as cursor:
                for statement in connection.ops.sequence_reset_sql(no_style(), snapshot_models()):
                    cursor.execute(statement)
            transaction.on_commit(cache.clear)
        from django.contrib.contenttypes.models import ContentType
        ContentType.objects.clear_cache()
        result = ImportBackup(backup_name)
        result.missing_backup_count = missing_backup_count
        result.missing_import_count = len(getattr(records, "missing_files", {}))
        return result
    except Exception:
        for name in saved_files:
            default_storage.delete(name)
        raise


def business_workbook(records, files):
    """Create the user-facing file without account/system records."""
    import copy
    import mimetypes
    from .data_workbook import is_business_model, write_workbook
    allowed = {m._meta.label_lower for m in snapshot_models() if is_business_model(m)}
    selected = [copy.deepcopy(r) for r in records if r["model"] in allowed]
    file_data = dict(files)
    logos = {str(r["pk"]): r for r in records if r["model"] == "core.brandlogo"}
    for record in selected:
        if record["model"] == "core.brand" and str(record["pk"]) in logos:
            logo = logos[str(record["pk"])]
            ext = mimetypes.guess_extension(logo["fields"]["content_type"]) or ".bin"
            name = "brand/logos/" + str(record["pk"]) + ext
            record["fields"]["logo"] = name
            file_data[name] = base64.b64decode(logo["fields"]["data"])
    referenced = {r["fields"].get(field.name) for r in selected
                  for field in external_file_fields(apps.get_model(r["model"])) if r["fields"].get(field.name)}
    return write_workbook(selected, {name: value for name, value in file_data.items() if name in referenced}, snapshot_models(), business=True, missing_files={name: reason for name, reason in getattr(records, "missing_files", {}).items() if name in referenced and name not in file_data})


def export_business_workbook():
    records, files = parse_snapshot(export_snapshot())
    return business_workbook(records, files)


def _merge_business_records(imported, imported_files, current, current_files, actor=None):
    """Called under the import table locks, after the full backup is durable."""
    import copy
    import mimetypes
    from .data_workbook import is_business_model
    allowed = {m._meta.label_lower: m for m in snapshot_models()}
    lookup = {(r["model"], str(r["pk"])): r for r in current}
    owners = [r for r in current if r["model"] == "accounts.user" and r["fields"].get("role") == "OWNER" and r["fields"].get("is_active")]
    if not owners:
        raise SnapshotError("An active owner account must exist before importing business data.")
    if actor is not None and str(actor) not in {str(owner["pk"]) for owner in owners}:
        raise SnapshotError("An active owner account must perform the import.")
    actor = str(actor) if actor is not None else owners[0]["pk"]
    preserved = [copy.deepcopy(r) for r in current if not is_business_model(allowed[r["model"]]) and r["model"] != "core.brandlogo"]
    result = list(preserved)
    assigned_users = set()
    unavailable = {**getattr(current, "missing_files", {}), **getattr(imported, "missing_files", {})}
    for record in imported:
        record = copy.deepcopy(record)
        model = allowed[record["model"]]
        old = lookup.get((record["model"], str(record["pk"])))
        fields = record["fields"]
        for field in model._meta.local_fields:
            if field.primary_key or field.name in fields:
                continue
            value = old["fields"].get(field.name) if old else field.get_default()
            if getattr(field, "auto_now", False):
                value = timezone.now().isoformat()
            elif getattr(field, "auto_now_add", False):
                value = value or timezone.now().isoformat()
            if field.name == "updated_by":
                value = actor
            elif field.name == "created_by":
                value = value or actor
            fields[field.name] = value
        if record["model"] == "owners.ownerprofile":
            if old:
                owner_user = old["fields"]["user"]
            else:
                matching = [u for u in owners if (u["fields"].get("email") or "").casefold() == (fields.get("email") or "").casefold()]
                if len(matching) == 1:
                    owner_user = matching[0]["pk"]
                elif len(owners) == 1:
                    owner_user = owners[0]["pk"]
                else:
                    raise SnapshotError("An owner profile must match an existing owner account by email. Create the account first.")
            if str(owner_user) in assigned_users:
                raise SnapshotError("Multiple owner profiles cannot use the same login account. Create the corresponding owner accounts first.")
            assigned_users.add(str(owner_user))
            fields["user"] = owner_user
        result.append(record)
        if record["model"] == "core.brand" and fields.get("logo"):
            name = fields["logo"]
            if name not in imported_files:
                if name in unavailable:
                    continue
                raise SnapshotError("A brand logo is missing from the workbook attachments.")
            result.append({"model": "core.brandlogo", "pk": record["pk"], "fields": {
                "data": base64.b64encode(imported_files[name]).decode("ascii"),
                "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                "updated_at": timezone.now().isoformat(),
            }})
    logos = {str(r["pk"]) for r in result if r["model"] == "core.brandlogo"}
    referenced = set()
    for record in result:
        for field in external_file_fields(allowed[record["model"]]):
            if record["model"] == "core.brand" and field.name == "logo" and str(record["pk"]) in logos:
                continue
            value = record["fields"].get(field.name)
            if value:
                referenced.add(value)
    files = {**current_files, **imported_files}
    if referenced - (files.keys() | unavailable.keys()):
        raise SnapshotError("A referenced attachment is missing from the workbook.")
    from .data_workbook import WorkbookRecords
    return WorkbookRecords(result, missing_files={key: value for key, value in unavailable.items() if key in referenced and key not in files}), {key: value for key, value in files.items() if key in referenced}
