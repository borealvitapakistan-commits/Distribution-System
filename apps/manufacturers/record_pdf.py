"""The documents of a Manufacturer order, as PDFs.

document_pdf renders one of our own documents — the Request to Quote as
it was sent, the Purchase Order, or the order as it stands now.

order_record_pdf is the Full Order Record: one PDF that walks through the
order in the order it happened, with the real files in it —

    1. Request to Quote        (our document, as sent)
    2. Manufacturer's quote    (the picture / PDF they replied with)
    3. Purchase Order          (our document)
    4. Advance payment proof   (only if an advance was paid)
    5. Manufacturer's invoice  (the picture / PDF that came with the goods)
    6. Payment proof           (the final payment)
    7. Summary & conversation  (every step said between us and them)

Uploaded files are shown as they are: a PDF's own pages follow a short
title page, a photo is placed on a titled page. A file that's missing or
can't be read gets a page saying so, so the record never fails to build.
"""

from dataclasses import dataclass
from decimal import Decimal

from io import BytesIO
from pathlib import PurePath

from django.utils import timezone
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from apps.core.utils import render_pdf

from .models import OrderRevision

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
ZERO = Decimal("0.00")


@dataclass
class Document:
    """What the document template prints at the top and bottom."""

    title: str
    date: object
    number_label: str
    lines: list
    subtotal: Decimal
    shipping_amount: Decimal = ZERO
    tax_percentage: Decimal = ZERO
    tax_amount: Decimal = ZERO
    grand_total: Decimal = ZERO
    has_price_changes: bool = False
    message: str = ""


def _revision_lines(revision):
    lines = [line for line in revision.lines.select_related("product") if not line.removed]
    for line in lines:
        # Frozen copies carry no colour: they are what was sent at the time.
        line.price_highlight = ""
    return lines


def _document(order, kind):
    """kind: "request" (as first sent), "purchase_order" (its latest
    version) or "current" (the order as it stands)."""
    if kind in ("request", "purchase_order"):
        stage = (
            OrderRevision.Stage.REQUEST if kind == "request"
            else OrderRevision.Stage.PURCHASE_ORDER
        )
        revisions = order.revisions.filter(stage=stage).order_by("number")
        revision = revisions.first() if kind == "request" else revisions.last()
        if revision is not None:
            lines = _revision_lines(revision)
            subtotal = sum((line.line_total for line in lines), ZERO)
            return Document(
                title="Request to Quote" if kind == "request" else "Purchase Order",
                date=revision.created_at,
                number_label="Reference number" if kind == "request" else "P.O. number",
                lines=lines,
                subtotal=subtotal,
                grand_total=subtotal,
                message=revision.message,
            )

    lines = list(order.items.select_related("product"))
    # The message that went with this document: the Request to Quote's
    # while it's still one, the latest Purchase Order's after that.
    message_stage = (
        OrderRevision.Stage.REQUEST if order.is_quote else OrderRevision.Stage.PURCHASE_ORDER
    )
    sent_with = (
        order.revisions.filter(stage=message_stage).order_by("-number")
        .values_list("message", flat=True).first()
    )
    return Document(
        title=order.document_title,
        date=order.created_at if order.is_quote else order.purchase_order_date,
        number_label="Reference number" if order.is_quote else "P.O. number",
        lines=lines,
        subtotal=order.subtotal,
        shipping_amount=order.shipping_amount,
        tax_percentage=order.tax_percentage,
        tax_amount=order.tax_amount,
        grand_total=order.grand_total,
        has_price_changes=order.has_price_changes,
        message=sent_with or "",
    )


