from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event
from apps.products.models import BottleSize
from apps.products.services import save_bottle_price
from apps.batches.services import (
    cancel_pending_batches_for_order,
    create_batches_for_order,
    mark_batch_received,
)

from .models import (
    Manufacturer,
    ManufacturerOrder,
    ManufacturerOrderItem,
    ManufacturerOrderPayment,
    OrderRevision,
    OrderRevisionLine,
)


def _manufacturer_payload(manufacturer):
    return {
        "name": manufacturer.name,
        "phone": manufacturer.phone,
        "email": manufacturer.email,
        "active": manufacturer.active,
        "upfront_payment_percentage": str(manufacturer.upfront_payment_percentage),
    }


@transaction.atomic
def create_manufacturer(*, actor, **data):
    require_owner(actor)

    manufacturer = Manufacturer(
        created_by=actor,
        updated_by=actor,
        **data,
    )
    manufacturer.full_clean()
    manufacturer.save()

    record_audit_event(
        user=actor,
        action="manufacturer.created",
        instance=manufacturer,
        after_data=_manufacturer_payload(manufacturer),
    )
    return manufacturer


@transaction.atomic
def update_manufacturer(*, actor, manufacturer_id, **data):
    require_owner(actor)

    manufacturer = (
        Manufacturer.objects.select_for_update()
        .filter(pk=manufacturer_id)
        .first()
    )
    if manufacturer is None:
        raise ValidationError("Manufacturer was not found.")

    before = _manufacturer_payload(manufacturer)
    for field, value in data.items():
        setattr(manufacturer, field, value)
    manufacturer.updated_by = actor
    manufacturer.full_clean()
    manufacturer.save()

    record_audit_event(
        user=actor,
        action="manufacturer.updated",
        instance=manufacturer,
        before_data=before,
        after_data=_manufacturer_payload(manufacturer),
    )
    return manufacturer


def _as_decimal(value):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValidationError("Value must be a number.")


def _validate_order_items(items, *, require_price=True):
    """Each row: product, bottle_size (optional), quantity (bottles) and
    unit_price — which may be left blank on a Request to Quote, meaning
    "please quote us"."""
    if not items:
        raise ValidationError("An order needs at least one product.")

    rows = []
    seen = set()

    for row in items:
        product = row.get("product")
        bottle_size = row.get("bottle_size") or None
        quantity = row.get("quantity")
        unit_price = row.get("unit_price")
        source_purchase_order_item = row.get("source_purchase_order_item")
        save_price = bool(row.get("save_price"))

        if not product:
            raise ValidationError("Every row needs a product.")

        if bottle_size is not None:
            bottle_size = int(bottle_size)
            if bottle_size not in BottleSize.values:
                raise ValidationError("Not a valid bottle size.")

        key = (product.pk, bottle_size)
        if key in seen:
            raise ValidationError(
                f"{product.name} appears more than once with the same bottle size."
            )

        seen.add(key)

        quantity = _as_decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        if unit_price in (None, ""):
            if require_price:
                raise ValidationError(f"{product.name} needs a unit price.")
            unit_price = None
        else:
            unit_price = _as_decimal(unit_price)
            if unit_price < 0:
                raise ValidationError("Unit price cannot be negative.")
            if unit_price == 0 and not require_price:
                # On a Request to Quote a 0 means "please quote".
                unit_price = None

        rows.append(
            {
                "product": product,
                "bottle_size": bottle_size,
                "quantity": quantity,
                "unit_price": unit_price,
                "source_purchase_order_item": source_purchase_order_item,
                "save_price": save_price,
            }
        )

    return rows


def _rows_payload(rows):
    return [
        {
            "product_id": str(row["product"].pk),
            "bottle_size": row["bottle_size"],
            "quantity": str(row["quantity"]),
            "unit_price": None if row["unit_price"] is None else str(row["unit_price"]),
            "source_purchase_order_item_id": (
                str(row["source_purchase_order_item"].pk)
                if row.get("source_purchase_order_item")
                else None
            ),
        }
        for row in rows
    ]


