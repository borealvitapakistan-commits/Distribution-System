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
