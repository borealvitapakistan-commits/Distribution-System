"""Plain-language guidance for a Manufacturer order: which of the four
stages it is at, whose turn it is, and the one thing to do next.

Every page in the order's flow reads from here, so the order page, the
list and the step pages all tell the user the same story in the same
words."""

from django.urls import reverse

from apps.owner_warehouse.models import Location

from .models import ManufacturerOrder
from .services import awaiting_manufacturer_reply

STAGES = [
    {
        "number": 1,
        "title": "Request to Quote",
        "plain": "You ask the manufacturer for prices",
        "help": (
            "A list of the products and quantities you want, sent to the "
            "manufacturer so they can tell you their prices. Nothing is "
            "ordered or paid yet."
        ),
    },
    {
        "number": 2,
        "title": "Quote",
        "plain": "You agree on the prices",
        "help": (
            "The manufacturer's reply with their prices. If a price is too "
            "high you can send a counter-offer; when you're happy, accept it."
        ),
    },
    {
        "number": 3,
        "title": "Purchase Order",
        "plain": "You confirm the order and pay any advance",
        "help": (
            "Your official order at the agreed prices. Send it to the "
            "manufacturer and record any advance payment you make."
        ),
    },
    {
        "number": 4,
        "title": "Invoice",
        "plain": "The goods arrive and go into stock",
        "help": (
            "When the goods and the manufacturer's bill arrive, record what "
            "actually came and pay the balance. The stock then goes into "
            "Inventory, where you split it across your warehouses."
        ),
    },
]

YOU = "you"
MANUFACTURER = "manufacturer"
NOBODY = ""


def current_stage(order):
    """1-4 for the stage in progress, 5 once everything is done."""
    if order.is_quote:
        return 1 if order.quoted_at is None else 2
    if order.invoice_approved_at is None:
        return 3 if order.status == ManufacturerOrder.Status.SENT else 4
    return 5


def _link(label, url, *, new_tab=False):
    return {"label": label, "url": url, "new_tab": new_tab}


def _check(text, done):
    return {"text": text, "done": done}


def _unallocated_stock(order):
    from apps.owner_inventory.models import StockBatch

    return StockBatch.objects.filter(
        source_batch__manufacturer_order_item__order=order,
        location__location_type=Location.LocationType.UNALLOCATED,
        quantity_remaining__gt=0,
    ).exists()


def next_step_guide(order, **options):
    """What the "What to do now" card on the order page says — see
    _build_guide. The first unfinished checklist item is marked "next"."""
    guide = _build_guide(order, **options)
    for check in guide["checklist"]:
        if not check["done"]:
            check["next"] = True
            break
    return guide


