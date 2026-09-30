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
    return Batch.objects.filter(code__startswith=f"{prefix}-").count() + 1


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


@dataclass
class DistributorTraceTotals:
    name: str
    received: Decimal = Decimal("0")
    on_hand: Decimal = Decimal("0")
    sold: Decimal = Decimal("0")


def trace_batch_code(code):
    """Follows one lot by its batch code through every hop the system
    tracks: the Owner orders it from the Manufacturer and receives it,
    ships it to a Distributor, the Distributor places it in a warehouse
    and finally hands it on to a sub-distributor — the last traced hop.

    The code is carried forward unchanged at every hop (Batch.code →
    StockBatch.batch_number → DistributorStockBatch.batch_number →
    SubDistributorSale.batch), so it is what ties the Owner's and every
    Distributor's otherwise separate ledgers together here. Read-only,
    and for the Owner's eyes only.

    Returns the canonical Batch (None for stock added by hand), every
    event oldest first, the quantity check-and-balance totals, and a
    per-Distributor breakdown."""
    from apps.distributor_inventory.models import (
        DistributorStockBatch,
        DistributorStockMovement,
        SubDistributorSale,
    )
    from apps.owner_inventory.models import StockBatch, StockMovement
    from apps.owner_warehouse.models import Location

    code = (code or "").strip()
    zero = Decimal("0")
    events = []
    by_distributor = {}

    def distributor_totals(profile):
        return by_distributor.setdefault(
            profile.pk, DistributorTraceTotals(name=profile.name)
        )

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

    for movement in (
        StockMovement.objects
        .filter(batch__batch_number__iexact=code)
        .select_related("from_location", "to_location", "created_by")
    ):
        source = movement.from_location
        destination = movement.to_location

        if movement.movement_type == StockMovement.MovementType.RECEIVED:
            received_by_owner += movement.quantity
            stage = "Manufacturer → Owner (received)"
        elif destination is None:
            # Leaving the Owner's system entirely — the Distributor's own
            # RECEIVED row records the same hand-off with the
            # Distributor's name, so it isn't listed twice.
            continue
        elif destination.location_type == Location.LocationType.TRANSIT:
            stage = "Owner → Distributor (shipped)"
        elif movement.movement_type == StockMovement.MovementType.ADJUSTMENT:
            stage = "Owner (adjustment)"
        else:
            stage = "Owner (moved between warehouses)"

        events.append(
            TraceEvent(
                at=movement.created_at,
                stage=stage,
                from_label=source.name if source else manufacturer_name,
                to_label=destination.name if destination else "—",
                quantity=movement.quantity,
                reference=movement.reference,
                by=movement.created_by,
            )
        )

    for movement in (
        DistributorStockMovement.objects
        .filter(batch__batch_number__iexact=code)
        .exclude(movement_type=DistributorStockMovement.MovementType.SOLD)
        .select_related(
            "distributor_profile", "from_location", "to_location", "created_by"
        )
    ):
        profile = movement.distributor_profile

        if movement.movement_type == DistributorStockMovement.MovementType.RECEIVED:
            distributor_totals(profile).received += movement.quantity
            stage = "Owner → Distributor (received)"
            from_label = "Owner"
        else:
            if movement.movement_type == DistributorStockMovement.MovementType.ADJUSTMENT:
                stage = "Distributor (adjustment)"
            else:
                stage = "Distributor (moved between warehouses)"
            from_label = (
                f"{profile.name} — {movement.from_location.name}"
                if movement.from_location
                else profile.name
            )

        events.append(
            TraceEvent(
                at=movement.created_at,
                stage=stage,
                from_label=from_label,
                to_label=(
                    f"{profile.name} — {movement.to_location.name}"
                    if movement.to_location
                    else profile.name
                ),
                quantity=movement.quantity,
                reference=movement.reference,
                by=movement.created_by,
            )
        )

    for sale in (
        SubDistributorSale.objects
        .filter(batch__batch_number__iexact=code)
        .select_related("distributor_profile", "from_location", "created_by")
    ):
        distributor_totals(sale.distributor_profile).sold += sale.quantity
        note = f" — {sale.note}" if sale.note else ""
        events.append(
            TraceEvent(
                at=sale.created_at,
                stage="Distributor → Sub-distributor (sold)",
                from_label=(
                    f"{sale.distributor_profile.name} — {sale.from_location.name}"
                ),
                to_label=sale.sub_distributor_name,
                quantity=sale.quantity,
                reference=f"Sale dated {sale.sale_date:%b %d, %Y}{note}",
                by=sale.created_by,
            )
        )

    for distributor_batch in (
        DistributorStockBatch.objects
        .filter(batch_number__iexact=code, quantity_remaining__gt=0)
        .select_related("distributor_profile")
    ):
        distributor_totals(distributor_batch.distributor_profile).on_hand += (
            distributor_batch.quantity_remaining
        )

    owner_on_hand = zero
    in_transit = zero

    for owner_batch in (
        StockBatch.objects
        .filter(batch_number__iexact=code, quantity_remaining__gt=0)
        .select_related("location")
    ):
        if owner_batch.location.location_type == Location.LocationType.TRANSIT:
            in_transit += owner_batch.quantity_remaining
        else:
            owner_on_hand += owner_batch.quantity_remaining

    events.sort(key=lambda event: event.at)
    distributors = sorted(by_distributor.values(), key=lambda row: row.name)

    return {
        "code": code,
        "batch": batch,
        "found": bool(batch or events),
        "events": events,
        "distributors": distributors,
        "totals": {
            "ordered": batch.manufacturer_order_item.quantity if batch else None,
            "received_by_owner": received_by_owner,
            "owner_on_hand": owner_on_hand,
            "in_transit": in_transit,
            "received_by_distributors": sum(
                (row.received for row in distributors), zero
            ),
            "distributor_on_hand": sum((row.on_hand for row in distributors), zero),
            "sold_to_sub_distributors": sum((row.sold for row in distributors), zero),
        },
    }
