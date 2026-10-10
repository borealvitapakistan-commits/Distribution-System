from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import require_approved_distributor, require_owner
from apps.agreements.models import discounted_price
from apps.agreements.services import agreement_in_force
from apps.audit.services import record_audit_event
from apps.distributor_inventory.services import receive_stock as distributor_receive_stock
from apps.owner_inventory.services import (
    release_stock_from_transit,
    ship_stock_to_transit,
)

from .models import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderPayment,
    PurchaseOrderRevision,
    PurchaseOrderRevisionLine,
)


def _as_decimal(quantity):
    try:
        return Decimal(quantity)
    except (InvalidOperation, TypeError):
        raise ValidationError("Quantity must be a number.")


def _validate_items(items):
    """Each row: product, quantity_requested and, on a Request to Quote,
    an optional unit_price — the price the Distributor asks for (blank or
    0 = "please quote")."""
    if not items:
        raise ValidationError("A purchase order needs at least one product.")

    rows = []
    seen = set()

    for row in items:
        product = row.get("product")
        quantity = row.get("quantity_requested")
        unit_price = row.get("unit_price")

        if not product:
            raise ValidationError("Every row needs a product.")

        if product.pk in seen:
            raise ValidationError(
                "A product can appear only once on a purchase order."
            )

        seen.add(product.pk)

        quantity = _as_decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        if unit_price in (None, ""):
            unit_price = None
        else:
            unit_price = _as_decimal(unit_price)
            if unit_price < 0:
                raise ValidationError("Unit price cannot be negative.")
            if unit_price == 0:
                unit_price = None

        rows.append(
            {
                "product": product,
                "quantity_requested": quantity,
                "unit_price": unit_price,
            }
        )

    return rows


def discount_for_price(list_price, unit_price):
    """The discount off list price that a typed price works out to —
    kept on the line so it still reads as "list less X%". A price above
    list counts as no discount."""
    if unit_price is None or not list_price:
        return Decimal("0")
    percentage = ((list_price - unit_price) * Decimal("100") / list_price).quantize(
        Decimal("0.01")
    )
    return min(max(percentage, Decimal("0")), Decimal("100"))


def _rows_payload(lines):
    return [
        {
            "product_id": str(line.product_id),
            "quantity_requested": str(line.quantity_requested),
            "list_price": str(line.list_price),
            "discount_percentage": str(line.discount_percentage),
            "requested_unit_price": (
                None if line.requested_unit_price is None else str(line.requested_unit_price)
            ),
            "unit_price": None if line.unit_price is None else str(line.unit_price),
        }
        for line in lines
    ]


def _line_snapshot(item, *, unit_price=None, quantity=None, removed=False):
    """One line of a PurchaseOrderRevision, taken from a live order line
    — with this step's price/quantity when they differ from the item's
    own (a counter-offer doesn't change the order, only proposes)."""
    return {
        "item": None if removed else item,
        "product": item.product,
        "quantity": item.quantity_ordered if quantity is None else quantity,
        "unit_price": item.unit_price if unit_price is None else unit_price,
        "removed": removed,
    }


def _current_lines(purchase_order):
    return [
        _line_snapshot(item)
        for item in purchase_order.items.select_related("product")
    ]


def _record_revision(
    *, actor, purchase_order, stage, by_owner, lines, message="", attachment=None
):
    """Freezes one step of the conversation: the lines as they stand now
    and what was said. The order must already be locked
    (select_for_update) so the numbering can't collide."""
    last = (
        purchase_order.revisions.order_by("-number")
        .values_list("number", flat=True)
        .first()
    )

    revision = PurchaseOrderRevision(
        purchase_order=purchase_order,
        number=(last or 0) + 1,
        stage=stage,
        by_owner=by_owner,
        message=(message or "").strip(),
        attachment=attachment or None,
        created_by=actor,
        updated_by=actor,
    )
    revision.full_clean()
    revision.save()

    PurchaseOrderRevisionLine.objects.bulk_create(
        [
            PurchaseOrderRevisionLine(
                revision=revision,
                created_by=actor,
                updated_by=actor,
                **line,
            )
            for line in lines
        ]
    )

    return revision


def awaiting_owner_reply(purchase_order):
    """On a Request to Quote: True while the Distributor's request or
    counter-offer is the last thing said, so the Owner has to answer."""
    if not purchase_order.is_quote:
        return False
    latest = purchase_order.revisions.order_by("-number").first()
    return latest is None or latest.stage in (
        PurchaseOrderRevision.Stage.REQUEST,
        PurchaseOrderRevision.Stage.COUNTER,
    )


def awaiting_distributor_reply(purchase_order):
    """On a Request to Quote: True while the Owner's quote is the last
    thing said — the Distributor accepts it or counters."""
    if not purchase_order.is_quote:
        return False
    latest = purchase_order.revisions.order_by("-number").first()
    return latest is not None and latest.stage == PurchaseOrderRevision.Stage.QUOTE