def _build_guide(order, *, can_edit_quote=False, can_edit_po=False):
    """What the "What to do now" card on the order page says.

    heading     — the step in a few words
    waiting_on  — YOU, MANUFACTURER or NOBODY: whose move it is
    summary     — one plain sentence
    checklist   — [{text, done}] — where this stage has got to
    primary     — the one main button, or None
    secondary   — smaller links
    choices     — for the quote review only: the options side by side
    """
    manufacturer = order.manufacturer.name
    pk = order.pk
    quote_url = reverse("manufacturer-order-quote", args=[pk])
    pdf_url = reverse("manufacturer-order-pdf", args=[pk])

    guide = {
        "heading": "",
        "waiting_on": NOBODY,
        "summary": "",
        "checklist": [],
        "primary": None,
        "secondary": [],
        "choices": [],
        "accept_form": False,
        "contact": None,
    }

    if order.status == ManufacturerOrder.Status.REFUNDED:
        guide.update(
            heading="Order refunded",
            summary="This order was refunded, so there's nothing left to do.",
        )
        return guide

    stage = current_stage(order)

    if stage == 1:
        guide.update(
            heading="Record Manufacturer's Reply",
            waiting_on=MANUFACTURER,
            summary=(
                f"You're asking {manufacturer} for their prices. Send them the "
                "Request to Quote, then come back here when they reply."
            ),
            checklist=[
                _check("Request to Quote created", True),
                _check(f"Download it and send it to {manufacturer}", False),
                _check("When they reply with prices, click Next and type them in", False),
            ],
            primary=_link("Next", quote_url),
            secondary=[_link("Download Request to Quote", f"{pdf_url}?download=1")],
            contact=order.manufacturer,
        )
        if can_edit_quote:
            guide["secondary"].append(
                _link("Edit Request to Quote", reverse("manufacturer-order-edit", args=[pk]))
            )
        return guide

    if stage == 2 and awaiting_manufacturer_reply(order):
        guide.update(
            heading="Record Manufacturer's Reply",
            waiting_on=MANUFACTURER,
            summary=(
                f"You sent {manufacturer} a counter-offer. When they answer, click "
                "Next — if they agreed, just save with the prices already filled in."
            ),
            checklist=[
                _check(f"{manufacturer} sent their prices", True),
                _check("You sent a counter-offer", True),
                _check("Record their answer", False),
            ],
            primary=_link("Next", quote_url),
            secondary=[_link(
                "See the conversation", reverse("manufacturer-order-conversation", args=[pk])
            )],
            contact=order.manufacturer,
        )
        return guide

    if stage == 2:
        highlights = [item.price_highlight for item in order.items.all()]
        changed_count = highlights.count("changed")
        new_price_count = highlights.count("new")
        if changed_count or new_price_count:
            parts = []
            if changed_count:
                parts.append(
                    f"{changed_count} price{'s' if changed_count != 1 else ''} "
                    "differ from what you asked (orange)"
                )
            if new_price_count:
                parts.append(
                    f"{new_price_count} price{'s' if new_price_count != 1 else ''} "
                    "filled in where you left it empty (yellow)"
                )
            summary = f"{manufacturer} replied: " + "; ".join(parts) + ". Check them below, then choose what to do."
        else:
            summary = f"{manufacturer} accepted every price you asked for. Choose what to do."
        guide.update(
            heading="Review the Manufacturer's Prices",
            waiting_on=YOU,
            summary=summary,
            choices=[
                {
                    "title": "Happy with the prices?",
                    "text": "Accept them. This turns the quote into a Purchase Order to send to the manufacturer.",
                    "accept": True,
                },
                {
                    "title": "Want a better price?",
                    "text": "Send a counter-offer with the prices you'd like instead.",
                    "link": _link("Send Counter-offer", reverse("manufacturer-order-counter", args=[pk])),
                },
                {
                    "title": "They sent another reply?",
                    "text": "Record their newer prices — the latest reply is what you accept.",
                    "link": _link("Record Another Reply", quote_url),
                },
            ],
            accept_form=True,
        )
        return guide

    po_sent = order.purchase_order_date
    sent_text = (
        f"Purchase Order sent {po_sent:%d %b %Y}" if po_sent else "Purchase Order sent"
    )

    if stage == 3 and order.pays_advance is None:
        guide.update(
            heading="Record Your Advance Payment",
            waiting_on=YOU,
            summary=(
                f"The order is confirmed. Download the Purchase Order and send it to "
                f"{manufacturer}, then say whether you paid them anything in advance."
            ),
            checklist=[
                _check(sent_text, True),
                _check("Say whether you paid an advance (and upload the proof)", False),
                _check("When the goods arrive, record the invoice", False),
            ],
            primary=_link("Record Advance Payment", reverse("manufacturer-order-advance", args=[pk])),
            secondary=[_link("Download Purchase Order", f"{pdf_url}?download=1")],
        )
    elif stage == 3:
        guide.update(
            heading="Wait for the Goods",
            waiting_on=MANUFACTURER,
            summary=(
                f"{manufacturer} is preparing your order. When the goods and their "
                "invoice arrive, record them here — that pays the balance and adds the stock."
            ),
            checklist=[
                _check(sent_text, True),
                _check("Advance paid" if order.pays_advance else "No advance paid", True),
                _check("Goods and invoice arrived", False),
            ],
            primary=_link("Goods Arrived — Record Invoice", reverse("manufacturer-order-received", args=[pk])),
            secondary=[_link("Download Purchase Order", f"{pdf_url}?download=1")],
        )
    elif stage == 4:
        guide.update(
            heading="Add the Stock to Inventory",
            waiting_on=YOU,
            summary="The goods were received but their invoice was never recorded, so the stock isn't in Inventory yet.",
            checklist=[
                _check(sent_text, True),
                _check("Goods received", True),
                _check("Record the invoice to add the stock", False),
            ],
            primary=_link("Record Invoice", reverse("manufacturer-order-received", args=[pk])),
        )
    else:
        unallocated = _unallocated_stock(order)
        guide.update(
            heading="Split the Stock Across Warehouses" if unallocated else "Order Complete",
            waiting_on=YOU if unallocated else NOBODY,
            summary=(
                "The order is paid and invoiced. Its batches are waiting in Inventory — "
                "split each one across your regions and warehouses."
                if unallocated
                else "Every stage is done: the order is paid, invoiced and in stock."
            ),
            checklist=[
                _check(sent_text, True),
                _check(
                    f"Invoice {order.invoice_number} recorded" if order.invoice_number else "Invoice recorded",
                    True,
                ),
                _check("Stock placed in warehouses", not unallocated),
            ],
            primary=(
                _link("Go to Inventory", reverse("inventory-list"))
                if unallocated
                else _link("Download Full Order Record", reverse("manufacturer-order-record-pdf", args=[pk]))
            ),
            secondary=[_link("See the conversation", "#conversation")],
        )
        if unallocated:
            guide["secondary"].insert(
                0,
                _link("Download Full Order Record", reverse("manufacturer-order-record-pdf", args=[pk])),
            )

    if can_edit_po and stage == 3:
        guide["secondary"].append(
            _link("Change Purchase Order", reverse("manufacturer-order-change", args=[pk]))
        )
    return guide


def list_status(order):
    """(stage number 1-5, short "what's next" text, whose turn) for a row
    on the orders list."""
    if order.status == ManufacturerOrder.Status.REFUNDED:
        return 5, "Refunded", NOBODY

    stage = current_stage(order)
    if stage == 1:
        return 1, "Waiting for the manufacturer's prices", MANUFACTURER
    if stage == 2:
        if awaiting_manufacturer_reply(order):
            return 2, "Waiting for their answer to your counter-offer", MANUFACTURER
        return 2, "Review the prices they sent", YOU
    if stage == 3:
        if order.pays_advance is None:
            return 3, "Record your advance payment", YOU
        return 3, "Waiting for the goods to arrive", MANUFACTURER
    if stage == 4:
        return 4, "Record the invoice", YOU
    if _unallocated_stock(order):
        return 5, "Split the stock across warehouses", YOU
    if order.status == ManufacturerOrder.Status.DISPUTED:
        return 5, "Complete — under discussion", NOBODY
    return 5, "Complete", NOBODY

