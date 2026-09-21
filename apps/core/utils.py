from io import BytesIO

from django.template.loader import render_to_string
from xhtml2pdf import pisa


def render_pdf(template_name, context):
    html = render_to_string(template_name, context)
    buffer = BytesIO()
    result = pisa.CreatePDF(html, dest=buffer)

    if result.err:
        raise ValueError("Could not generate PDF.")

    return buffer.getvalue()