def _create_order_lines(*, actor, order, rows):
    lines = [
        ManufacturerOrderItem(
            order=order,
            product=row["product"],
            bottle_size=row["bottle_size"],
            quantity=row["quantity"],
            requested_unit_price=row["unit_price"],
            unit_price=row["unit_price"],
            source_purchase_order_item=row.get("source_purchase_order_item"),
            created_by=actor,
            updated_by=actor,
        )
        for row in rows
    ]

    for line in lines:
        line.full_clean()

    return ManufacturerOrderItem.objects.bulk_create(lines)


def _line_snapshot(item, *, unit_price=None, quantity=None, removed=False):
    """One line of an OrderRevision, taken from a live order line —
    with this step's price/quantity when they differ from the item's
    own (a counter-offer doesn't change the order, only proposes)."""
    return {
        "item": None if removed else item,
        "product": item.product,
        "bottle_size": item.bottle_size,
        "quantity": item.quantity if quantity is None else quantity,
        "unit_price": item.unit_price if unit_price is None else unit_price,
        "removed": removed,
    }


def _record_revision(*, actor, order, stage, lines, message="", attachment=None):
    """Freezes one step of the negotiation: the lines as they stand
    now and what was said. The order must already be locked
    (select_for_update) so the numbering can't collide."""
    last = order.revisions.order_by("-number").values_list("number", flat=True).first()

    revision = OrderRevision(
        order=order,
        number=(last or 0) + 1,
        stage=stage,
        message=(message or "").strip(),
        attachment=attachment or None,
        created_by=actor,
        updated_by=actor,
    )
    revision.full_clean()
    revision.save()

    OrderRevisionLine.objects.bulk_create(
        [
            OrderRevisionLine(
                revision=revision,
                created_by=actor,
                updated_by=actor,
                **line,
            )
            for line in lines
        ]
    )

    return revision


def _current_lines(order):
    return [_line_snapshot(item) for item in order.items.select_related("product")]


def _save_system_prices(*, actor, rows):
    """Lines ticked "Save as system price" on a Request to Quote store
    their price as our saved price for that product and bottle size —
    the one the "Use system price" button offers next time."""
    saved = []

    for row in rows:
        if not row.get("save_price"):
            continue
        if row["bottle_size"] is None or row["unit_price"] is None:
            raise ValidationError(
                f"{row['product'].name}: a saved price needs a bottle size and a price."
            )
        save_bottle_price(
            actor=actor,
            product=row["product"],
            bottle_size=row["bottle_size"],
            price=row["unit_price"],
        )
        saved.append(row)

    return saved