def payment_pages(order, part=None):
    """The pages of a Purchase Order PDF. With an advance between 0% and
    100% it is two pages — one for the advance we pay now, one for the
    balance we pay later — otherwise one plain page. part ("advance" or
    "balance") picks a single page."""
    split = (not order.is_quote) and 0 < order.advance_percent < 100
    if not split:
        return [{"part": None}]

    total = order.grand_total
    paid = order.advance_paid
    advance = paid if paid else order.advance_amount
    advance_percent = (
        (advance * Decimal("100") / total).quantize(Decimal("0.01")).normalize() if total else order.advance_percent
    )
    pages = {
        "advance": {
            "part": "advance",
            "subtitle": f"Advance payment · {order.advance_percent.normalize():f}%",
            "now_label": f"Advance now ({order.advance_percent.normalize():f}%)",
            "now_amount": order.advance_amount,
            "statement": (
                f"We are paying Rs {order.advance_amount:,.2f} now as a {order.advance_percent.normalize():f}% "
                f"advance on the order total of Rs {total:,.2f}."
            ),
        },
        "balance": {
            "part": "balance",
            "subtitle": f"Balance payment · {(Decimal('100') - advance_percent).normalize():f}%",
            "now_label": f"Balance now ({(Decimal('100') - advance_percent).normalize():f}%)",
            "now_amount": total - advance,
            "statement": (
                f"We are paying the remaining Rs {total - advance:,.2f} "
                f"({(Decimal('100') - advance_percent).normalize():f}%) of the order total of Rs {total:,.2f}."
            ),
        },
    }
    return [pages[part]] if part in pages else [pages["advance"], pages["balance"]]


CM = 72 / 2.54
STAMP_BOTTOM_CM = 1.0
STAMP_FULL_CM = 3.7
STAMP_MIN_CM = 2.5


def _stamp_overlay(height_cm):
    """One A4 page with only the company stamp box, bottom right."""
    from reportlab.lib.units import cm

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setStrokeColor(HexColor("#9ca3af"))
    pdf.setDash(1, 2)
    pdf.rect(13.2 * cm, STAMP_BOTTOM_CM * cm, 6.2 * cm, height_cm * cm, stroke=1, fill=0)
    pdf.setFillColor(HexColor("#6b7280"))
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(16.3 * cm, (STAMP_BOTTOM_CM + 0.3) * cm, "Company stamp")
    pdf.save()
    return PdfReader(BytesIO(buffer.getvalue())).pages[0]


def _lowest_text(page):
    """How far up the page (in points) its lowest line of text sits."""
    lowest = [page.mediabox.height]

    def visit(text, cm, tm, font, size):
        if text.strip():
            lowest.append(tm[5] * cm[3] + cm[5])

    page.extract_text(visitor_text=visit)
    return min(lowest)


