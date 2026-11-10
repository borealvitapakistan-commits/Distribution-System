"""Shared helpers for tests that walk a Manufacturer order forward."""

from django.utils import timezone

from .models import ManufacturerOrder


def stamp_purchase_order(order_id):
    """Marks the order's Purchase Order as stamped — the step before the
    payment proof — without writing a file to disk."""
    ManufacturerOrder.objects.filter(pk=order_id).update(
        stamped_po_file="tests/stamped-po.png",
        stamped_po_uploaded_at=timezone.now(),
    )
