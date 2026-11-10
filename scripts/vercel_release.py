"""Run release checks using Vercel's downloaded environment without shell sourcing."""
import argparse
import os
import sys
from pathlib import Path

import environ


ROOT = Path(__file__).resolve().parent.parent


def load_production_environment():
    """Shared by release checks and explicit production management commands."""
    env_file = ROOT / ".vercel" / ".env.production.local"
    if not env_file.is_file():
        raise SystemExit("Run vercel pull --yes --environment=production first.")

    environ.Env.read_env(env_file, overwrite=True)
    # Use the session pooler for DDL/migrations, independently of the runtime's
    # transaction pooler. Never print or pass connection URLs on the command line.
    if os.environ.get("DATABASE_MIGRATION_URL"):
        os.environ["DATABASE_URL"] = os.environ["DATABASE_MIGRATION_URL"]
    os.environ["VERCEL"] = "1"
    os.environ["DB_TRANSACTION_POOLER"] = "False"
    os.environ["DB_POOL_MIN"] = "0"
    os.environ["DB_POOL_MAX"] = "1"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(ROOT))

    import django

    django.setup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate", action="store_true", help="Apply pending production migrations.")
    options = parser.parse_args()
    load_production_environment()

    from django.core.management import call_command

    call_command("check", deploy=True, fail_level="WARNING")
    if options.migrate:
        call_command("migrate", interactive=False)
    else:
        # A push never silently runs existing destructive migrations. The
        # workflow's manual migration option is used after reviewing the plan.
        call_command("migrate", check_unapplied=True, interactive=False)


if __name__ == "__main__":
    main()
