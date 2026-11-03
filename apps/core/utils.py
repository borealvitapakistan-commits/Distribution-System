import mimetypes
import tempfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve
from xhtml2pdf import pisa


def _brand_logo_file(uri):
    """The saved brand logo (kept in the database) as a temporary file."""
    try:
        match = resolve(uri.split("?", 1)[0])
    except Resolver404:
        return None
    if match.url_name != "brand-logo":
        return None

    from .models import Brand

    brand = Brand.objects.filter(pk=match.kwargs["pk"]).only(
        "logo_data", "logo_content_type", "updated_at"
    ).first()
    if brand is None or not brand.logo_data:
        return None
    suffix = mimetypes.guess_extension(brand.logo_content_type or "") or ".png"
    path = Path(tempfile.gettempdir()) / f"brand-logo-{brand.pk}-{brand.updated_at:%Y%m%d%H%M%S%f}{suffix}"
    if not path.exists():
        path.write_bytes(bytes(brand.logo_data))
    return str(path)


def _pdf_link_callback(uri, rel):
    """Lets PDF templates use normal /media/ and /static/ URLs and the
    brand logo's URL — xhtml2pdf needs them as files on disk."""
    logo = _brand_logo_file(uri)
    if logo:
        return logo

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
