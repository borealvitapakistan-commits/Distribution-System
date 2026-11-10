import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings


class HealthTests(TestCase):
    def test_readiness_checks_the_database_without_login(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response["Cache-Control"])

    def test_database_outage_returns_no_connection_details(self):
        with patch("config.health.connection.cursor", side_effect=OperationalError("private-db-host")):
            with self.assertLogs("config.health", level="ERROR"):
                response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "private-db-host", status_code=503)


@override_settings(USE_S3_STORAGE=True, STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class UploadMediaTests(SimpleTestCase):
    def test_dry_run_and_rerun_preserve_existing_files_and_database_names(self):
        with TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "brand" / "logos").mkdir(parents=True)
            (source / "brand" / "logos" / "our logo.png").write_bytes(b"logo")
            (source / "proof.pdf").write_bytes(b"new local proof")
            default_storage.save("proof.pdf", ContentFile(b"existing remote proof"))

            output = StringIO()
            call_command("upload_media", source=directory, stdout=output)
            self.assertIn("Dry run: 1 files to upload; 1 already exist", output.getvalue())
            self.assertFalse(default_storage.exists("brand/logos/our logo.png"))

            call_command("upload_media", source=directory, apply=True, stdout=StringIO())
            with default_storage.open("brand/logos/our logo.png") as logo:
                self.assertEqual(logo.read(), b"logo")
            with default_storage.open("proof.pdf") as proof:
                self.assertEqual(proof.read(), b"existing remote proof")

            output = StringIO()
            call_command("upload_media", source=directory, apply=True, stdout=output)
            self.assertIn("Uploaded 0 files; skipped 2 existing files", output.getvalue())

    @override_settings(USE_S3_STORAGE=False)
    def test_local_storage_cannot_be_used_as_an_upload_destination(self):
        with self.assertRaisesMessage(CommandError, "Enable USE_S3_STORAGE"):
            call_command("upload_media", stdout=StringIO())


class VercelSettingsTests(SimpleTestCase):
    def test_production_uses_private_storage_small_pool_and_exact_hosts(self):
        # Import fresh settings as Vercel would, without loading the local .env.
        environment = {
            **os.environ,
            "VERCEL": "1", "DEBUG": "True", "USE_S3_STORAGE": "True",
            "SECRET_KEY": "test-only-secret",
            "DATABASE_URL": "postgresql://test:test@db.invalid:6543/test",
            "ALLOWED_HOSTS": "inventory.example.com",
            "CSRF_TRUSTED_ORIGINS": "https://inventory.example.com",
            "VERCEL_URL": "this-deployment.vercel.app",
            "VERCEL_PROJECT_PRODUCTION_URL": "our-inventory.vercel.app",
            "S3_ENDPOINT_URL": "https://storage.invalid/storage/v1/s3",
            "S3_REGION_NAME": "test-region", "S3_BUCKET_NAME": "private-media",
            "S3_ACCESS_KEY_ID": "test-key", "S3_SECRET_ACCESS_KEY": "test-secret",
            "DB_POOL_MIN": "0", "DB_POOL_MAX": "1",
            "DB_TRANSACTION_POOLER": "True", "SECURE_SSL_REDIRECT": "True",
        }
        script = """
import json
from config import settings as s
from storages.backends.s3 import S3Storage
storage = S3Storage(**s.STORAGES['default']['OPTIONS'])
url = storage.url('proofs/a proof.pdf')
print(json.dumps({
 'debug': s.DEBUG, 'hosts': s.ALLOWED_HOSTS, 'csrf': s.CSRF_TRUSTED_ORIGINS,
 'secure': s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE and s.SECURE_SSL_REDIRECT,
 'pool': s.DATABASES['default']['OPTIONS']['pool']['max_size'],
 'prepare': s.DATABASES['default']['OPTIONS']['prepare_threshold'],
 'cursors_disabled': s.DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'],
 'proxy': s.SECURE_PROXY_SSL_HEADER, 'url': url,
 'acl': storage.default_acl, 'overwrite': storage.file_overwrite,
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=settings.BASE_DIR, env=environment,
            check=True, capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        self.assertFalse(data["debug"])
        self.assertTrue(data["secure"])
        self.assertEqual(data["pool"], 1)
        self.assertIsNone(data["prepare"])
        self.assertTrue(data["cursors_disabled"])
        self.assertEqual(data["proxy"], ["HTTP_X_FORWARDED_PROTO", "https"])
        self.assertIn("this-deployment.vercel.app", data["hosts"])
        self.assertIn("https://our-inventory.vercel.app", data["csrf"])
        self.assertNotIn(".vercel.app", data["hosts"])
        self.assertIn("/private-media/proofs/a%20proof.pdf?", data["url"])
        self.assertIn("X-Amz-Signature=", data["url"])
        self.assertIsNone(data["acl"])
        self.assertFalse(data["overwrite"])
