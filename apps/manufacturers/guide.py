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


# The steps of each workflow, in order. A direct Purchase Order starts at
# "po"; a Request to Quote has two steps before it. From the Purchase
# Order on, both are the same.
STEP_INFO = {
    "rtq": ("Request to Quote", "You ask the manufacturer for prices"),
    "quote": ("Quote", "The manufacturer's prices arrive and you agree on them"),
    "po": ("Purchase Order", "Generate and print the Purchase Order"),
    "stamped": ("Stamped PO", "Upload a photo of the Purchase Order with the official stamp"),
    "advance": ("Payment Proof", "Say whether you paid an advance, and upload the proof"),
    "invoice": ("Invoice", "The goods and their invoice arrive and go into stock"),
    "final": ("Payment Proof", "Pay the balance and upload its proof"),
}
SHARED_STEPS = ["po", "stamped", "advance", "invoice", "final"]
WORKFLOW_STEPS = {
    ManufacturerOrder.Workflow.PURCHASE_ORDER: SHARED_STEPS,
    ManufacturerOrder.Workflow.REQUEST_TO_QUOTE: ["rtq", "quote"] + SHARED_STEPS,
}


def steps_for(order):
    return WORKFLOW_STEPS.get(order.workflow, SHARED_STEPS)


def current_step(order):
    """The key of the step the order is on, or "done"."""
    if order.status == ManufacturerOrder.Status.REFUNDED:
        return "done"
    if order.is_quote:
        return "rtq" if order.quoted_at is None else "quote"
    if order.invoice_approved_at is None:
        if order.status == ManufacturerOrder.Status.SENT:
            if order.pays_advance is None:
                return "stamped" if not order.stamped_po_file else "advance"
            return "invoice"
        return "invoice"
    return "done"


def _step_date(order, key):
    payments = list(order.payments.all())
    if key == "rtq":
        return order.created_at
    if key == "quote":
        return order.quoted_at
    if key == "po":
        return order.purchase_order_date
    if key == "stamped":
        return order.stamped_po_uploaded_at
    if key == "advance":
        advance = [p.paid_at for p in payments if p.kind == p.Kind.ADVANCE]
        return advance[0] if advance else None
    if key == "invoice":
        return order.invoice_approved_at
    final = [p.paid_at for p in payments if p.kind == p.Kind.FINAL]
    return final[-1] if final else None


def _done_note(order, key):
    if key == "stamped" and not order.stamped_po_file:
        return "Not recorded"  # orders from before this step existed
    if key == "advance" and order.pays_advance is False:
        return "No advance"
    if key == "final" and order.invoice_approved_at and not any(
        p.kind == p.Kind.FINAL for p in order.payments.all()
    ):
        return "Paid in advance"
    return ""


def workflow_steps(order, current=None):
    """The order's steps, each {number, key, title, plain, help, state,
    date, done_note}, state being "done", "current" or "upcoming".
    current overrides which step is highlighted (a step page)."""
    keys = steps_for(order)
    at = current or current_step(order)
    position = keys.index(at) if at in keys else len(keys)
    steps = []
    for index, key in enumerate(keys):
        title, plain = STEP_INFO[key]
        state = "done" if index < position else "current" if index == position else "upcoming"
        steps.append({
            "number": index + 1,
            "key": key,
            "title": title,
            "plain": plain,
            "help": plain,
            "state": state,
            "date": _step_date(order, key) if state == "done" else None,
            "done_note": _done_note(order, key) if state == "done" else "",
            "note": _quote_note(order) if key == "quote" and state == "current" else plain,
        })
    return steps


def _quote_note(order):
    from .models import OrderRevision

    if awaiting_manufacturer_reply(order):
        return "Waiting for the manufacturer's reply"
    stages = list(order.revisions.values_list("stage", flat=True))
    quotes = stages.count(OrderRevision.Stage.QUOTE)
    counters = stages.count(OrderRevision.Stage.COUNTER)
    if counters:
        return (
            f"Negotiating — {quotes} repl{'y' if quotes == 1 else 'ies'}, "
            f"{counters} counter-offer{'' if counters == 1 else 's'}"
        )
    return "Review the manufacturer's prices"


