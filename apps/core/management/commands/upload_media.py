"""Copy existing local uploads to private object storage, preserving DB file names."""
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copy local media to configured S3 storage. Dry run unless --apply is supplied."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(settings.MEDIA_ROOT))
        parser.add_argument("--apply", action="store_true", help="Actually upload missing files.")

    def handle(self, *args, **options):
        if not settings.USE_S3_STORAGE:
            raise CommandError("Enable USE_S3_STORAGE and configure S3 credentials first.")
        source = Path(options["source"]).resolve()
        if not source.is_dir():
            raise CommandError("The source media directory does not exist.")

        uploaded = planned = skipped = 0
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(source):
                raise CommandError("A source file resolves outside the media directory.")
            name = path.relative_to(source).as_posix()
            if any(part.startswith(".") for part in path.relative_to(source).parts):
                continue
            if default_storage.exists(name):
                skipped += 1
                continue
            planned += 1
            if options["apply"]:
                with path.open("rb") as content:
                    saved_name = default_storage.save(name, File(content, name=name))
                if saved_name != name:
                    raise CommandError(
                        "Storage renamed a file during upload. Stop and check for concurrent uploads."
                    )
                uploaded += 1

        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"Uploaded {uploaded} files; skipped {skipped} existing files."))
        else:
            self.stdout.write(f"Dry run: {planned} files to upload; {skipped} already exist. No files changed.")
            self.stdout.write("Run again with --apply to upload. Existing objects are never overwritten.")
