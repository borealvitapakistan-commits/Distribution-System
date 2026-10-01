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

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderPayment


def _as_decimal(quantity):
    try:
        return Decimal(quantity)
    except (InvalidOperation, TypeError):
        raise ValidationError("Quantity must be a number.")


def _validate_items(items):
    if not items:
        raise ValidationError("A purchase order needs at least one product.")

    rows = []
    seen = set()

    for row in items:
        product = row.get("product")
        quantity = row.get("quantity_requested")

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

        rows.append(
            {
                "product": product,
                "quantity_requested": quantity,
            }
        )

    return rows


def _next_po_number():
    last = PurchaseOrder.objects.order_by("-created_at").first()
    next_seq = 1

    if last is not None:
        try:
            next_seq = int(last.po_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            next_seq = PurchaseOrder.objects.count() + 1

    return f"PO-{next_seq:05d}"


@transaction.atomic
def create_purchase_order(*, actor, items):
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
        created_by=actor,
        updated_by=actor,
    )
    purchase_order.full_clean()
    purchase_order.save()

    lines = []

    for row in rows:
        product = row["product"]
        discount = agreement.discount_for(product) if agreement else Decimal("0")

        lines.append(
            PurchaseOrderItem(
                purchase_order=purchase_order,
                product=product,
                quantity_requested=row["quantity_requested"],
                list_price=product.base_retail_price,
                discount_percentage=discount,
                unit_price=discounted_price(product.base_retail_price, discount),
                created_by=actor,
                updated_by=actor,
            )
        )

    for line in lines:
        line.full_clean()

    PurchaseOrderItem.objects.bulk_create(lines)

    record_audit_event(
        user=actor,
        action="requests.purchase_order_created",
        instance=purchase_order,
        after_data={
            "po_number": purchase_order.po_number,
            "distributor_profile_id": str(distributor_profile.pk),
            "agreement_id": str(agreement.pk) if agreement else None,
            "items": [
                {
                    "product_id": str(line.product_id),
                    "quantity_requested": str(line.quantity_requested),
                    "list_price": str(line.list_price),
                    "discount_percentage": str(line.discount_percentage),
                    "unit_price": str(line.unit_price),
                }
                for line in lines
            ],
        },
    )

    return purchase_order


def _recompute_status(purchase_order):
    """An order stays open until every line has either arrived in full
    or been closed by the Owner as unavailable (after whatever part of
    it did ship arrived) - e.g. while the Owner waits on a Manufacturer
    for one product, the order sits at Partially shipped."""
    items = list(purchase_order.items.all())

    if purchase_order.status == PurchaseOrder.Status.DECLINED:
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

    if purchase_order.invoiced_at is None:
        purchase_order.invoiced_at = timezone.now()
        purchase_order.updated_by = actor
        purchase_order.save(update_fields=["invoiced_at", "updated_at", "updated_by"])

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
    shipped yet can change; the unit price follows the percentage."""
    require_owner(actor)

    purchase_order = _get_open_order_for_update(purchase_order_id)
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


def mark_purchase_order_viewed(*, actor, purchase_order):
    require_owner(actor)

    if purchase_order.owner_viewed_at is not None:
        return purchase_order

    purchase_order.owner_viewed_at = timezone.now()
    purchase_order.save(update_fields=["owner_viewed_at"])

    return purchase_order
