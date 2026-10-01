from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

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
    Vendor,
    Manufacturer,
    ManufacturerOrder,
    ManufacturerOrderItem,
    ManufacturerOrderPayment,
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


def _vendor_payload(vendor):
    return {
        "name": vendor.name,
        "email": vendor.email,
        "phone": vendor.phone,
        "active": vendor.active,
    }


@transaction.atomic
def create_vendor(*, actor, **data):
    require_owner(actor)

    vendor = Vendor(created_by=actor, updated_by=actor, **data)
    vendor.full_clean()
    vendor.save()

    record_audit_event(
        user=actor,
        action="vendor.created",
        instance=vendor,
        after_data=_vendor_payload(vendor),
    )
    return vendor


@transaction.atomic
def update_vendor(*, actor, vendor_id, **data):
    require_owner(actor)

    vendor = Vendor.objects.select_for_update().filter(pk=vendor_id).first()
    if vendor is None:
        raise ValidationError("Vendor was not found.")

    before = _vendor_payload(vendor)
    for field, value in data.items():
        setattr(vendor, field, value)
    vendor.updated_by = actor
    vendor.full_clean()
    vendor.save()

    record_audit_event(
        user=actor,
        action="vendor.updated",
        instance=vendor,
        before_data=before,
        after_data=_vendor_payload(vendor),
    )
    return vendor


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

        rows.append(
            {
                "product": product,
                "bottle_size": bottle_size,
                "quantity": quantity,
                "unit_price": unit_price,
                "source_purchase_order_item": source_purchase_order_item,
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
    vendor=None,
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
        vendor=vendor,
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
def create_request_to_quote(*, actor, vendor, manufacturer, items, brand=None, terms=""):
    """Step one: the products, bottle sizes and quantities we want, at the
    prices we'd like (pre-filled from our saved prices, each optional).
    Printed and handed to the Vendor, who takes it to the Manufacturer —
    nothing is stocked or paid yet, so no batches are created until it
    becomes a Purchase Order."""
    require_owner(actor)

    if vendor is None:
        raise ValidationError("Choose the vendor this goes to.")

    rows = _validate_order_items(items, require_price=False)

    order = ManufacturerOrder(
        vendor=vendor,
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

    _create_order_lines(actor=actor, order=order, rows=rows)

    record_audit_event(
        user=actor,
        action="manufacturer.quote_requested",
        instance=order,
        after_data={
            "po_number": order.po_number,
            "vendor_id": str(vendor.pk),
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
    *, actor, order_id, vendor, manufacturer, items, brand=None, terms=""
):
    """Rewrites a Request to Quote's lines — only until the
    Manufacturer's quote has been entered, since after that the
    asked-for prices are what the quote is compared against."""
    require_owner(actor)

    order = _get_quote_for_update(order_id)

    if order.quoted_at is not None:
        raise ValidationError(
            "The Manufacturer's quote is already entered — edit the quoted prices instead."
        )

    if vendor is None:
        raise ValidationError("Choose the vendor this goes to.")

    rows = _validate_order_items(items, require_price=False)

    order.vendor = vendor
    order.manufacturer = manufacturer
    order.brand = brand
    order.terms = (terms or "").strip()
    order.updated_by = actor
    order.full_clean()
    order.save()

    order.items.all().delete()
    _create_order_lines(actor=actor, order=order, rows=rows)

    record_audit_event(
        user=actor,
        action="manufacturer.quote_request_updated",
        instance=order,
        after_data={
            "vendor_id": str(vendor.pk),
            "manufacturer_id": str(manufacturer.pk),
            "brand_id": str(brand.pk) if brand else None,
            "items": _rows_payload(rows),
        },
    )

    return order


@transaction.atomic
def record_manufacturer_quote(
    *, actor, order_id, item_updates, quote_file=None, remove_item_ids=(), save_price_item_ids=()
):
    """The Manufacturer's real prices came back to us through the Vendor
    (on paper, by email, a picture, ...). The Owner uploads that reply and
    types each line's quoted price; lines whose price changed from what
    we asked for are highlighted on the order. Lines the Manufacturer
    can't supply can be dropped. For the lines in save_price_item_ids,
    the quoted price also replaces our saved price for that product and
    bottle size. Can be re-entered until the order is confirmed.

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

    for item in items:
        if str(item.pk) in remove_item_ids:
            changes.append({"item_id": str(item.pk), "removed": True})
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

    record_audit_event(
        user=actor,
        action="manufacturer.quote_recorded",
        instance=order,
        after_data={"items": changes},
    )

    return order


@transaction.atomic
def confirm_purchase_order(*, actor, order_id):
    """Step two: the quoted prices are agreed, so the Request to Quote
    becomes the Purchase Order we hand the Vendor for the Manufacturer. From here the
    usual flow continues — advance payment, receiving, invoice."""
    require_owner(actor)

    order = _get_quote_for_update(order_id)

    if order.quoted_at is None:
        raise ValidationError(
            "Enter the Manufacturer's quote before turning this into a Purchase Order."
        )

    lines = list(order.items.select_related("product"))

    if any(line.unit_price is None for line in lines):
        raise ValidationError("Every product needs a price before it can be ordered.")

    order.status = ManufacturerOrder.Status.SENT
    order.po_sent_at = timezone.now()
    order.updated_by = actor
    order.save(update_fields=["status", "po_sent_at", "updated_at", "updated_by"])

    create_batches_for_order(actor=actor, order=order, items=lines)

    record_audit_event(
        user=actor,
        action="manufacturer.purchase_order_confirmed",
        instance=order,
        after_data={"po_number": order.po_number, "grand_total": str(order.grand_total)},
    )

    return order


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
    invoice_number="",
    item_prices=None,
    shipping_amount=None,
    tax_percentage=None,
):
    """The Manufacturer's own invoice, entered and approved as one action:
    the Owner updates each line's actual price and quantity to match what
    was invoiced (this is the point where the received quantity is
    actually trustworthy — the manufacturer may have sent more or less
    than the original order), uploads the invoice document, and that
    upload is itself the approval — there's no separate reviewer.

    The FIRST time an order's invoice is approved, each line lands as its
    own unallocated batch — not in any warehouse or region yet, decided
    separately via Inventory > Allocate. Re-editing an already-approved
    invoice later does not create stock again."""
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

    order.invoice_number = (invoice_number or "").strip()

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
            "invoice_number", "invoice_file", "invoice_approved_at",
            "invoice_approved_by", "shipping_amount", "tax_percentage",
            "updated_at", "updated_by",
        ]
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
        unallocated = get_or_create_unallocated_location(actor=actor)

        for item in order.items.select_related("product", "batch"):
            update = (item_prices or {}).get(str(item.pk)) or {}
            expiry_date = update.get("expiry_date")

            mark_batch_received(actor=actor, batch=item.batch, expiry_date=expiry_date)

            receive_stock(
                actor=actor,
                product=item.product,
                quantity=item.quantity,
                to_location=unallocated,
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
    invoice_number="",
    invoice_file=None,
    item_prices=None,
    shipping_amount=None,
    tax_percentage=None,
    paid_remaining=False,
    amount=None,
    paid_at=None,
    proof=None,
    note="",
):
    """Step three: the goods have arrived. In one go, marks the order
    received, approves the Manufacturer's invoice — the actual quantity,
    price and expiry per line, which is what creates the stock — and, if
    anything is still owed on the invoiced total and the Owner has paid
    it, records that final payment with its proof. Whichever of these is
    already done is skipped; if any part fails, none of it is saved."""
    require_owner(actor)

    order = _get_order_for_update(order_id)
    needs_receipt, needs_invoice = receiving_steps(order)

    if not needs_receipt and not needs_invoice:
        raise ValidationError("This order has already been received and invoiced.")

    if needs_receipt:
        order = mark_manufacturer_order_received(actor=actor, order_id=order.pk)

    if needs_invoice:
        order = record_manufacturer_invoice(
            actor=actor,
            order_id=order.pk,
            invoice_file=invoice_file,
            invoice_number=invoice_number,
            item_prices=item_prices,
            shipping_amount=shipping_amount,
            tax_percentage=tax_percentage,
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

    order.stock_created = needs_invoice
    return order
