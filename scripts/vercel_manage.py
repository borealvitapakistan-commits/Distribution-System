"""Explicit production management commands using `vercel pull` credentials."""
import sys

from vercel_release import load_production_environment


if __name__ == "__main__":
    load_production_environment()
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
