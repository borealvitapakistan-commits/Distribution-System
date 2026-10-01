from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from xhtml2pdf import pisa


def _pdf_link_callback(uri, rel):
    """Lets PDF templates use normal /media/ and /static/ URLs (e.g. a
    brand logo) — xhtml2pdf needs them as files on disk."""
    if uri.startswith(settings.MEDIA_URL):
        path = Path(settings.MEDIA_ROOT) / uri[len(settings.MEDIA_URL):]
        return str(path) if path.exists() else uri

    if uri.startswith(settings.STATIC_URL):
        found = finders.find(uri[len(settings.STATIC_URL):])
        return found or uri

    return uri


def render_pdf(template_name, context):
    html = render_to_string(template_name, context)
    buffer = BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, link_callback=_pdf_link_callback)

    if result.err:
        raise ValueError("Could not generate PDF.")

    return buffer.getvalue()
