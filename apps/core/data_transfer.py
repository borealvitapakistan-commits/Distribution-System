"""Versioned full-data CSV snapshots. Backups live outside the replaced database."""
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
from django.core.files.base import ContentFile
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


def export_snapshot():
    rows = []
    media = {}
    for model in snapshot_models():
        for obj in model._base_manager.order_by("pk").iterator():
            record = json.loads(serializers.serialize("json", [obj]))[0]
            rows.append(["record", record["model"], str(record["pk"]), json.dumps(record["fields"], ensure_ascii=True)])
            for field in model._meta.fields:
                if isinstance(field, models.FileField):
                    file = getattr(obj, field.name)
                    if file and file.name not in media:
                        try:
                            with file.storage.open(file.name, "rb") as source:
                                media[file.name] = base64.b64encode(source.read()).decode("ascii")
                        except OSError as exc:
                            raise SnapshotError("An attached file is missing or unreadable. Restore it before exporting or importing.") from exc
    rows.extend(["file", "", name, data] for name, data in sorted(media.items()))
    manifest = {"format": "distribution-data", "version": 1, "created_at": timezone.now().isoformat(),
                "schema": schema(), "rows": len(rows), "sha256": digest(rows)}
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(HEADER)
    writer.writerow(["manifest", "", "", json.dumps(manifest)])
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def parse_snapshot(payload):
    if len(payload) > MAX_BYTES:
        raise SnapshotError("The CSV must be 100 MB or smaller.")
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
            raise SnapshotError("The CSV must contain an active owner account with a usable password.")
        referenced = set()
        for record in records:
            for field in allowed[record["model"]]._meta.fields:
                if isinstance(field, models.FileField):
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
        raise SnapshotError("Invalid or incomplete export CSV. Use an unchanged CSV exported by this version of the app.") from exc


def import_snapshot(payload):
    records, files = parse_snapshot(payload)
    saved_files = []
    backup_path = None
    try:
        # SQLite requires disabling FK checks before opening the transaction.
        # IMMEDIATE transactions serialize writers, including the backup read.
        with connection.constraint_checks_disabled(), transaction.atomic():
            backup = export_snapshot()
            directory = settings.DATA_BACKUP_ROOT
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup_path = directory / (timezone.localtime().strftime("before-import-%Y%m%d-%H%M%S-") + uuid.uuid4().hex + ".csv")
            with backup_path.open("xb") as target:
                backup_path.chmod(0o600)
                target.write(backup)
                target.flush()
                os.fsync(target.fileno())
            # Use separate paths so restoring attachments cannot overwrite old files.
            replacements = {}
            from django.core.files.storage import default_storage
            prefix = "data-imports/" + uuid.uuid4().hex
            for name, content in files.items():
                saved = default_storage.save(prefix + "/" + PurePosixPath(name).name[-48:], ContentFile(content))
                saved_files.append(saved)
                replacements[name] = saved
            allowed = {m._meta.label_lower: m for m in snapshot_models()}
            for record in records:
                for field in allowed[record["model"]]._meta.fields:
                    if isinstance(field, models.FileField) and record["fields"].get(field.name):
                        record["fields"][field.name] = replacements[record["fields"][field.name]]
            # Delete without model cascades/signals: every exported table and its
            # automatic M2M tables is replaced, including protected ledger rows.
            tables = set()
            for model in [*snapshot_models(), *(apps.get_model(label) for label in EXCLUDED)]:
                tables.add(model._meta.db_table)
                for field in model._meta.local_many_to_many:
                    tables.add(field.remote_field.through._meta.db_table)
            with connection.cursor() as cursor:
                for table in sorted(tables):
                    cursor.execute("DELETE FROM " + connection.ops.quote_name(table))
            for obj in serializers.deserialize("json", json.dumps(records)):
                obj.save()
            connection.check_constraints()
        from django.contrib.contenttypes.models import ContentType
        ContentType.objects.clear_cache()
        return backup_path
    except Exception:
        from django.core.files.storage import default_storage
        for name in saved_files:
            default_storage.delete(name)
        raise
