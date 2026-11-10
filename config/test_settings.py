"""Isolated test configuration. Never uses the application's Supabase database."""
import os

# The isolated runner must also start on machines without production credentials.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from .settings import *  # noqa: F403

# CI exercises real PostgreSQL locking and migrations. Local tests use SQLite.
if env("TEST_DATABASE_URL", default=""):  # noqa: F405
    DATABASES = {"default": env.db("TEST_DATABASE_URL")}  # noqa: F405
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

SECURE_SSL_REDIRECT = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SHOPIFY_INVENTORY_SYNC = False
SLACK_WEBHOOK_URL = ""
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
