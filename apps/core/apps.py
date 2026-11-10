from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.utils.autoreload import autoreload_started


def _watch_env_file(sender, **kwargs):
    """The dev server reads .env only when it starts, so a changed
    DATABASE_URL (or any other setting there) would otherwise be ignored
    until a manual restart. Watching the file restarts it on save."""
    sender.extra_files.add(Path(settings.BASE_DIR) / ".env")


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'

    def ready(self):
        autoreload_started.connect(_watch_env_file)
