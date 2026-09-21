from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import require_approved_distributor, require_owner
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
        requested_price = row.get("requested_price")

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

        if requested_price not in (None, ""):
            requested_price = _as_decimal(requested_price)

            if requested_price < 0:
                raise ValidationError("Price cannot be negative.")
        else:
            requested_price = None

        rows.append(
            {
                "product": product,
                "quantity_requested": quantity,
                "requested_price": requested_price,
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

    purchase_order = PurchaseOrder(
        distributor_profile=distributor_profile,
        po_number=_next_po_number(),
        created_by=actor,
        updated_by=actor,
    )
    purchase_order.full_clean()
    purchase_order.save()

    lines = [
        PurchaseOrderItem(
            purchase_order=purchase_order,
            product=row["product"],
            quantity_requested=row["quantity_requested"],
            unit_price=(
                row["requested_price"]
                if row["requested_price"] is not None
                else row["product"].base_retail_price
            ),
            created_by=actor,
            updated_by=actor,
        )
        for row in rows
    ]

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
            "items": [
                {
                    "product_id": str(row["product"].pk),
                    "quantity_requested": str(row["quantity_requested"]),
                    "requested_price": (
                        str(row["requested_price"])
                        if row["requested_price"] is not None
                        else None
                    ),
                }
                for row in rows
            ],
        },
    )

    return purchase_order


def _recompute_status(purchase_order):
    items = list(purchase_order.items.all())

    if purchase_order.status == PurchaseOrder.Status.DECLINED:
        return

    if all(item.quantity_received >= item.quantity_requested for item in items):
        purchase_order.status = PurchaseOrder.Status.RECEIVED
    elif any(item.quantity_shipped > 0 for item in items):
        purchase_order.status = PurchaseOrder.Status.SHIPPED
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
def record_payment(*, actor, purchase_order_id, amount, paid_at, proof, note=""):
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

    payment = PurchaseOrderPayment(
        purchase_order=purchase_order,
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
            "amount": str(amount),
            "paid_at": str(paid_at),
            "status": payment.status,
        },
    )

    return payment


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