def step_position(order):
    """(1-based position of the current step, number of steps); the
    position is one past the last step once the order is complete."""
    keys = steps_for(order)
    at = current_step(order)
    return (keys.index(at) + 1 if at in keys else len(keys) + 1), len(keys)


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

    known = getattr(order, "_has_unallocated_stock", None)
    if known is not None:
        return known

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

    stamped_text = (
        f"Stamped Purchase Order uploaded {order.stamped_po_uploaded_at:%d %b %Y}"
        if order.stamped_po_uploaded_at else "Stamped Purchase Order uploaded"
    )

    if stage == 3 and order.pays_advance is None and not order.stamped_po_file:
        guide.update(
            heading="Upload the Stamped Purchase Order",
            waiting_on=YOU,
            summary=(
                "Print the Purchase Order, have it stamped, then upload a photo of the "
                "stamped copy. Payment proof comes after this."
            ),
            checklist=[
                _check(sent_text, True),
                _check("Upload a photo of the stamped Purchase Order", False),
                _check("Say whether you paid an advance (and upload the proof)", False),
            ],
            primary=_link("Upload Stamped Purchase Order", reverse("manufacturer-order-stamped", args=[pk])),
            secondary=[
                _link("Print Purchase Order", pdf_url, new_tab=True),
                _link("Download Purchase Order", f"{pdf_url}?download=1"),
            ],
        )
    elif stage == 3 and order.pays_advance is None:
        guide.update(
            heading="Record Your Advance Payment",
            waiting_on=YOU,
            summary=(
                f"The order is confirmed. Download the Purchase Order and send it to "
                f"{manufacturer}, then say whether you paid them anything in advance."
            ),
            checklist=[
                _check(sent_text, True),
                _check(stamped_text, True),
                _check("Say whether you paid an advance (and upload the proof)", False),
                _check("When the goods arrive, record the invoice", False),
            ],
            primary=_link("Record Advance Payment", reverse("manufacturer-order-advance", args=[pk])),
            secondary=[
                _link("Download Purchase Order", f"{pdf_url}?download=1"),
                _link("Replace Stamped PO Photo", reverse("manufacturer-order-stamped", args=[pk])),
            ],
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


def annotate_list_status(orders):
    """list_status for a whole list of orders in two queries in all —
    instead of one or two per order — and stores it on each order as
    stage_number, next_text, waiting_on. Returns the orders."""
    from apps.owner_inventory.models import StockBatch

    from .models import OrderRevision

    orders = list(orders)
    ids = [order.pk for order in orders]

    unallocated = set(
        StockBatch.objects.filter(
            source_batch__manufacturer_order_item__order__in=ids,
            location__location_type=Location.LocationType.UNALLOCATED,
            quantity_remaining__gt=0,
        ).values_list("source_batch__manufacturer_order_item__order", flat=True)
    )

    latest_stage = {}
    quote_ids = [order.pk for order in orders if order.is_quote]
    for order_id, stage in (
        OrderRevision.objects.filter(order__in=quote_ids)
        .order_by("order_id", "-number")
        .values_list("order_id", "stage")
    ):
        latest_stage.setdefault(order_id, stage)

    for order in orders:
        order._has_unallocated_stock = order.pk in unallocated
        if order.is_quote:
            order._latest_revision_stage = latest_stage.get(order.pk)
        order.stage_number, order.next_text, order.waiting_on = list_status(order)
        order.step_total = len(steps_for(order))
        order.step_range = range(1, order.step_total + 1)
    return orders


def list_status(order):
    """(position of the current step in the order's workflow — one past
    the last once complete — short "what's next" text, whose turn) for a
    row on the orders list."""
    _stage, text, turn = _list_status(order)
    return step_position(order)[0], text, turn


def _list_status(order):
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
        if order.pays_advance is None and not order.stamped_po_file:
            return 3, "Upload the stamped Purchase Order", YOU
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