def _last_page_has_products(pdf_bytes):
    """True for a one-page document, or when the last page carries product
    rows (its table headings are repeated there)."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return len(reader.pages) == 1 or "Description" in reader.pages[-1].extract_text()


def _stamped(pdf_bytes):
    """The PDF with the company stamp drawn in the bottom-right corner of
    its last page — as tall as the free space allows — or None when that
    corner is too full for a stamp."""
    reader = PdfReader(BytesIO(pdf_bytes))
    # Leave room below the lowest text for the boxes drawn around it.
    free_cm = (_lowest_text(reader.pages[-1]) - 24) / CM - STAMP_BOTTOM_CM
    if free_cm < STAMP_MIN_CM:
        return None
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.pages[-1].merge_page(_stamp_overlay(min(STAMP_FULL_CM, free_cm)))
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def document_pdf(order, *, kind="current", brand, highlight_changes=False, part=None, split=False):
    """split=True gives a Purchase Order with an advance its two payment
    documents; part picks one of them. A Purchase Order's company stamp
    goes on the last page of each document only."""
    from .views import document_table

    doc = _document(order, kind)
    columns, rows = document_table(doc.lines)
    pages = payment_pages(order, part) if (split or part) else [{"part": None}]

    def render(page, stamp_inline=False, break_before=None):
        return render_pdf(
            "manufacturers/manufacturer_order_pdf.html",
            {
                "order": order,
                "doc": doc,
                "brand": brand,
                "columns": columns,
                "rows": rows,
                "highlight_changes": highlight_changes,
                "pages": [page],
                "stamp_inline": stamp_inline,
                "break_before": break_before,
            },
        )

    if order.is_quote:
        return render(pages[0])

    writer = PdfWriter()
    for page in pages:
        # The stamp goes in the free bottom-right corner of the last page.
        # The last page must also show products: when the totals land on a
        # page of their own, or leave no room for the stamp, the last one
        # or two products move to that page with them.
        first = render(page)
        carry = min(2, len(doc.lines) - 1)
        pdf_bytes = None if (carry > 0 and not _last_page_has_products(first)) else _stamped(first)
        if pdf_bytes is None and carry > 0:
            pdf_bytes = _stamped(render(page, break_before=len(doc.lines) - carry))
        if pdf_bytes is None:
            pdf_bytes = _stamped(first) or render(page, stamp_inline=True)
        for each in PdfReader(BytesIO(pdf_bytes)).pages:
            writer.add_page(each)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Pages made here: section titles, photos, "nothing attached" notes
# ---------------------------------------------------------------------------


def _page(brand, title, subtitle="", note="", image=None):
    """One A4 page with the section title at the top and, optionally, a
    photo scaled to fit the rest of the page or a note under the title."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 42
    colour = HexColor(getattr(brand, "primary_color", "") or "#1f7a4d")

    pdf.setFillColor(colour)
    pdf.rect(0, height - 8, width, 8, stroke=0, fill=1)

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(margin, height - 54, title)
    top = height - 54
    if subtitle:
        pdf.setFillColor(HexColor("#6b7280"))
        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin, height - 72, subtitle)
        top = height - 72

    if note:
        pdf.setFillColor(HexColor("#374151"))
        pdf.setFont("Helvetica", 11)
        y = top - 34
        for line in note.split("\n"):
            pdf.drawString(margin, y, line)
            y -= 16

    if image is not None:
        box_width = width - 2 * margin
        box_height = top - 24 - margin
        image_width, image_height = image.getSize()
        scale = min(box_width / image_width, box_height / image_height, 1.0)
        drawn_width, drawn_height = image_width * scale, image_height * scale
        pdf.drawImage(
            image,
            margin + (box_width - drawn_width) / 2,
            top - 24 - drawn_height,
            drawn_width,
            drawn_height,
            preserveAspectRatio=True,
            mask="auto",
        )

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


PRINT_PIXELS = 1600


def _print_image(data):
    """A photo as a JPEG no bigger than a printed A4 page needs."""
    image = Image.open(BytesIO(data))
    image.thumbnail((PRINT_PIXELS, PRINT_PIXELS))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    out = BytesIO()
    image.save(out, format="JPEG", quality=82, optimize=True)
    out.seek(0)
    return ImageReader(out)


def _file_bytes(field_file):
    if not field_file:
        return None
    try:
        field_file.open("rb")
        try:
            return field_file.read()
        finally:
            field_file.close()
    except (OSError, ValueError):
        return None


def _attachment_parts(brand, title, field_file, *, when=None, subtitle="", missing):
    """The PDF pages that show one uploaded file under its section title."""
    if not subtitle and when:
        subtitle = f"Recorded {timezone.localtime(when):%d %b %Y}"
    data = _file_bytes(field_file)
    if data is None:
        return [_page(brand, title, subtitle, note=missing)]

    name = PurePath(field_file.name).name
    extension = PurePath(name).suffix.lower()

    if extension == ".pdf":
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("password protected")
            if reader.is_encrypted:
                # Re-save without the (empty) password so it can be merged.
                plain = PdfWriter(clone_from=reader)
                out = BytesIO()
                plain.write(out)
                data = out.getvalue()
        except Exception:
            return [_page(
                brand, title, subtitle,
                note=f"The attached file {name} couldn't be opened\n(it may be damaged or password protected).",
            )]
        return [
            _page(brand, title, subtitle, note=f"Their document ({name}) follows on the next page."),
            data,
        ]

    if extension in IMAGE_TYPES:
        try:
            return [_page(brand, title, subtitle, image=_print_image(data))]
        except Exception:
            pass

    return [_page(
        brand, title, subtitle,
        note=f"The attached file {name} can't be shown here.\nOpen it from the order page.",
    )]


