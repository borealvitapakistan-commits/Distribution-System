import csv
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.manufacturers.models import Manufacturer
from apps.products.models import Product
from apps.core.models import Brand, BrandLogo, DataVersion
from apps.core.data_transfer import (
    SnapshotError, digest, export_snapshot, import_snapshot, parse_snapshot,
)


class DataTransferTests(TransactionTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.override = override_settings(
            MEDIA_ROOT=self.root / "media", DATA_BACKUP_ROOT=self.root / "backups", USE_S3_STORAGE=False,
            STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                      "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.owner = User.objects.create_user("owner@example.com", "secret-password", role="OWNER")
        self.brand = Brand.objects.create(name="Original برانڈ", address='Line one, "quoted"\nLine two')
        self.brand.logo = ContentFile(b"original-image", name="brand.png")
        self.brand.save()
        self.supplier = Manufacturer.objects.create(name="Media supplier", logo=ContentFile(b"supplier-image", name="supplier.png"))

    def rewrite(self, payload, modify):
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
        modify(rows)
        manifest = json.loads(rows[1][3])
        manifest.update(rows=len(rows[2:]), sha256=digest(rows[2:]))
        rows[1][3] = json.dumps(manifest)
        output = io.StringIO(newline="")
        csv.writer(output).writerows(rows)
        return output.getvalue().encode("utf-8-sig")

    def test_round_trip_replaces_data_preserves_relations_files_and_dated_backup(self):
        group = Group.objects.create(name="Extra access")
        self.owner.groups.add(group)
        before_groups = set(self.owner.groups.values_list("name", flat=True))
        manufacturer = Manufacturer.objects.create(name="Supplier", created_by=self.owner)
        product = Product.objects.create(name="Linked product", manufacturer=manufacturer, brand=self.brand, created_by=self.owner)
        event = AuditEvent.objects.create(actor=self.owner, action="test.created", object_type="Product", object_id=str(product.pk))
        exported = export_snapshot()
        self.brand.name = "Current version"
        self.brand.save()
        extra = Brand.objects.create(name="Remove me")
        backup = self.root / "backups" / import_snapshot(exported).name
        restored = Brand.objects.get(pk=self.brand.pk)
        self.assertEqual(Product.objects.get(pk=product.pk).manufacturer_id, manufacturer.pk)
        self.assertEqual(Product.objects.get(pk=product.pk).brand_id, restored.pk)
        self.assertEqual(AuditEvent.objects.get(pk=event.pk).actor_id, self.owner.pk)
        self.assertEqual(restored.name, "Original برانڈ")
        self.assertEqual(restored.address, self.brand.address)
        self.assertFalse(Brand.objects.filter(pk=extra.pk).exists())
        self.assertEqual(bytes(BrandLogo.objects.get(brand=restored).data), b"original-image")
        with Manufacturer.objects.get(pk=self.supplier.pk).logo.open("rb") as file:
            self.assertEqual(file.read(), b"supplier-image")
        self.assertEqual(set(User.objects.get(pk=self.owner.pk).groups.values_list("name", flat=True)), before_groups)
        self.assertTrue(User.objects.get(pk=self.owner.pk).check_password("secret-password"))
        self.assertTrue(backup.name.startswith("before-import-"))
        records, _ = parse_snapshot(backup.read_bytes())
        self.assertIn("Current version", [r["fields"].get("name") for r in records])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        import_snapshot(exported)
        self.assertTrue(backup.exists())
        self.assertEqual(len(list((self.root / "backups").glob("*.csv"))), 2)

    def test_truncated_and_invalid_csv_leave_existing_data(self):
        payload = export_snapshot()
        for invalid in (b"not,csv", payload[:-10], payload.replace(b"Original", b"Modified")):
            with self.assertRaises(SnapshotError):
                import_snapshot(invalid)
        self.assertTrue(Brand.objects.filter(pk=self.brand.pk).exists())
        self.assertFalse((self.root / "backups").exists())

    def test_backup_failure_prevents_replacement(self):
        payload = export_snapshot()
        with patch("pathlib.Path.mkdir", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                import_snapshot(payload)
        self.assertTrue(Brand.objects.filter(pk=self.brand.pk).exists())

    def test_late_failure_rolls_back_and_retains_backup(self):
        payload = export_snapshot()
        self.brand.name = "Keep current data"
        self.brand.save()
        with patch("apps.core.data_transfer.connection.check_constraints", side_effect=IntegrityError("invalid FK")):
            with self.assertRaises(IntegrityError):
                import_snapshot(payload)
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.name, "Keep current data")
        self.assertEqual(len(list((self.root / "backups").glob("*.csv"))), 1)
        self.assertFalse(any((self.root / "media" / "data-imports").rglob("*.png")))

    def test_missing_owner_and_unsafe_paths_are_rejected(self):
        payload = export_snapshot()
        def remove_owner(rows):
            rows[:] = [r for r in rows if r[1] != "accounts.user"]
        def unsafe_path(rows):
            next(r for r in rows if r[0] == "file")[2] = "../escape.png"
        for modify in (remove_owner, unsafe_path):
            with self.assertRaises(SnapshotError):
                import_snapshot(self.rewrite(payload, modify))

    def test_owner_ui_export_confirmation_and_successful_import(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("data-settings"))
        self.assertContains(page, "Export Data")
        self.assertContains(page, "Import Data")
        response = self.client.get(reverse("data-export"))
        self.assertEqual(response["Cache-Control"], "no-store")
        from django.core.files.uploadedfile import SimpleUploadedFile
        rejected = self.client.post(reverse("data-settings"), {"data_file": SimpleUploadedFile("data.csv", response.content)})
        self.assertEqual(rejected.status_code, 400)
        accepted = self.client.post(reverse("data-settings"), {
            "confirm_replace": "yes", "data_file": SimpleUploadedFile("data.csv", response.content),
        })
        self.assertRedirects(accepted, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_permissions(self):
        for url in ("data-settings", "data-export"):
            self.assertEqual(self.client.get(reverse(url)).status_code, 302)
        inactive = User.objects.create_user("inactive@example.com", "secret", role="OWNER", is_active=False)
        self.client.force_login(inactive)
        for url in ("data-settings", "data-export"):
            self.assertEqual(self.client.get(reverse(url)).status_code, 302)
            self.assertEqual(self.client.post(reverse(url)).status_code, 302)

    def test_invalid_foreign_key_rolls_back_replacement(self):
        Manufacturer.objects.create(name="Supplier", created_by=self.owner)
        payload = export_snapshot()
        def break_reference(rows):
            row = next(r for r in rows if r[1] == "manufacturers.manufacturer")
            fields = json.loads(row[3])
            fields["created_by"] = "00000000-0000-0000-0000-000000000001"
            row[3] = json.dumps(fields)
        with self.assertRaises(IntegrityError):
            import_snapshot(self.rewrite(payload, break_reference))
        self.assertTrue(Brand.objects.filter(pk=self.brand.pk).exists())
        self.assertEqual(Manufacturer.objects.get(name="Supplier").created_by_id, self.owner.pk)

    def test_hosted_backup_uses_persistent_storage_and_keeps_previous_backups(self):
        from django.core.files.storage import default_storage
        with override_settings(USE_S3_STORAGE=True):
            payload = export_snapshot()
            first = import_snapshot(payload)
            second = import_snapshot(payload)
            self.assertTrue(str(first).startswith("data-backups/before-import-"))
            self.assertNotEqual(first, second)
            self.assertTrue(default_storage.exists(str(first)))
            self.assertTrue(default_storage.exists(str(second)))
            with default_storage.open(str(first), "rb") as backup:
                records, _ = parse_snapshot(backup.read())
            self.assertTrue(any(r["model"] == "core.brandlogo" for r in records))

    def test_import_changes_cache_version_and_clears_cached_sessions(self):
        from django.core.cache import cache
        from django.contrib.sessions.backends.cached_db import SessionStore
        session = SessionStore()
        session["old"] = "old data"
        session.save()
        old_key = session.session_key
        DataVersion.objects.update_or_create(pk=1, defaults={"number": 123})
        payload = export_snapshot()
        cache.set("stale-brand", "old value")
        import_snapshot(payload)
        self.assertIsNone(cache.get("stale-brand"))
        self.assertNotEqual(DataVersion.objects.get(pk=1).number, 123)
        self.assertNotIn("old", SessionStore(old_key))

    def test_auto_increment_records_can_be_created_after_import(self):
        import_snapshot(export_snapshot())
        self.assertIsNotNone(Group.objects.create(name="After import").pk)

    def test_legacy_file_logo_survives_without_a_database_logo_row(self):
        from django.core.files.storage import default_storage
        BrandLogo.objects.filter(brand=self.brand).delete()
        name = default_storage.save("brand/logos/legacy.png", ContentFile(b"legacy-logo"))
        Brand.objects.filter(pk=self.brand.pk).update(logo=name)
        import_snapshot(export_snapshot())
        restored = Brand.objects.get(pk=self.brand.pk)
        with restored.logo.open("rb") as logo:
            self.assertEqual(logo.read(), b"legacy-logo")
