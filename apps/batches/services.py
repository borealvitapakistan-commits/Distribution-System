from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_audit_event

from .models import Batch


def _code_part(text, fallback):
    text = (text or "").strip()
    return (text[:3] or fallback).upper()


def _next_sequence(prefix):
    """One past the highest number used so far — not a count, so a deleted
    batch (e.g. from a deleted order) never makes a code repeat."""
    numbers = [
        int(code[len(prefix) + 1:])
        for code in Batch.objects.filter(code__startswith=f"{prefix}-").values_list("code", flat=True)
        if code[len(prefix) + 1:].isdigit()
    ]
    return max(numbers, default=0) + 1


def generate_batch_code(*, brand, product):
    """BRA-PRO-001 — first three characters of the brand name, first
    three of the product name, then a sequence number scoped to that
    brand/product pair."""
    brand_part = _code_part(brand.name if brand else None, "GEN")
    product_part = _code_part(product.name, "PRD")

    prefix = f"{brand_part}-{product_part}"
    sequence = _next_sequence(prefix)

    return f"{prefix}-{sequence:03d}"


@transaction.atomic
def create_batches_for_order(*, actor, order, items):
    """One Batch per Manufacturer Purchase Order line, created the
    moment the order is placed — before the Manufacturer has shipped
    anything. items: the already-saved ManufacturerOrderItem rows."""
    batches = []

    for item in items:
        batch = Batch(
            code=generate_batch_code(brand=order.brand, product=item.product),
            manufacturer_order_item=item,
            status=Batch.Status.PENDING,
            created_by=actor,
            updated_by=actor,
        )
        batch.full_clean()
        batch.save()
        batches.append(batch)

        record_audit_event(
            user=actor,
            action="batches.batch_created",
            instance=batch,
            after_data={
                "code": batch.code,
                "manufacturer_order_item_id": str(item.pk),
                "product_id": str(item.product_id),
            },
        )

    return batches


@transaction.atomic
def mark_batch_received(*, actor, batch, expiry_date=None):
    batch.status = Batch.Status.RECEIVED
    batch.received_at = timezone.now()

    if expiry_date:
        batch.expiry_date = expiry_date

    batch.updated_by = actor
    batch.full_clean()
    batch.save(
        update_fields=[
            "status", "received_at", "expiry_date", "updated_at", "updated_by",
        ]
    )

    record_audit_event(
        user=actor,
        action="batches.batch_received",
        instance=batch,
        after_data={"expiry_date": str(batch.expiry_date) if batch.expiry_date else None},
    )

    return batch


@transaction.atomic
def cancel_pending_batches_for_order(*, actor, order):
    """Called when a Manufacturer order is refunded: any line that was
    never actually invoiced/received is a lot that's never going to
    arrive — close it out rather than leaving it Pending forever."""
    cancelled = []

    for batch in Batch.objects.filter(
        manufacturer_order_item__order=order,
        status=Batch.Status.PENDING,
    ):
        batch.status = Batch.Status.CANCELLED
        batch.cancelled_at = timezone.now()
        batch.updated_by = actor
        batch.save(update_fields=["status", "cancelled_at", "updated_at", "updated_by"])
        cancelled.append(batch)

        record_audit_event(
            user=actor,
            action="batches.batch_cancelled",
            instance=batch,
            after_data={"code": batch.code},
        )

    return cancelled


@dataclass
class TraceEvent:
    at: datetime
    stage: str
    from_label: str
    to_label: str
    quantity: Decimal
    reference: str = ""
    by: object = None


