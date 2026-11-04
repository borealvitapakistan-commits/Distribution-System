"""{% asset 'css/app.css' %} — a static file's URL with a version taken
from its contents, so the browser keeps it cached until the file really
changes (a timestamp made it download the CSS and JS again on every page)."""
import hashlib
from functools import lru_cache

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


def _file_version(path):
    found = finders.find(path)
    if not found:
        return ""
    with open(found, "rb") as handle:
        return hashlib.md5(handle.read(), usedforsecurity=False).hexdigest()[:10]


_cached_version = lru_cache(maxsize=None)(_file_version)


@register.simple_tag
def asset(path):
    # Re-read while developing, so edited CSS shows without a restart.
    version = _file_version(path) if settings.DEBUG else _cached_version(path)
    url = static(path)
    return f"{url}?v={version}" if version else url