def _next_po_number():
    last = PurchaseOrder.objects.order_by("-created_at").first()
    next_seq = 1

    if last is not None:
        try:
            next_seq = int(last.po_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_seq = PurchaseOrder.objects.count() + 1

    return f"PO-{next_seq:05d}"


def _create_order_lines(*, actor, purchase_order, rows, agreement, as_quote):
    """Each line remembers its list price. On a Request to Quote its
    price is whatever the Distributor asked for (blank = please quote);
    on an order placed directly it's the agreement's price."""
    lines = []

    for row in rows:
        product = row["product"]
        agreement_discount = agreement.discount_for(product) if agreement else Decimal("0")

        if as_quote:
            requested = row.get("unit_price")
            discount = (
                agreement_discount if requested is None
                else discount_for_price(product.base_retail_price, requested)
            )
            unit_price = requested
        else:
            requested = None
            discount = agreement_discount
            unit_price = discounted_price(product.base_retail_price, discount)

        lines.append(
            PurchaseOrderItem(
                purchase_order=purchase_order,
                product=product,
                quantity_requested=row["quantity_requested"],
                list_price=product.base_retail_price,
                discount_percentage=discount,
                requested_unit_price=requested,
                unit_price=unit_price,
                created_by=actor,
                updated_by=actor,
            )
        )

    for line in lines:
        line.full_clean()

    return PurchaseOrderItem.objects.bulk_create(lines)


@transaction.atomic
def create_purchase_order(*, actor, items, message=""):
    """Places a Purchase Order directly at the agreement's prices,
    skipping the Request to Quote — kept for the API and for older
    integrations. The Distributor's pages always start with a Request to
    Quote (create_request_to_quote)."""
    require_approved_distributor(actor)

    rows = _validate_items(items)

    distributor_profile = actor.distributor_profile

    # The agreement is checked first: only one signed for this Distributor
    # and covering today prices the order; otherwise list prices apply.
    agreement = agreement_in_force(distributor_profile)

    purchase_order = PurchaseOrder(
        distributor_profile=distributor_profile,
        agreement=agreement,
        po_number=_next_po_number(),
        po_placed_at=timezone.now(),
        created_by=actor,
        updated_by=actor,
    )
    purchase_order.full_clean()
    purchase_order.save()

    lines = _create_order_lines(
        actor=actor,
        purchase_order=purchase_order,
        rows=rows,
        agreement=agreement,
        as_quote=False,
    )
    _record_revision(
        actor=actor,
        purchase_order=purchase_order,
        stage=PurchaseOrderRevision.Stage.PURCHASE_ORDER,
        by_owner=False,
        lines=[_line_snapshot(line) for line in lines],
        message=message,
    )

    record_audit_event(
        user=actor,
        action="requests.purchase_order_created",
        instance=purchase_order,
        after_data={
            "po_number": purchase_order.po_number,
            "distributor_profile_id": str(distributor_profile.pk),
            "agreement_id": str(agreement.pk) if agreement else None,
            "items": _rows_payload(lines),
        },
    )

    return purchase_order


@transaction.atomic
def create_request_to_quote(*, actor, items, message=""):
    """Step one, from the Distributor's panel: the products and quantities
    they want, at the prices they'd like (pre-filled from their agreement,
    each optional). The Owner answers it from their own panel with a
    quote — nothing is reserved, shipped or paid until the Distributor
    accepts that quote and it becomes a Purchase Order."""
    require_approved_distributor(actor)

    rows = _validate_items(items)
    distributor_profile = actor.distributor_profile
    agreement = agreement_in_force(distributor_profile)

    purchase_order = PurchaseOrder(
        distributor_profile=distributor_profile,
        agreement=agreement,
        po_number=_next_po_number(),
        status=PurchaseOrder.Status.QUOTE,
        created_by=actor,
        updated_by=actor,
    )
    purchase_order.full_clean()
    purchase_order.save()

    lines = _create_order_lines(
        actor=actor,
        purchase_order=purchase_order,
        rows=rows,
        agreement=agreement,
        as_quote=True,
    )
    _record_revision(
        actor=actor,
        purchase_order=purchase_order,
        stage=PurchaseOrderRevision.Stage.REQUEST,
        by_owner=False,
        lines=[_line_snapshot(line) for line in lines],
        message=message,
    )

    record_audit_event(
        user=actor,
        action="requests.quote_requested",
        instance=purchase_order,
        after_data={
            "po_number": purchase_order.po_number,
            "distributor_profile_id": str(distributor_profile.pk),
            "agreement_id": str(agreement.pk) if agreement else None,
            "items": _rows_payload(lines),
        },
    )

    return purchase_order


def _get_quote_for_update(purchase_order_id):
    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    if not purchase_order.is_quote:
        raise ValidationError(
            "This is no longer a Request to Quote — "
            + (
                "it was declined."
                if purchase_order.status == PurchaseOrder.Status.DECLINED
                else "it's already a Purchase Order."
            )
        )

    return purchase_order


def _get_own_quote_for_update(actor, purchase_order_id):
    purchase_order = _get_quote_for_update(purchase_order_id)

    if purchase_order.distributor_profile_id != actor.distributor_profile.pk:
        raise ValidationError("You can only manage your own purchase orders.")

    return purchase_order


@transaction.atomic
def update_request_to_quote(*, actor, purchase_order_id, items, message=None):
    """Rewrites a Request to Quote's lines — only until the Owner has
    answered it, since after that the asked-for prices are what the
    quote is compared against. Until then it's still the Distributor's
    draft, so its first step is rewritten in place."""
    require_approved_distributor(actor)

    purchase_order = _get_own_quote_for_update(actor, purchase_order_id)

    if purchase_order.quoted_at is not None:
        raise ValidationError(
            "The Owner has already quoted — answer their quote instead."
        )

    rows = _validate_items(items)

    purchase_order.updated_by = actor
    purchase_order.save(update_fields=["updated_at", "updated_by"])

    purchase_order.items.all().delete()
    lines = _create_order_lines(
        actor=actor,
        purchase_order=purchase_order,
        rows=rows,
        agreement=purchase_order.agreement,
        as_quote=True,
    )

    request = purchase_order.revisions.filter(
        stage=PurchaseOrderRevision.Stage.REQUEST
    ).first()

    if request is None:
        _record_revision(
            actor=actor,
            purchase_order=purchase_order,
            stage=PurchaseOrderRevision.Stage.REQUEST,
            by_owner=False,
            lines=[_line_snapshot(line) for line in lines],
            message=message or "",
        )
    else:
        request.lines.all().delete()
        PurchaseOrderRevisionLine.objects.bulk_create(
            [
                PurchaseOrderRevisionLine(
                    revision=request, created_by=actor, updated_by=actor,
                    **_line_snapshot(line),
                )
                for line in lines
            ]
        )
        if message is not None:
            request.message = message.strip()
        request.updated_by = actor
        request.save(update_fields=["message", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="requests.quote_request_updated",
        instance=purchase_order,
        after_data={"items": _rows_payload(lines)},
    )

    return purchase_order


@transaction.atomic
def send_quote(
    *, actor, purchase_order_id, item_updates, remove_item_ids=(), message="", attachment=None
):
    """The Owner answers the Distributor's Request to Quote (or their
    counter-offer) from the Owner's panel: a price — and quantity, if it
    changes — per line, with a message. Lines that can't be supplied can
    be dropped. The order's prices become this quote; the Distributor
    then accepts it or counters. Every quote is its own step in the
    conversation, so a revised quote can be sent at any time until the
    Distributor accepts.

    item_updates: {item_pk_as_str: {"unit_price": Decimal, "quantity": Decimal}}"""
    require_owner(actor)

    purchase_order = _get_quote_for_update(purchase_order_id)
    remove_item_ids = {str(pk) for pk in remove_item_ids}

    items = list(purchase_order.items.select_for_update().select_related("product"))

    if all(str(item.pk) in remove_item_ids for item in items):
        raise ValidationError(
            "At least one product must stay on the order — decline it instead."
        )

    changes = []
    dropped = []

    for item in items:
        if str(item.pk) in remove_item_ids:
            changes.append({"item_id": str(item.pk), "removed": True})
            dropped.append(_line_snapshot(item, removed=True))
            item.delete()
            continue

        update = item_updates.get(str(item.pk)) or {}
        unit_price = update.get("unit_price")
        quantity = update.get("quantity")

        if unit_price is None:
            raise ValidationError(f"Enter the price you're quoting for {item.product.name}.")

        item.unit_price = _as_decimal(unit_price)
        item.discount_percentage = discount_for_price(item.list_price, item.unit_price)
        if quantity is not None:
            item.quantity_requested = _as_decimal(quantity)

        item.updated_by = actor
        item.full_clean()
        item.save(
            update_fields=[
                "unit_price", "discount_percentage", "quantity_requested",
                "updated_at", "updated_by",
            ]
        )
        changes.append(
            {
                "item_id": str(item.pk),
                "requested_unit_price": (
                    None if item.requested_unit_price is None
                    else str(item.requested_unit_price)
                ),
                "unit_price": str(item.unit_price),
                "quantity": str(item.quantity_requested),
            }
        )

    purchase_order.quoted_at = timezone.now()
    purchase_order.updated_by = actor
    purchase_order.save(update_fields=["quoted_at", "updated_at", "updated_by"])

    revision = _record_revision(
        actor=actor,
        purchase_order=purchase_order,
        stage=PurchaseOrderRevision.Stage.QUOTE,
        by_owner=True,
        lines=_current_lines(purchase_order) + dropped,
        message=message,
        attachment=attachment,
    )

    record_audit_event(
        user=actor,
        action="requests.quote_sent",
        instance=purchase_order,
        after_data={"revision": revision.number, "items": changes},
    )

    return revision


@transaction.atomic
def record_counter_offer(
    *, actor, purchase_order_id, item_updates, message="", attachment=None
):
    """The Distributor answers the Owner's quote with the prices (and
    quantities) they want instead. This only proposes — the order's own
    prices stay at the Owner's latest quote until the Owner answers.

    item_updates: {item_pk_as_str: {"unit_price": Decimal, "quantity": Decimal}}"""
    require_approved_distributor(actor)

    purchase_order = _get_own_quote_for_update(actor, purchase_order_id)

    if not awaiting_distributor_reply(purchase_order):
        raise ValidationError(
            "A counter-offer answers the Owner's quote — wait for the Owner to reply first."
        )

    lines = []
    changes = []

    for item in purchase_order.items.select_related("product"):
        update = item_updates.get(str(item.pk)) or {}
        unit_price = update.get("unit_price")
        quantity = update.get("quantity")

        if unit_price is None:
            raise ValidationError(f"Enter the price you're offering for {item.product.name}.")

        unit_price = _as_decimal(unit_price)
        quantity = item.quantity_requested if quantity is None else _as_decimal(quantity)

        if unit_price < 0:
            raise ValidationError("Unit price cannot be negative.")
        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        lines.append(_line_snapshot(item, unit_price=unit_price, quantity=quantity))
        changes.append(
            {"item_id": str(item.pk), "unit_price": str(unit_price), "quantity": str(quantity)}
        )

    revision = _record_revision(
        actor=actor,
        purchase_order=purchase_order,
        stage=PurchaseOrderRevision.Stage.COUNTER,
        by_owner=False,
        lines=lines,
        message=message,
        attachment=attachment,
    )

    record_audit_event(
        user=actor,
        action="requests.counter_offer_sent",
        instance=purchase_order,
        after_data={"revision": revision.number, "items": changes},
    )

    return revision


@transaction.atomic
def accept_quote(*, actor, purchase_order_id, message=""):
    """Step three: the Distributor accepts the Owner's latest quote and
    the Request to Quote becomes their Purchase Order. From here the usual
    flow continues — advance payment, shipping, receiving, invoice.

    Not allowed while the Distributor's own counter-offer is still
    waiting for the Owner's answer."""
    require_approved_distributor(actor)

    purchase_order = _get_own_quote_for_update(actor, purchase_order_id)

    if not awaiting_distributor_reply(purchase_order):
        raise ValidationError(
            "There's no quote from the Owner to accept yet — wait for their reply."
        )

    lines = list(purchase_order.items.select_related("product"))

    if any(line.unit_price is None for line in lines):
        raise ValidationError("Every product needs a price before it can be ordered.")

    purchase_order.status = PurchaseOrder.Status.PENDING
    purchase_order.po_placed_at = timezone.now()
    purchase_order.updated_by = actor
    purchase_order.save(update_fields=["status", "po_placed_at", "updated_at", "updated_by"])

    _record_revision(
        actor=actor,
        purchase_order=purchase_order,
        stage=PurchaseOrderRevision.Stage.PURCHASE_ORDER,
        by_owner=False,
        lines=[_line_snapshot(line) for line in lines],
        message=message,
    )

    record_audit_event(
        user=actor,
        action="requests.quote_accepted",
        instance=purchase_order,
        after_data={
            "po_number": purchase_order.po_number,
            "grand_total": str(purchase_order.grand_total),
        },
    )

    return purchase_order


@transaction.atomic
def update_purchase_order(
    *, actor, purchase_order_id, item_updates, remove_item_ids=(), message=""
):
    """The Owner changes a placed Purchase Order — prices, quantities or
    dropping a line — as long as nothing has shipped and the invoice
    hasn't gone out. Every change is saved as a new version of the
    Purchase Order (v2, v3, ...), so the earlier ones stay visible to
    both sides.

    item_updates: {item_pk_as_str: {"unit_price": Decimal, "quantity": Decimal}}"""
    require_owner(actor)

    purchase_order = _get_open_order_for_update(purchase_order_id)

    if not purchase_order.can_change:
        raise ValidationError(
            "Only a Purchase Order with nothing shipped and no invoice yet can be changed."
        )

    remove_item_ids = {str(pk) for pk in remove_item_ids}
    items = list(purchase_order.items.select_for_update().select_related("product"))

    if all(str(item.pk) in remove_item_ids for item in items):
        raise ValidationError(
            "At least one product must stay on the order — decline it instead."
        )

    before = {"grand_total": str(purchase_order.grand_total)}
    changes = []
    dropped = []

    for item in items:
        if str(item.pk) in remove_item_ids:
            dropped.append(_line_snapshot(item, removed=True))
            changes.append({"item_id": str(item.pk), "removed": True})
            item.delete()
            continue

        update = item_updates.get(str(item.pk)) or {}
        unit_price = update.get("unit_price")
        quantity = update.get("quantity")

        if unit_price is None:
            raise ValidationError(f"Enter the price for {item.product.name}.")

        unit_price = _as_decimal(unit_price)
        quantity = item.quantity_requested if quantity is None else _as_decimal(quantity)

        if unit_price == item.unit_price and quantity == item.quantity_requested:
            continue

        item.unit_price = unit_price
        item.discount_percentage = discount_for_price(item.list_price, unit_price)
        item.quantity_requested = quantity
        item.updated_by = actor
        item.full_clean()
        item.save(
            update_fields=[
                "unit_price", "discount_percentage", "quantity_requested",
                "updated_at", "updated_by",
            ]
        )
        changes.append(
            {"item_id": str(item.pk), "unit_price": str(unit_price), "quantity": str(quantity)}
        )

    if not changes:
        raise ValidationError("Nothing was changed on the Purchase Order.")

    if purchase_order.grand_total < purchase_order.total_paid:
        raise ValidationError(
            f"The new total ({purchase_order.grand_total}) would be less than what's "
            f"already been paid ({purchase_order.total_paid})."
        )

    purchase_order.updated_by = actor
    purchase_order.save(update_fields=["updated_at", "updated_by"])

    revision = _record_revision(
        actor=actor,
        purchase_order=purchase_order,
        stage=PurchaseOrderRevision.Stage.PURCHASE_ORDER,
        by_owner=True,
        lines=_current_lines(purchase_order) + dropped,
        message=message,
    )

    record_audit_event(
        user=actor,
        action="requests.purchase_order_updated",
        instance=purchase_order,
        before_data=before,
        after_data={
            "revision": revision.number,
            "grand_total": str(purchase_order.grand_total),
            "items": changes,
        },
    )

    return revision


def invoice_prefix():
    """SI-<BRAND>- — the Owner's sales invoices to Distributors, e.g.
    SI-BOREAL-VITA-0001. A series of its own, apart from the INV- numbers
    given to Manufacturer invoices."""
    from django.utils.text import slugify

    from apps.core.models import Brand

    brand = Brand.objects.filter(active=True).first()
    code = slugify(brand.name).upper() if brand else ""
    return f"SI-{code}-" if code else "SI-"


def next_invoice_number():
    prefix = invoice_prefix()
    used = PurchaseOrder.objects.filter(invoice_number__startswith=prefix).values_list(
        "invoice_number", flat=True
    )
    numbers = [int(n[len(prefix):]) for n in used if n[len(prefix):].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


@transaction.atomic
def issue_invoice(
    *, actor, purchase_order_id, tax_percentage=None, shipping_amount=None, message=""
):
    """Step four: the Owner invoices the Distributor once everything that
    will ship has shipped. The invoice bills what was actually supplied —
    a line closed as unavailable only counts what went out — with the
    final tax and shipping, and gets its own number. After this the
    order's prices are final."""
    require_owner(actor)

    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    if purchase_order.is_invoiced:
        raise ValidationError(
            f"This order is already invoiced as {purchase_order.invoice_number}."
        )

    if not purchase_order.can_issue_invoice:
        raise ValidationError(
            "The invoice goes out once everything has shipped — ship the rest, "
            "or close what can't be supplied as unavailable."
        )

    if tax_percentage is not None:
        purchase_order.tax_percentage = _as_decimal(tax_percentage)

    if shipping_amount is not None:
        purchase_order.shipping_amount = _as_decimal(shipping_amount)

    if purchase_order.grand_total < purchase_order.total_paid:
        raise ValidationError(
            f"The invoice total ({purchase_order.grand_total}) would be less than "
            f"what's already been paid ({purchase_order.total_paid})."
        )

    purchase_order.invoice_number = next_invoice_number()
    purchase_order.invoiced_at = timezone.now()
    purchase_order.updated_by = actor
    purchase_order.full_clean()
    purchase_order.save(
        update_fields=[
            "invoice_number", "invoiced_at", "tax_percentage", "shipping_amount",
            "updated_at", "updated_by",
        ]
    )

    _record_revision(
        actor=actor,
        purchase_order=purchase_order,
        stage=PurchaseOrderRevision.Stage.INVOICE,
        by_owner=True,
        lines=[
            _line_snapshot(item, removed=item.quantity_ordered <= 0)
            for item in purchase_order.items.select_related("product")
        ],
        message=message or f"Invoice {purchase_order.invoice_number}",
    )

    record_audit_event(
        user=actor,
        action="requests.purchase_order_invoiced",
        instance=purchase_order,
        after_data={
            "invoice_number": purchase_order.invoice_number,
            "grand_total": str(purchase_order.grand_total),
        },
    )

    return purchase_order


def revision_timeline(purchase_order):
    """The order's whole conversation, oldest first, ready to show: each
    step with its lines compared against the step before it. A line is
    "changed" (orange) when its price moved, "new" (yellow) when a price
    was given where there was none, "" (white) when it's the same."""
    revisions = list(
        purchase_order.revisions
        .select_related("created_by")
        .prefetch_related("lines__product")
        .order_by("number")
    )

    timeline = []
    previous = {}
    po_version = 0

    for revision in revisions:
        label = revision.get_stage_display()
        if revision.stage == PurchaseOrderRevision.Stage.PURCHASE_ORDER:
            po_version += 1
            if po_version > 1:
                label = f"Purchase Order v{po_version}"

        rows = []
        current = {}

        for line in revision.lines.all():
            before = previous.get(line.line_key)
            highlight = ""
            if not line.removed and before is not None and line.unit_price is not None:
                if before.unit_price is None:
                    highlight = "new"
                elif before.unit_price != line.unit_price:
                    highlight = "changed"

            rows.append(
                {
                    "line": line,
                    "previous": before,
                    "highlight": highlight,
                    "quantity_changed": (
                        before is not None and before.quantity != line.quantity
                    ),
                }
            )

            if not line.removed:
                current[line.line_key] = line

        timeline.append(
            {
                "revision": revision,
                "label": label,
                "rows": rows,
                "changed_count": sum(1 for row in rows if row["highlight"]),
            }
        )
        previous = current

    return timeline


def order_steps(purchase_order):
    """The Request to Quote → Quote → Purchase Order → Invoice tracker at
    the top of an order, the same on both panels. Each step is "done",
    "current" or "upcoming".

    Quote is the back-and-forth between the Owner and the Distributor: it
    starts with the Owner's first quote and lasts until the Distributor
    accepts it."""
    po = purchase_order
    stages = list(po.revisions.order_by("number").values_list("stage", "created_at"))
    quotes = [at for stage, at in stages if stage == PurchaseOrderRevision.Stage.QUOTE]
    counters = sum(1 for stage, _ in stages if stage == PurchaseOrderRevision.Stage.COUNTER)
    po_versions = sum(
        1 for stage, _ in stages if stage == PurchaseOrderRevision.Stage.PURCHASE_ORDER
    )
    declined = po.status == PurchaseOrder.Status.DECLINED

    if po.declined_as_quote:
        states = (
            ["done", "current", "upcoming", "upcoming"] if po.quoted_at
            else ["current", "upcoming", "upcoming", "upcoming"]
        )
    elif po.is_quote and po.quoted_at is None:
        states = ["current", "upcoming", "upcoming", "upcoming"]
    elif po.is_quote:
        states = ["done", "current", "upcoming", "upcoming"]
    elif not po.is_invoiced:
        states = ["done", "done", "current", "upcoming"]
    else:
        states = ["done", "done", "done", "done"]

    if declined and po.declined_as_quote:
        quote_note = request_note = "Declined by the Owner"
    else:
        request_note = "Waiting for the Owner's quote"
        if awaiting_owner_reply(po) and counters:
            quote_note = "Waiting for the Owner's answer to the counter-offer"
        elif awaiting_distributor_reply(po):
            quote_note = "Waiting for the Distributor to accept or counter"
        else:
            quote_note = "Negotiating"
        if counters:
            quote_note += (
                f" · {len(quotes)} quote{'' if len(quotes) == 1 else 's'}, "
                f"{counters} counter-offer{'' if counters == 1 else 's'}"
            )

    if declined:
        po_note = "Declined by the Owner"
    elif po.status == PurchaseOrder.Status.RECEIVED:
        po_note = "Goods received"
    elif po.status in (PurchaseOrder.Status.SHIPPED, PurchaseOrder.Status.PARTIALLY_SHIPPED):
        po_note = po.get_status_display()
    else:
        po_note = "Being prepared by the Owner"
    if po_versions > 1:
        po_note += f" · v{po_versions}"

    invoice_note = "Ready to invoice" if po.can_issue_invoice else "After everything ships"

    # Older orders went straight to a Purchase Order, with no quote at all.
    skipped_quote = not po.is_quote and not po.declined_as_quote and po.quoted_at is None

    return [
        {
            "number": 1,
            "title": "Request to Quote",
            "state": states[0],
            "date": po.created_at,
            "note": request_note,
            "done_note": "",
        },
        {
            "number": 2,
            "title": "Quote",
            "state": states[1],
            "date": None if skipped_quote else (quotes[-1] if quotes else po.quoted_at),
            "note": quote_note if states[1] == "current" else "Upcoming",
            "done_note": "Ordered directly" if skipped_quote else "",
        },
        {
            "number": 3,
            "title": "Purchase Order",
            "state": states[2],
            "date": po.purchase_order_date,
            "note": po_note if states[2] == "current" else "Upcoming",
            "done_note": "",
        },
        {
            "number": 4,
            "title": "Invoice",
            "state": states[3],
            "date": po.invoiced_at,
            "note": invoice_note,
            "done_note": "",
        },
    ]


def _recompute_status(purchase_order):
    """An order stays open until every line has either arrived in full
    or been closed by the Owner as unavailable (after whatever part of
    it did ship arrived) - e.g. while the Owner waits on a Manufacturer
    for one product, the order sits at Partially shipped."""
    items = list(purchase_order.items.all())

    if purchase_order.status in (PurchaseOrder.Status.DECLINED, PurchaseOrder.Status.QUOTE):
        return

    if all(item.quantity_ordered == 0 for item in items):
        # Every product was closed as unavailable before any shipped.
        purchase_order.status = PurchaseOrder.Status.DECLINED
    elif all(item.is_complete for item in items):
        purchase_order.status = PurchaseOrder.Status.RECEIVED
    elif any(item.quantity_shipped > 0 for item in items):
        purchase_order.status = (
            PurchaseOrder.Status.PARTIALLY_SHIPPED
            if any(item.quantity_to_ship_remaining > 0 for item in items)
            else PurchaseOrder.Status.SHIPPED
        )
    else:
        purchase_order.status = PurchaseOrder.Status.PENDING

    purchase_order.save(update_fields=["status", "updated_at"])


def _require_payment_gate_satisfied(purchase_order):
    if purchase_order.upfront_amount_satisfied:
        return

    required = purchase_order.required_upfront_amount
    paid = purchase_order.total_paid
    pct = purchase_order.distributor_profile.upfront_payment_percentage

    raise ValidationError(
        f"This Distributor's terms require {pct}% "
        f"({required}) confirmed paid before shipping. "
        f"Only {paid} has been confirmed so far."
    )


@transaction.atomic
def ship_purchase_order_item(
    *,
    actor,
    item_id,
    allocations,
):
    """allocations: a list of (batch, quantity) pairs — the Owner can
    pull a shipment for one product line from several warehouses/batches
    in a single action, splitting the quantity however they want."""
    require_owner(actor)

    item = (
        PurchaseOrderItem.objects
        .select_for_update()
        .select_related("purchase_order", "purchase_order__distributor_profile")
        .filter(pk=item_id)
        .first()
    )

    if item is None:
        raise ValidationError("Purchase order line was not found.")

    purchase_order = item.purchase_order

    if purchase_order.status == PurchaseOrder.Status.DECLINED:
        raise ValidationError("This purchase order has been declined.")

    if purchase_order.is_quote:
        raise ValidationError(
            "This is still a Request to Quote — nothing ships until the "
            "Distributor accepts the quote."
        )

    _require_payment_gate_satisfied(purchase_order)

    allocations = [
        (batch, _as_decimal(quantity))
        for batch, quantity in (allocations or [])
        if batch is not None and _as_decimal(quantity) > 0
    ]

    if not allocations:
        raise ValidationError(
            "Enter a quantity to ship from at least one warehouse."
        )

    for batch, _quantity in allocations:
        if batch.product_id != item.product_id:
            raise ValidationError(
                "One of the selected batches is for a different product."
            )

    total_quantity = sum(quantity for _batch, quantity in allocations)

    if total_quantity > item.quantity_to_ship_remaining:
        raise ValidationError(
            f"Only {item.quantity_to_ship_remaining} remaining to ship on this line."
        )

    for batch, quantity in allocations:
        ship_stock_to_transit(
            actor=actor,
            product=item.product,
            quantity=quantity,
            from_location=batch.location,
            batch=batch,
            reference=f"Purchase order {purchase_order.po_number} shipped",
            shipped_for=item,
        )

    item.quantity_shipped += total_quantity
    item.updated_by = actor
    item.save(update_fields=["quantity_shipped", "updated_at", "updated_by"])

    _recompute_status(purchase_order)

    record_audit_event(
        user=actor,
        action="requests.purchase_order_item_shipped",
        instance=item,
        after_data={
            "quantity_sent": str(total_quantity),
            "quantity_shipped": str(item.quantity_shipped),
            "allocations": [
                {
                    "batch_id": str(batch.pk),
                    "location_id": str(batch.location_id),
                    "quantity": str(quantity),
                }
                for batch, quantity in allocations
            ],
        },
    )

    return item


@transaction.atomic
def receive_purchase_order_item(*, actor, item_id, quantity):
    require_approved_distributor(actor)

    item = (
        PurchaseOrderItem.objects
        .select_for_update()
        .select_related("purchase_order", "purchase_order__distributor_profile")
        .filter(pk=item_id)
        .first()
    )

    if item is None:
        raise ValidationError("Purchase order line was not found.")

    purchase_order = item.purchase_order

    if purchase_order.distributor_profile_id != actor.distributor_profile.pk:
        raise ValidationError("You can only receive your own purchase orders.")

    quantity = _as_decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    if quantity > item.quantity_to_receive_remaining:
        raise ValidationError(
            f"Only {item.quantity_to_receive_remaining} remaining to receive on this line."
        )

    reference = f"Purchase order {purchase_order.po_number} received"

    consumed = release_stock_from_transit(
        actor=actor,
        product=item.product,
        quantity=quantity,
        reference=reference,
        shipped_for=item,
    )

    # A receipt can span more than one originating batch (if separate
    # shipments landed in Transit) — each one lands in the Distributor's
    # own system carrying its own batch code and expiry forward.
    for batch, batch_quantity in consumed:
        distributor_receive_stock(
            actor=actor,
            distributor_profile=purchase_order.distributor_profile,
            product=item.product,
            quantity=batch_quantity,
            reference=reference,
            batch_number=batch.batch_number,
            expiry_date=batch.expiry_date,
            require_distributor_actor=False,
        )

    item.quantity_received += quantity
    item.updated_by = actor
    item.save(update_fields=["quantity_received", "updated_at", "updated_by"])

    _recompute_status(purchase_order)

    record_audit_event(
        user=actor,
        action="requests.purchase_order_item_received",
        instance=item,
        after_data={
            "quantity_received_now": str(quantity),
            "quantity_received": str(item.quantity_received),
        },
    )

    return item


@transaction.atomic
def update_pricing(*, actor, purchase_order_id, tax_percentage, shipping_amount):
    require_owner(actor)

    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    if purchase_order.is_invoiced:
        raise ValidationError(
            f"Invoice {purchase_order.invoice_number} is already issued — its "
            "tax and shipping are final."
        )

    purchase_order.tax_percentage = _as_decimal(tax_percentage)
    purchase_order.shipping_amount = _as_decimal(shipping_amount)
    purchase_order.updated_by = actor
    purchase_order.full_clean()
    purchase_order.save(
        update_fields=[
            "tax_percentage", "shipping_amount", "updated_at", "updated_by",
        ]
    )

    record_audit_event(
        user=actor,
        action="requests.purchase_order_pricing_updated",
        instance=purchase_order,
        after_data={
            "tax_percentage": str(purchase_order.tax_percentage),
            "shipping_amount": str(purchase_order.shipping_amount),
        },
    )

    return purchase_order


def _get_open_order_for_update(purchase_order_id):
    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    if not purchase_order.is_open:
        raise ValidationError(
            f"This purchase order is {purchase_order.get_status_display().lower()} "
            "and can no longer be changed."
        )

    return purchase_order


@transaction.atomic
def update_line_discounts(*, actor, purchase_order_id, discounts):
    """The Owner keeps or changes each line's discount - e.g. the
    agreement gives 18% but Ashwagandha goes at 15% on this order.
    discounts: {item_id_as_str: percentage}. Only lines that haven't
    shipped yet can change; the unit price follows the percentage. Any
    change is saved as a new version of the Purchase Order."""
    require_owner(actor)

    purchase_order = _get_open_order_for_update(purchase_order_id)

    if purchase_order.is_quote:
        raise ValidationError(
            "This is still a Request to Quote — send your prices as a quote."
        )

    if purchase_order.is_invoiced:
        raise ValidationError("The invoice is already issued — its prices are final.")

    changes = {}

    for item in purchase_order.items.select_for_update().select_related("product"):
        raw = (discounts or {}).get(str(item.pk))

        if raw in (None, ""):
            continue

        percentage = _as_decimal(raw)

        if not Decimal("0") <= percentage <= Decimal("100"):
            raise ValidationError(f"{item.product.name}: discount must be between 0 and 100.")

        if percentage == item.discount_percentage:
            continue

        if not item.can_reprice:
            raise ValidationError(
                f"{item.product.name} has already shipped (or was closed), "
                "so its price is settled."
            )

        changes[str(item.pk)] = {
            "product_id": str(item.product_id),
            "from": str(item.discount_percentage),
            "to": str(percentage),
        }

        item.discount_percentage = percentage
        item.unit_price = discounted_price(item.list_price, percentage)
        item.updated_by = actor
        item.full_clean()
        item.save(
            update_fields=["discount_percentage", "unit_price", "updated_at", "updated_by"]
        )

    if changes:
        _record_revision(
            actor=actor,
            purchase_order=purchase_order,
            stage=PurchaseOrderRevision.Stage.PURCHASE_ORDER,
            by_owner=True,
            lines=_current_lines(purchase_order),
            message=f"Discount changed on {len(changes)} product{'' if len(changes) == 1 else 's'}.",
        )
        record_audit_event(
            user=actor,
            action="requests.purchase_order_discounts_updated",
            instance=purchase_order,
            after_data={"lines": changes},
        )

    return purchase_order


@transaction.atomic
def update_line_status(*, actor, item_id, note, unavailable):
    """The Owner's comment on one product line, and whether it's
    unavailable. Marking it unavailable drops whatever hasn't shipped
    from the order (needs a comment saying why); clearing the flag puts
    it back, e.g. once stock arrives from a Manufacturer after all."""
    require_owner(actor)

    item = (
        PurchaseOrderItem.objects
        .select_for_update()
        .select_related("product")
        .filter(pk=item_id)
        .first()
    )

    if item is None:
        raise ValidationError("Purchase order line was not found.")

    purchase_order = _get_open_order_for_update(item.purchase_order_id)

    if purchase_order.is_quote:
        raise ValidationError(
            "This is still a Request to Quote — drop the product or comment on it in your quote."
        )

    note = (note or "").strip()
    unavailable = bool(unavailable)

    if unavailable and not item.unavailable:
        if not note:
            raise ValidationError(
                f"Add a comment telling the Distributor why {item.product.name} isn't available."
            )

        if item.quantity_shipped >= item.quantity_requested:
            raise ValidationError(f"{item.product.name} has already shipped in full.")

    before = {"owner_note": item.owner_note, "unavailable": item.unavailable}

    item.owner_note = note
    item.unavailable = unavailable
    item.updated_by = actor
    item.save(update_fields=["owner_note", "unavailable", "updated_at", "updated_by"])

    _recompute_status(purchase_order)

    record_audit_event(
        user=actor,
        action="requests.purchase_order_line_updated",
        instance=item,
        before_data=before,
        after_data={"owner_note": note, "unavailable": unavailable},
    )

    return item


@transaction.atomic
def decline_purchase_order(*, actor, purchase_order_id, comment):
    require_owner(actor)

    comment = (comment or "").strip()

    if not comment:
        raise ValidationError("A comment is required to decline a purchase order.")

    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    if purchase_order.is_invoiced:
        raise ValidationError("This order is already invoiced and can't be declined.")

    purchase_order.status = PurchaseOrder.Status.DECLINED
    purchase_order.owner_comment = comment
    purchase_order.updated_by = actor
    purchase_order.save(
        update_fields=["status", "owner_comment", "updated_at", "updated_by"]
    )

    record_audit_event(
        user=actor,
        action="requests.purchase_order_declined",
        instance=purchase_order,
        after_data={"owner_comment": comment},
    )

    return purchase_order


@transaction.atomic
def add_owner_comment(*, actor, purchase_order_id, comment):
    require_owner(actor)

    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    purchase_order.owner_comment = (comment or "").strip()
    purchase_order.updated_by = actor
    purchase_order.save(update_fields=["owner_comment", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="requests.purchase_order_commented",
        instance=purchase_order,
        after_data={"owner_comment": purchase_order.owner_comment},
    )

    return purchase_order


@transaction.atomic
def record_payment(
    *,
    actor,
    purchase_order_id,
    amount,
    paid_at,
    proof,
    kind=PurchaseOrderPayment.Kind.FINAL,
    note="",
):
    """A Distributor's payment toward their order, always with proof. It
    only counts as paid once the Owner confirms it; it can't claim more
    than what's still owed."""
    require_approved_distributor(actor)

    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    if purchase_order.distributor_profile_id != actor.distributor_profile.pk:
        raise ValidationError("You can only record payments on your own purchase orders.")

    if purchase_order.is_quote:
        raise ValidationError(
            "This is still a Request to Quote — accept the quote before paying."
        )

    amount = _as_decimal(amount)

    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")

    if not proof:
        raise ValidationError("A proof of payment file is required.")

    if amount > purchase_order.remaining_amount:
        raise ValidationError(
            f"Only {purchase_order.remaining_amount} is left to pay on this order."
        )

    payment = PurchaseOrderPayment(
        purchase_order=purchase_order,
        kind=kind,
        amount=amount,
        paid_at=paid_at,
        proof=proof,
        note=(note or "").strip(),
        created_by=actor,
        updated_by=actor,
    )
    payment.full_clean()
    payment.save()

    record_audit_event(
        user=actor,
        action="requests.purchase_order_payment_recorded",
        instance=payment,
        after_data={
            "kind": kind,
            "amount": str(amount),
            "paid_at": str(paid_at),
            "status": payment.status,
        },
    )

    return payment


def _get_own_order_for_update(actor, purchase_order_id):
    purchase_order = (
        PurchaseOrder.objects
        .select_for_update()
        .filter(pk=purchase_order_id)
        .first()
    )

    if purchase_order is None:
        raise ValidationError("Purchase order was not found.")

    if purchase_order.distributor_profile_id != actor.distributor_profile.pk:
        raise ValidationError("You can only manage your own purchase orders.")

    return purchase_order


@transaction.atomic
def record_advance_decision(
    *, actor, purchase_order_id, pays_advance, amount=None, paid_at=None, proof=None, note=""
):
    """Step two of placing an order: "Are you paying in advance?" — asked
    once, before the Owner ships anything. Yes records the advance
    (anything up to the whole order) with its proof, for the Owner to
    confirm; No notes that the full amount is due on receipt."""
    require_approved_distributor(actor)

    purchase_order = _get_own_order_for_update(actor, purchase_order_id)

    if purchase_order.status != PurchaseOrder.Status.PENDING:
        raise ValidationError(
            "An advance can only be recorded before the order is shipped."
        )

    if purchase_order.pays_advance is not None:
        raise ValidationError("The advance payment has already been answered.")

    purchase_order.pays_advance = bool(pays_advance)
    purchase_order.updated_by = actor
    purchase_order.save(update_fields=["pays_advance", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="requests.purchase_order_advance_decided",
        instance=purchase_order,
        after_data={"pays_advance": purchase_order.pays_advance},
    )

    if purchase_order.pays_advance:
        return record_payment(
            actor=actor,
            purchase_order_id=purchase_order.pk,
            amount=amount,
            paid_at=paid_at,
            proof=proof,
            kind=PurchaseOrderPayment.Kind.ADVANCE,
            note=note,
        )

    return None


@transaction.atomic
def receive_purchase_order(
    *,
    actor,
    purchase_order_id,
    quantities,
    paid_remaining=False,
    amount=None,
    paid_at=None,
    proof=None,
    note="",
):
    """Step three: the goods have arrived. In one go, confirms what
    arrived on each line (quantities: {item_id: quantity}) — which puts
    each shipped batch into the Distributor's own stock — and, if
    anything is still owed and they've paid it, records that final
    payment with its proof. If any part fails, none of it is saved."""
    require_approved_distributor(actor)

    purchase_order = _get_own_order_for_update(actor, purchase_order_id)
    received_any = False

    for item in purchase_order.items.all():
        quantity = (quantities or {}).get(str(item.pk))

        if quantity:
            receive_purchase_order_item(actor=actor, item_id=item.pk, quantity=quantity)
            received_any = True

    if not received_any:
        raise ValidationError("Enter the quantity that arrived on at least one line.")

    purchase_order.refresh_from_db()

    if purchase_order.pays_advance is None:
        purchase_order.pays_advance = False
        purchase_order.save(update_fields=["pays_advance"])

    if paid_remaining and purchase_order.remaining_amount > 0:
        record_payment(
            actor=actor,
            purchase_order_id=purchase_order.pk,
            amount=amount,
            paid_at=paid_at,
            proof=proof,
            kind=PurchaseOrderPayment.Kind.FINAL,
            note=note,
        )

    return purchase_order


@transaction.atomic
def confirm_payment(*, actor, payment_id):
    """The Owner attesting that they actually saw this money land — the
    only thing that counts toward a Purchase Order's paid total and
    unblocks shipping. No bank integration; this is a manual sign-off."""
    require_owner(actor)

    payment = (
        PurchaseOrderPayment.objects
        .select_for_update()
        .select_related("purchase_order")
        .filter(pk=payment_id)
        .first()
    )

    if payment is None:
        raise ValidationError("Payment was not found.")

    if payment.status != PurchaseOrderPayment.Status.PENDING:
        raise ValidationError("Only a pending payment can be confirmed.")

    payment.status = PurchaseOrderPayment.Status.CONFIRMED
    payment.confirmed_at = timezone.now()
    payment.confirmed_by = actor
    payment.updated_by = actor
    payment.save(
        update_fields=[
            "status", "confirmed_at", "confirmed_by", "updated_at", "updated_by",
        ]
    )

    record_audit_event(
        user=actor,
        action="requests.purchase_order_payment_confirmed",
        instance=payment,
        after_data={"amount": str(payment.amount)},
    )

    return payment


@transaction.atomic
def reject_payment(*, actor, payment_id, reason):
    require_owner(actor)

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError("A reason is required to reject a payment.")

    payment = (
        PurchaseOrderPayment.objects
        .select_for_update()
        .filter(pk=payment_id)
        .first()
    )

    if payment is None:
        raise ValidationError("Payment was not found.")

    if payment.status != PurchaseOrderPayment.Status.PENDING:
        raise ValidationError("Only a pending payment can be rejected.")

    payment.status = PurchaseOrderPayment.Status.REJECTED
    payment.rejection_reason = reason
    payment.updated_by = actor
    payment.save(
        update_fields=["status", "rejection_reason", "updated_at", "updated_by"]
    )

    record_audit_event(
        user=actor,
        action="requests.purchase_order_payment_rejected",
        instance=payment,
        after_data={"reason": reason},
    )

    return payment


def _latest_revision_number(purchase_order):
    return (
        purchase_order.revisions.order_by("-number")
        .values_list("number", flat=True)
        .first()
    ) or 0


def mark_purchase_order_viewed(*, actor, purchase_order):
    """The Owner opened the order: it's no longer new, and everything the
    Distributor has said on it so far has been seen."""
    require_owner(actor)

    fields = []
    latest = _latest_revision_number(purchase_order)

    if purchase_order.owner_viewed_at is None:
        purchase_order.owner_viewed_at = timezone.now()
        fields.append("owner_viewed_at")

    if purchase_order.owner_seen_revision != latest:
        purchase_order.owner_seen_revision = latest
        fields.append("owner_seen_revision")

    if fields:
        purchase_order.save(update_fields=fields)

    return purchase_order


def mark_seen_by_distributor(*, actor, purchase_order):
    """The Distributor opened the order: everything the Owner has said on
    it so far has been seen."""
    require_approved_distributor(actor)

    latest = _latest_revision_number(purchase_order)

    if purchase_order.distributor_seen_revision != latest:
        purchase_order.distributor_seen_revision = latest
        purchase_order.save(update_fields=["distributor_seen_revision"])

    return purchase_order
