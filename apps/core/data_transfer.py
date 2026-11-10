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


HEADER = ["kind", "model", "id", "data"]
MAX_BYTES = 100 * 1024 * 1024
EXCLUDED = {"sessions.session", "authtoken.token"}
csv.field_size_limit(MAX_BYTES)


class SnapshotError(ValueError):
    pass


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
    for model in snapshot_models():
        for obj in model._base_manager.order_by("pk").iterator():
            record = json.loads(serializers.serialize("json", [obj]))[0]
            rows.append(["record", record["model"], str(record["pk"]), json.dumps(record["fields"], ensure_ascii=True)])
            for field in external_file_fields(model):
                if model._meta.label_lower == "core.brand" and field.name == "logo" and obj.pk in database_logos:
                    continue
                file = getattr(obj, field.name)
                if file and file.name not in media:
                    try:
                        with file.storage.open(file.name, "rb") as source:
                            media[file.name] = base64.b64encode(source.read()).decode("ascii")
                    except (OSError, FileNotFoundError) as exc:
                        raise SnapshotError("An attached file is missing or unreadable. Restore it before exporting or importing.") from exc
    rows.extend(["file", "", name, data] for name, data in sorted(media.items()))
    records = [{"model": row[1], "pk": row[2], "fields": json.loads(row[3])}
               for row in rows if row[0] == "record"]
    from .data_workbook import write_workbook
    return write_workbook(records, {name: base64.b64decode(data) for name, data in media.items()}, snapshot_models())


def parse_snapshot(payload):
    if len(payload) > MAX_BYTES:
        raise SnapshotError("The workbook must be 100 MB or smaller.")
    if payload.startswith(b"PK"):
        from .data_workbook import WorkbookError, read_workbook
        try:
            records, files = read_workbook(payload, snapshot_models(), MAX_BYTES)
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
        return _parse_csv_snapshot(output.getvalue().encode("utf-8-sig"), check_size=False)
    return _parse_csv_snapshot(payload)


def _parse_csv_snapshot(payload, check_size=True):
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
        if referenced != set(files):
            raise ValueError()
        # Convert field types before touching the current database.
        list(serializers.deserialize("json", json.dumps(records)))
        return records, files
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


def import_snapshot(payload):
    records, files = parse_snapshot(payload)
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if pending:
        raise SnapshotError("Apply pending database migrations before importing: " + ", ".join(m.app_label + "." + m.name for m, _ in pending))
    saved_files = []
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
            backup_name = save_backup(export_snapshot())
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
        return PurePosixPath(backup_name)
    except Exception:
        for name in saved_files:
            default_storage.delete(name)
        raise