# ---------------------------------------------------------------------------
# The Full Order Record
# ---------------------------------------------------------------------------


def _paid_line(payment):
    line = f"Rs {payment.amount:,.2f} paid {payment.paid_at:%d %b %Y}"
    return f"{line} · {payment.note}" if payment.note else line


def order_record_pdf(order, *, brand, record_context):
    """The whole order as one PDF, in the order it happened.
    record_context: what the summary & conversation template needs."""
    parts = []
    number = 0

    def section(title):
        nonlocal number
        number += 1
        return f"{number}. {title}"

    # 1. Our Request to Quote, as it was sent. Older orders were placed
    # straight as a Purchase Order and never had one.
    if not order.revisions.filter(stage=OrderRevision.Stage.REQUEST).exists():
        parts.append(_page(
            brand, section("Request to Quote & quote"),
            note=(
                "This order was placed directly as a Purchase Order,\n"
                "without a Request to Quote or a quote from the manufacturer."
            ),
        ))
        return _finish(order, brand, record_context, parts, number)

    parts.append(document_pdf(order, kind="request", brand=brand))
    number += 1

    # 2. What the manufacturer sent back — every reply that had a file.
    title = section("Manufacturer's quote")
    replies = [
        revision for revision in order.revisions.filter(stage=OrderRevision.Stage.QUOTE).order_by("number")
        if revision.attachment
    ]
    if replies:
        for index, revision in enumerate(replies, start=1):
            label = title if len(replies) == 1 else f"{title} — reply {index} of {len(replies)}"
            parts += _attachment_parts(
                brand, label, revision.attachment, when=revision.created_at,
                missing="The file attached to this reply is no longer on the server.",
            )
    elif order.quote_file:
        parts += _attachment_parts(
            brand, title, order.quote_file, when=order.quoted_at,
            missing="Their quote file is no longer on the server.",
        )
    else:
        parts.append(_page(brand, title, note="No quote document was attached."))

    return _finish(order, brand, record_context, parts, number)


def _finish(order, brand, record_context, parts, number):
    """Purchase Order onwards, then the summary & conversation."""

    def section(title):
        nonlocal number
        number += 1
        return f"{number}. {title}"

    if not order.is_quote:
        # 3. Our Purchase Order.
        parts.append(document_pdf(order, kind="purchase_order", brand=brand))
        number += 1

        payments = list(order.payments.all())
        advances = [payment for payment in payments if payment.kind == payment.Kind.ADVANCE]
        finals = [payment for payment in payments if payment.kind == payment.Kind.FINAL]

        # 4. The advance — only when one was paid.
        if advances:
            title = section("Advance payment proof")
            for payment in advances:
                parts += _attachment_parts(
                    brand, title, payment.proof,
                    subtitle=_paid_line(payment),
                    missing="No proof was attached to this payment.",
                )

        # 5. The manufacturer's invoice / receipt that came with the goods.
        if order.invoice_approved_at:
            title = section("Manufacturer's invoice")
            ref = f" (their no. {order.supplier_invoice_ref})" if order.supplier_invoice_ref else ""
            parts += _attachment_parts(
                brand, title + ref, order.invoice_file, when=order.invoice_approved_at,
                missing=f"Invoice {order.invoice_number} was recorded without their document.",
            )

        # 6. Proof of the final payment.
        if finals:
            title = section("Payment proof")
            for payment in finals:
                parts += _attachment_parts(
                    brand, title, payment.proof,
                    subtitle=_paid_line(payment),
                    missing="No proof was attached to this payment.",
                )

    # 7. The summary and the whole conversation, last.
    parts.append(render_pdf(
        "manufacturers/manufacturer_order_record_pdf.html",
        {**record_context, "section_number": number + 1},
    ))

    writer = PdfWriter()
    for part in parts:
        writer.append(PdfReader(BytesIO(part)))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