def _next_manufacturer_po_number():
    last = ManufacturerOrder.objects.order_by("-created_at").first()
    next_seq = 1

    if last is not None:
        try:
            next_seq = int(last.po_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_seq = ManufacturerOrder.objects.count() + 1

    return f"MO-{next_seq:05d}"


@transaction.atomic
def create_manufacturer_order(
    *,
    actor,
    manufacturer,
    items,
    brand=None,
    tax_percentage=0,
    shipping_amount=0,
    note="",
):
    """Places a Purchase Order directly, skipping the Request to Quote
    step — every line needs its agreed price."""
    require_owner(actor)

    rows = _validate_order_items(items)

    order = ManufacturerOrder(
        manufacturer=manufacturer,
        brand=brand,
        po_number=_next_manufacturer_po_number(),
        status=ManufacturerOrder.Status.SENT,
        po_sent_at=timezone.now(),
        owner_note=(note or "").strip(),
        tax_percentage=_as_decimal(tax_percentage or 0),
        shipping_amount=_as_decimal(shipping_amount or 0),
        created_by=actor,
        updated_by=actor,
    )
    order.full_clean()
    order.save()

    lines = _create_order_lines(actor=actor, order=order, rows=rows)
    create_batches_for_order(actor=actor, order=order, items=lines)
    _record_revision(
        actor=actor,
        order=order,
        stage=OrderRevision.Stage.PURCHASE_ORDER,
        lines=[_line_snapshot(line) for line in lines],
        message=note,
    )

    record_audit_event(
        user=actor,
        action="manufacturer.order_created",
        instance=order,
        after_data={
            "po_number": order.po_number,
            "manufacturer_id": str(manufacturer.pk),
            "brand_id": str(brand.pk) if brand else None,
            "items": _rows_payload(rows),
        },
    )

    return order


@transaction.atomic
def create_request_to_quote(
    *, actor, manufacturer, items, brand=None, terms="", message=""
):
    """Step one: the products, bottle sizes and quantities we want, at the
    prices we'd like (pre-filled from our saved prices, each optional).
    Printed and sent to the Manufacturer (our vendor) — nothing is
    stocked or paid yet, so no batches are created until it becomes a
    Purchase Order."""
    require_owner(actor)

    rows = _validate_order_items(items, require_price=False)

    order = ManufacturerOrder(
        manufacturer=manufacturer,
        brand=brand,
        po_number=_next_manufacturer_po_number(),
        status=ManufacturerOrder.Status.QUOTE,
        terms=(terms or "").strip(),
        created_by=actor,
        updated_by=actor,
    )
    order.full_clean()
    order.save()

    lines = _create_order_lines(actor=actor, order=order, rows=rows)
    _save_system_prices(actor=actor, rows=rows)
    _record_revision(
        actor=actor,
        order=order,
        stage=OrderRevision.Stage.REQUEST,
        lines=[_line_snapshot(line) for line in lines],
        message=message,
    )

    record_audit_event(
        user=actor,
        action="manufacturer.quote_requested",
        instance=order,
        after_data={
            "po_number": order.po_number,
            "manufacturer_id": str(manufacturer.pk),
            "brand_id": str(brand.pk) if brand else None,
            "items": _rows_payload(rows),
        },
    )

    return order


def _get_quote_for_update(order_id):
    order = _get_order_for_update(order_id)

    if order.status != ManufacturerOrder.Status.QUOTE:
        raise ValidationError(
            "This is already a Purchase Order — its Request to Quote can't change."
        )

    return order


@transaction.atomic
def update_request_to_quote(
    *, actor, order_id, manufacturer, items, brand=None, terms="", message=None
):
    """Rewrites a Request to Quote's lines — only until the
    Manufacturer's quote has been entered, since after that the
    asked-for prices are what the quote is compared against. Until then
    it's still our draft, so its first revision is rewritten in place
    rather than adding a new step to the conversation."""
    require_owner(actor)

    order = _get_quote_for_update(order_id)

    if order.quoted_at is not None:
        raise ValidationError(
            "The Manufacturer's quote is already entered — edit the quoted prices instead."
        )

    rows = _validate_order_items(items, require_price=False)

    order.manufacturer = manufacturer
    order.brand = brand
    order.terms = (terms or "").strip()
    order.updated_by = actor
    order.full_clean()
    order.save()

    order.items.all().delete()
    lines = _create_order_lines(actor=actor, order=order, rows=rows)
    _save_system_prices(actor=actor, rows=rows)

    request = order.revisions.filter(stage=OrderRevision.Stage.REQUEST).first()
    if request is None:
        _record_revision(
            actor=actor,
            order=order,
            stage=OrderRevision.Stage.REQUEST,
            lines=[_line_snapshot(line) for line in lines],
            message=message or "",
        )
    else:
        request.lines.all().delete()
        OrderRevisionLine.objects.bulk_create(
            [
                OrderRevisionLine(
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
        action="manufacturer.quote_request_updated",
        instance=order,
        after_data={
            "manufacturer_id": str(manufacturer.pk),
            "brand_id": str(brand.pk) if brand else None,
            "items": _rows_payload(rows),
        },
    )

    return order


@transaction.atomic
def record_manufacturer_quote(
    *,
    actor,
    order_id,
    item_updates,
    quote_file=None,
    remove_item_ids=(),
    save_price_item_ids=(),
    message="",
):
    """The Manufacturer's real prices came back to us from the Manufacturer
    (on paper, by email, a picture, ...). The Owner uploads that reply and
    types each line's quoted price; lines whose price changed from what
    we asked for are highlighted on the order. Lines the Manufacturer
    can't supply can be dropped. For the lines in save_price_item_ids,
    the quoted price also replaces our saved price for that product and
    bottle size. Every reply is kept as its own step in the order's
    conversation, so this is entered again for each new reply until the
    order is confirmed.

    item_updates: {item_pk_as_str: {"unit_price": Decimal, "quantity": Decimal}}"""
    require_owner(actor)

    order = _get_quote_for_update(order_id)

    if not quote_file and not order.quote_file:
        raise ValidationError("Upload the Manufacturer's quote document.")

    remove_item_ids = {str(pk) for pk in remove_item_ids}
    save_price_item_ids = {str(pk) for pk in save_price_item_ids}

    items = list(order.items.select_for_update().select_related("product"))
    kept = [item for item in items if str(item.pk) not in remove_item_ids]

    if not kept:
        raise ValidationError("At least one product must stay on the order.")

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
            raise ValidationError(f"Enter the quoted price for {item.product.name}.")

        item.unit_price = _as_decimal(unit_price)
        if quantity is not None:
            item.quantity = _as_decimal(quantity)

        item.updated_by = actor
        item.full_clean()
        item.save(update_fields=["unit_price", "quantity", "updated_at", "updated_by"])

        changes.append(
            {
                "item_id": str(item.pk),
                "requested_unit_price": (
                    None if item.requested_unit_price is None
                    else str(item.requested_unit_price)
                ),
                "unit_price": str(item.unit_price),
                "quantity": str(item.quantity),
            }
        )

        if str(item.pk) in save_price_item_ids and item.bottle_size:
            save_bottle_price(
                actor=actor,
                product=item.product,
                bottle_size=item.bottle_size,
                price=item.unit_price,
            )

    if quote_file:
        order.quote_file = quote_file

    order.quoted_at = timezone.now()
    order.updated_by = actor
    order.save(update_fields=["quote_file", "quoted_at", "updated_at", "updated_by"])

    revision = _record_revision(
        actor=actor,
        order=order,
        stage=OrderRevision.Stage.QUOTE,
        lines=_current_lines(order) + dropped,
        message=message,
        attachment=quote_file,
    )

    record_audit_event(
        user=actor,
        action="manufacturer.quote_recorded",
        instance=order,
        after_data={"revision": revision.number, "items": changes},
    )

    return order


@transaction.atomic
def confirm_purchase_order(*, actor, order_id, message=""):
    """Step two: the quoted prices are agreed, so the Request to Quote
    becomes the Purchase Order we send the Manufacturer. From here the
    usual flow continues — advance payment, receiving, invoice.

    What's accepted is the Manufacturer's latest quote, so this isn't
    allowed while our own counter-offer is still waiting for their
    answer — record their reply first (the same prices, if they agreed)."""
    require_owner(actor)

    order = _get_quote_for_update(order_id)

    if order.quoted_at is None:
        raise ValidationError(
            "Enter the Manufacturer's quote before turning this into a Purchase Order."
        )

    if awaiting_manufacturer_reply(order):
        raise ValidationError(
            "Your counter-offer is still waiting for the Manufacturer's answer — "
            "record their reply before sending the Purchase Order."
        )

    lines = list(order.items.select_related("product"))

    if any(line.unit_price is None for line in lines):
        raise ValidationError("Every product needs a price before it can be ordered.")

    order.status = ManufacturerOrder.Status.SENT
    order.po_sent_at = timezone.now()
    order.updated_by = actor
    order.save(update_fields=["status", "po_sent_at", "updated_at", "updated_by"])

    create_batches_for_order(actor=actor, order=order, items=lines)
    _record_revision(
        actor=actor,
        order=order,
        stage=OrderRevision.Stage.PURCHASE_ORDER,
        lines=[_line_snapshot(line) for line in lines],
        message=message,
    )

    record_audit_event(
        user=actor,
        action="manufacturer.purchase_order_confirmed",
        instance=order,
        after_data={"po_number": order.po_number, "grand_total": str(order.grand_total)},
    )

    return order


def awaiting_manufacturer_reply(order):
    """On a Request to Quote: whose turn it is. True while our request
    or our counter-offer is the last thing said."""
    latest = order.revisions.order_by("-number").first()
    return latest is not None and latest.stage in (
        OrderRevision.Stage.REQUEST,
        OrderRevision.Stage.COUNTER,
    )


@transaction.atomic
def record_counter_offer(*, actor, order_id, item_updates, message="", attachment=None):
    """We answer the Manufacturer's quote with the prices (and
    quantities) we want instead. This only proposes — the order's own
    prices stay at the Manufacturer's latest quote until they reply,
    and that reply is what can be turned into the Purchase Order.

    item_updates: {item_pk_as_str: {"unit_price": Decimal, "quantity": Decimal}}"""
    require_owner(actor)

    order = _get_quote_for_update(order_id)

    if order.quoted_at is None:
        raise ValidationError(
            "Enter the Manufacturer's quote first — a counter-offer answers their prices."
        )

    lines = []
    changes = []

    for item in order.items.select_related("product"):
        update = item_updates.get(str(item.pk)) or {}
        unit_price = update.get("unit_price")
        quantity = update.get("quantity")

        if unit_price is None:
            raise ValidationError(f"Enter the price you're offering for {item.product.name}.")

        unit_price = _as_decimal(unit_price)
        quantity = item.quantity if quantity is None else _as_decimal(quantity)

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
        order=order,
        stage=OrderRevision.Stage.COUNTER,
        lines=lines,
        message=message,
        attachment=attachment,
    )

    record_audit_event(
        user=actor,
        action="manufacturer.counter_offer_sent",
        instance=order,
        after_data={"revision": revision.number, "items": changes},
    )

    return revision


@transaction.atomic
def update_purchase_order(
    *, actor, order_id, item_updates, remove_item_ids=(), message=""
):
    """Changes a Purchase Order already sent to the Manufacturer — prices,
    quantities, or dropping a line — as long as the goods haven't arrived
    and the invoice isn't in yet. Every change is saved as a new version
    of the Purchase Order (v2, v3, ...), so the earlier ones stay visible.

    A dropped line's batch is still only Pending (nothing has arrived),
    so it's removed along with the line.

    item_updates: {item_pk_as_str: {"unit_price": Decimal, "quantity": Decimal}}"""
    require_owner(actor)

    order = _get_order_for_update(order_id)

    if (
        order.status != ManufacturerOrder.Status.SENT
        or order.received_at is not None
        or order.invoice_approved_at is not None
    ):
        raise ValidationError(
            "Only a Purchase Order that hasn't been received or invoiced yet can be changed."
        )

    remove_item_ids = {str(pk) for pk in remove_item_ids}
    items = list(order.items.select_for_update().select_related("product", "batch"))

    if all(str(item.pk) in remove_item_ids for item in items):
        raise ValidationError("At least one product must stay on the order.")

    before = {"grand_total": str(order.grand_total)}
    changes = []
    dropped = []

    for item in items:
        if str(item.pk) in remove_item_ids:
            dropped.append(_line_snapshot(item, removed=True))
            changes.append({"item_id": str(item.pk), "removed": True})
            batch = getattr(item, "batch", None)
            if batch is not None:
                record_audit_event(
                    user=actor,
                    action="batches.batch_removed",
                    instance=batch,
                    before_data={"code": batch.code},
                    reason=f"Line dropped from Purchase Order {order.po_number}",
                )
                batch.delete()
            item.delete()
            continue

        update = item_updates.get(str(item.pk)) or {}
        unit_price = update.get("unit_price")
        quantity = update.get("quantity")

        if unit_price is None:
            raise ValidationError(f"Enter the price for {item.product.name}.")

        unit_price = _as_decimal(unit_price)
        quantity = item.quantity if quantity is None else _as_decimal(quantity)

        if unit_price == item.unit_price and quantity == item.quantity:
            continue

        item.unit_price = unit_price
        item.quantity = quantity
        item.updated_by = actor
        item.full_clean()
        item.save(update_fields=["unit_price", "quantity", "updated_at", "updated_by"])
        changes.append(
            {"item_id": str(item.pk), "unit_price": str(unit_price), "quantity": str(quantity)}
        )

    if not changes:
        raise ValidationError("Nothing was changed on the Purchase Order.")

    if order.grand_total < order.total_paid:
        raise ValidationError(
            f"The new total ({order.grand_total}) would be less than what's already "
            f"been paid ({order.total_paid})."
        )

    order.updated_by = actor
    order.save(update_fields=["updated_at", "updated_by"])

    revision = _record_revision(
        actor=actor,
        order=order,
        stage=OrderRevision.Stage.PURCHASE_ORDER,
        lines=_current_lines(order) + dropped,
        message=message,
    )

    record_audit_event(
        user=actor,
        action="manufacturer.purchase_order_updated",
        instance=order,
        before_data=before,
        after_data={
            "revision": revision.number,
            "grand_total": str(order.grand_total),
            "items": changes,
        },
    )

    return revision


def revision_timeline(order):
    """The order's whole conversation, oldest first, ready to show:
    each step with its lines compared against the step before it.
    A line is "changed" (orange) when its price moved, "new" (yellow)
    when a price was given where there was none, "" (white) when it's
    the same — the same colours as on the order itself."""
    revisions = list(
        order.revisions
        .select_related("created_by")
        .prefetch_related("lines__product")
        .order_by("number")
    )

    timeline = []
    previous = {}
    po_version = 0

    for revision in revisions:
        if revision.stage == OrderRevision.Stage.PURCHASE_ORDER:
            po_version += 1

        label = revision.get_stage_display()
        if revision.stage == OrderRevision.Stage.PURCHASE_ORDER and po_version > 1:
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


def _get_order_for_update(order_id):
    order = (
        ManufacturerOrder.objects
        .select_for_update()
        .filter(pk=order_id)
        .first()
    )

    if order is None:
        raise ValidationError("Manufacturer order was not found.")

    return order


@transaction.atomic
def mark_manufacturer_order_received(*, actor, order_id):
    """Marks the goods as physically arrived — nothing more. This does
    not touch inventory: the quantity on the original order isn't
    trustworthy yet (that's confirmed by the invoice), so stock is only
    created once the invoice is approved."""
    require_owner(actor)

    order = _get_order_for_update(order_id)

    if order.status != ManufacturerOrder.Status.SENT:
        raise ValidationError(
            "Only an order that's still Sent can be marked received."
        )

    order.status = ManufacturerOrder.Status.RECEIVED
    order.received_at = timezone.now()
    order.updated_by = actor
    order.save(update_fields=["status", "received_at", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="manufacturer.order_received",
        instance=order,
        after_data={"received_at": str(order.received_at)},
    )

    return order


@transaction.atomic
def record_manufacturer_invoice(
    *,
    actor,
    order_id,
    invoice_file=None,
    supplier_invoice_ref="",
    item_prices=None,
    shipping_amount=None,
    tax_percentage=None,
    to_location=None,
):
    """The Manufacturer's own invoice, entered and approved as one action:
    the Owner updates each line's actual price and quantity to match what
    was invoiced (this is the point where the received quantity is
    actually trustworthy — the manufacturer may have sent more or less
    than the original order), uploads the invoice document, and that
    upload is itself the approval — there's no separate reviewer.

    The FIRST time an order's invoice is approved it gets our own invoice
    number (INV-<BRAND>-0001) and each line lands as its own batch in
    to_location — the warehouse picked on the receive page — or, when
    none is given, the unallocated pool to be placed later via
    Inventory > Allocate. Re-editing an already-approved invoice later
    does not create stock again."""
    from apps.owner_inventory.services import (
        get_or_create_unallocated_location,
        receive_stock,
    )

    require_owner(actor)

    order = _get_order_for_update(order_id)
    is_first_approval = order.invoice_approved_at is None

    before = {
        "invoice_number": order.invoice_number,
        "grand_total": str(order.grand_total),
    }

    for item in order.items.select_for_update():
        update = (item_prices or {}).get(str(item.pk))

        if not update:
            continue

        if update.get("unit_price") is not None:
            item.unit_price = update["unit_price"]

        if update.get("quantity") is not None:
            item.quantity = update["quantity"]

        item.updated_by = actor
        item.full_clean()
        item.save(update_fields=["unit_price", "quantity", "updated_at", "updated_by"])

    order.supplier_invoice_ref = (supplier_invoice_ref or "").strip()

    if is_first_approval or not order.invoice_number:
        order.invoice_number = next_invoice_number(order)

    if shipping_amount is not None:
        order.shipping_amount = _as_decimal(shipping_amount)

    if tax_percentage is not None:
        order.tax_percentage = _as_decimal(tax_percentage)

    if invoice_file:
        order.invoice_file = invoice_file

    order.invoice_approved_at = timezone.now()
    order.invoice_approved_by = actor
    order.updated_by = actor
    order.full_clean()
    order.save(
        update_fields=[
            "invoice_number", "supplier_invoice_ref", "invoice_file", "invoice_approved_at",
            "invoice_approved_by", "shipping_amount", "tax_percentage",
            "updated_at", "updated_by",
        ]
    )

    _record_revision(
        actor=actor,
        order=order,
        stage=OrderRevision.Stage.INVOICE,
        lines=_current_lines(order),
        message=(
            f"Manufacturer's invoice no. {order.supplier_invoice_ref}"
            if order.supplier_invoice_ref else ""
        ),
        attachment=invoice_file,
    )

    record_audit_event(
        user=actor,
        action="manufacturer.order_invoice_recorded",
        instance=order,
        before_data=before,
        after_data={
            "invoice_number": order.invoice_number,
            "grand_total": str(order.grand_total),
        },
    )

    if is_first_approval:
        destination = to_location or get_or_create_unallocated_location(actor=actor)

        for item in order.items.select_related("product", "batch"):
            update = (item_prices or {}).get(str(item.pk)) or {}
            expiry_date = update.get("expiry_date")

            mark_batch_received(actor=actor, batch=item.batch, expiry_date=expiry_date)

            receive_stock(
                actor=actor,
                product=item.product,
                quantity=item.quantity,
                to_location=destination,
                reference=f"Manufacturer order {order.po_number} invoiced",
                batch_number=item.batch.code,
                expiry_date=item.batch.expiry_date,
                source_batch=item.batch,
            )

    order.stock_created = is_first_approval

    return order


@transaction.atomic
def set_manufacturer_order_outcome(*, actor, order_id, outcome, note=""):
    require_owner(actor)

    if outcome not in (
        ManufacturerOrder.Status.GOOD,
        ManufacturerOrder.Status.DISPUTED,
        ManufacturerOrder.Status.REFUNDED,
    ):
        raise ValidationError("Not a valid outcome.")

    order = _get_order_for_update(order_id)

    if order.status not in (
        ManufacturerOrder.Status.RECEIVED,
        ManufacturerOrder.Status.DISPUTED,
    ):
        raise ValidationError(
            "The order must be marked received before recording an outcome."
        )

    before_status = order.status
    order.status = outcome
    order.outcome_note = (note or "").strip()
    order.updated_by = actor
    order.save(
        update_fields=["status", "outcome_note", "updated_at", "updated_by"]
    )

    record_audit_event(
        user=actor,
        action="manufacturer.order_outcome_set",
        instance=order,
        before_data={"status": before_status},
        after_data={"status": order.status, "outcome_note": order.outcome_note},
    )

    if outcome == ManufacturerOrder.Status.REFUNDED:
        cancel_pending_batches_for_order(actor=actor, order=order)

    return order


@transaction.atomic
def record_manufacturer_payment(
    *,
    actor,
    order_id,
    amount,
    paid_at,
    proof,
    kind=ManufacturerOrderPayment.Kind.FINAL,
    note="",
):
    """One payment to the Manufacturer, always with proof. Can't take the
    order past what it costs."""
    require_owner(actor)

    order = _get_order_for_update(order_id)
    amount = _as_decimal(amount)

    if order.status == ManufacturerOrder.Status.QUOTE:
        raise ValidationError(
            "This is still a Request to Quote — confirm the Purchase Order before paying."
        )

    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")

    if not proof:
        raise ValidationError("Upload a proof of payment.")

    if amount > order.remaining_amount:
        raise ValidationError(
            f"Only {order.remaining_amount} is left to pay on this order."
        )

    payment = ManufacturerOrderPayment(
        order=order,
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
        action="manufacturer.order_payment_recorded",
        instance=payment,
        after_data={
            "kind": kind,
            "amount": str(amount),
            "paid_at": str(paid_at),
        },
    )

    return payment


@transaction.atomic
def record_advance_decision(
    *, actor, order_id, pays_advance, amount=None, paid_at=None, proof=None, note=""
):
    """Step two of placing an order: "Are you paying in advance?" — asked
    once, while the order is still out with the Manufacturer. Yes records
    the advance (anything up to the whole order) with its proof; No just
    notes that the full amount is due on receipt."""
    require_owner(actor)

    order = _get_order_for_update(order_id)

    if order.status != ManufacturerOrder.Status.SENT:
        raise ValidationError(
            "An advance can only be recorded before the order is received."
        )

    if order.pays_advance is not None:
        raise ValidationError("The advance payment has already been answered.")

    order.pays_advance = bool(pays_advance)
    order.updated_by = actor
    order.save(update_fields=["pays_advance", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="manufacturer.order_advance_decided",
        instance=order,
        after_data={"pays_advance": order.pays_advance},
    )

    if order.pays_advance:
        return record_manufacturer_payment(
            actor=actor,
            order_id=order.pk,
            amount=amount,
            paid_at=paid_at,
            proof=proof,
            kind=ManufacturerOrderPayment.Kind.ADVANCE,
            note=note,
        )

    return None


def invoice_prefix(order):
    """INV-<BRAND>- — e.g. INV-BOREAL-VITA- for the Boreal Vita brand."""
    from apps.core.models import Brand

    brand = order.brand or Brand.objects.filter(active=True).first()
    code = slugify(brand.name).upper() if brand else ""
    return f"INV-{code}-" if code else "INV-"


def next_invoice_number(order):
    """The next free invoice number for the order's brand:
    INV-BOREAL-VITA-0001, INV-BOREAL-VITA-0002, ..."""
    prefix = invoice_prefix(order)
    used = ManufacturerOrder.objects.filter(invoice_number__startswith=prefix).values_list(
        "invoice_number", flat=True
    )
    numbers = [int(n[len(prefix):]) for n in used if n[len(prefix):].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def receiving_steps(order):
    """What's still to do on an order's Order Received page: confirm the
    goods arrived, and/or approve the Manufacturer's invoice (which is
    what puts the stock into Inventory). An order can have one without
    the other — older orders were sometimes invoiced before arriving,
    or received and never invoiced."""
    if order.status == ManufacturerOrder.Status.QUOTE:
        return False, False

    needs_receipt = order.status == ManufacturerOrder.Status.SENT
    needs_invoice = (
        order.invoice_approved_at is None
        and order.status != ManufacturerOrder.Status.REFUNDED
    )
    return needs_receipt, needs_invoice


@transaction.atomic
def receive_manufacturer_order(
    *,
    actor,
    order_id,
    supplier_invoice_ref="",
    invoice_file=None,
    item_prices=None,
    shipping_amount=None,
    tax_percentage=None,
    to_location=None,
    paid_remaining=False,
    amount=None,
    paid_at=None,
    proof=None,
    note="",
):
    """Step three: the goods have arrived. In one go, marks the order
    received, approves the Manufacturer's invoice — the actual quantity,
    price and expiry per line, which is what creates the stock in
    to_location — and records the final payment for whatever is still
    owed on the invoiced total, with its proof.

    Two gates: the advance question must have been answered first, and
    the order can't be received until it's paid in full. Whichever step
    is already done is skipped; if any part fails, none of it is saved."""
    require_owner(actor)

    order = _get_order_for_update(order_id)
    needs_receipt, needs_invoice = receiving_steps(order)

    if not needs_receipt and not needs_invoice:
        raise ValidationError("This order has already been received and invoiced.")

    if needs_receipt and order.pays_advance is None:
        raise ValidationError(
            "Answer the advance payment (advance or no advance) before "
            "recording the invoice and receiving."
        )

    if needs_receipt:
        order = mark_manufacturer_order_received(actor=actor, order_id=order.pk)

    if needs_invoice:
        order = record_manufacturer_invoice(
            actor=actor,
            order_id=order.pk,
            invoice_file=invoice_file,
            supplier_invoice_ref=supplier_invoice_ref,
            item_prices=item_prices,
            shipping_amount=shipping_amount,
            tax_percentage=tax_percentage,
            to_location=to_location,
        )

    if order.pays_advance is None:
        order.pays_advance = False
        order.save(update_fields=["pays_advance"])

    if paid_remaining and order.remaining_amount > 0:
        record_manufacturer_payment(
            actor=actor,
            order_id=order.pk,
            amount=amount,
            paid_at=paid_at,
            proof=proof,
            kind=ManufacturerOrderPayment.Kind.FINAL,
            note=note,
        )

    if order.remaining_amount > 0:
        raise ValidationError(
            f"{order.remaining_amount} is still owed on this order — pay the "
            "full amount before marking it received."
        )

    order.stock_created = needs_invoice
    return order