def trace_batch_code(code):
    """Follows one lot by its batch code through every hop the system
    tracks: the Owner orders it from the Manufacturer, receives it into a
    warehouse, moves it between warehouses and sells it on Shopify.

    The code is carried forward unchanged at every hop (Batch.code →
    StockBatch.batch_number), so it is what ties the Manufacturer order
    to the Owner's ledger here. Read-only, and for the Owner's eyes only.

    Stock handed to a Distributor before the Distributor panel was
    archived still shows as history, so the quantities keep adding up.

    Returns the canonical Batch (None for stock added by hand), every
    event oldest first and the quantity check-and-balance totals."""
    from apps.owner_inventory.models import StockBatch, StockMovement
    from apps.owner_warehouse.models import Location

    code = (code or "").strip()
    zero = Decimal("0")
    events = []

    batch = (
        Batch.objects
        .select_related(
            "manufacturer_order_item__order__manufacturer",
            "created_by",
        )
        .filter(code__iexact=code)
        .first()
    )
    manufacturer_name = "—"

    if batch is not None:
        item = batch.manufacturer_order_item
        manufacturer_name = item.order.manufacturer.name
        events.append(
            TraceEvent(
                at=batch.created_at,
                stage="Owner → Manufacturer (ordered)",
                from_label="Owner",
                to_label=manufacturer_name,
                quantity=item.quantity,
                reference=f"Manufacturer order {item.order.po_number}",
                by=batch.created_by,
            )
        )

    received_by_owner = zero
    sold_on_shopify = zero
    sent_to_distributors = zero

    def place(location):
        """A warehouse with the Inventory (region) it belongs to."""
        if location is None:
            return "—"
        if location.inventory_id and location.location_type == Location.LocationType.OWN:
            return f"{location.name} ({location.inventory.name})"
        return location.name

    for movement in (
        StockMovement.objects
        .filter(batch__batch_number__iexact=code)
        .select_related(
            "from_location__inventory",
            "to_location__inventory",
            "shopify_order",
            "created_by",
        )
    ):
        source = movement.from_location
        destination = movement.to_location
        from_label = place(source)
        to_label = place(destination)
        to_distributor = movement.channel == StockMovement.Channel.DISTRIBUTOR

        if movement.movement_type == StockMovement.MovementType.RECEIVED:
            received_by_owner += movement.quantity
            stage = "Manufacturer → Inventory (received)"
            from_label = manufacturer_name
        elif movement.movement_type == StockMovement.MovementType.SALE:
            sold_on_shopify += movement.quantity
            stage = "Inventory → Shopify (sold)"
            to_label = (
                f"Shopify order {movement.shopify_order.name}"
                if movement.shopify_order else "Shopify customer"
            )
        elif movement.movement_type == StockMovement.MovementType.ADJUSTMENT:
            stage = "Inventory (adjustment)"
            if movement.channel == StockMovement.Channel.SHOPIFY:
                stage = "Inventory (changed on Shopify)"
            if destination is None:
                to_label = "Removed from stock"
        elif to_distributor and (
            destination is None
            or destination.location_type == Location.LocationType.TRANSIT
        ):
            # A hand-off recorded before the Distributor panel was
            # archived. Stock that went through Transit is counted once,
            # when it left the warehouse.
            if source is not None and source.location_type == Location.LocationType.TRANSIT:
                continue
            sent_to_distributors += movement.quantity
            stage = "Inventory → Distributor (archived)"
            to_label = "Distributor"
        elif destination is None:
            continue
        elif destination.location_type == Location.LocationType.OWN:
            stage = "Inventory → Warehouse (stored)"
        else:
            stage = "Inventory (allocated to a region)"

        events.append(
            TraceEvent(
                at=movement.created_at,
                stage=stage,
                from_label=from_label,
                to_label=to_label,
                quantity=movement.quantity,
                reference=movement.reference,
                by=movement.created_by,
            )
        )

    owner_on_hand = zero
    where_now = {}

    for owner_batch in (
        StockBatch.objects
        .filter(batch_number__iexact=code, quantity_remaining__gt=0)
        .exclude(location__location_type=Location.LocationType.TRANSIT)
        .select_related("location__inventory")
    ):
        owner_on_hand += owner_batch.quantity_remaining
        label = place(owner_batch.location)
        where_now[label] = where_now.get(label, zero) + owner_batch.quantity_remaining

    events.sort(key=lambda event: event.at)

    return {
        "code": code,
        "batch": batch,
        "found": bool(batch or events),
        "events": events,
        "where_now": sorted(where_now.items()),
        "totals": {
            "ordered": batch.manufacturer_order_item.quantity if batch else None,
            "received_by_owner": received_by_owner,
            "owner_on_hand": owner_on_hand,
            "sold_on_shopify": sold_on_shopify,
            "sent_to_distributors": sent_to_distributors,
        },
    }
