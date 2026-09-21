from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_approved_distributor
from apps.audit.services import record_audit_event

from .models import DistributorStockBalance, DistributorStockBatch, DistributorStockMovement


def _as_decimal(quantity):
    try:
        return Decimal(quantity)
    except (InvalidOperation, TypeError):
        raise ValidationError("Quantity must be a number.")


def _get_or_create_balance(*, distributor_profile, product, location):
    balance, _ = DistributorStockBalance.objects.select_for_update().get_or_create(
        distributor_profile=distributor_profile,
        product=product,
        location=location,
        defaults={"quantity": Decimal("0")},
    )
    return balance


def available_batches_fefo(*, distributor_profile, product=None, location=None):
    """FEFO-ordered batches with stock left — the first one is the
    suggested default whenever the Distributor moves stock out of a
    warehouse."""
    queryset = (
        DistributorStockBatch.objects
        .filter(distributor_profile=distributor_profile)
        .available()
        .fefo_ordered()
    )

    if product is not None:
        queryset = queryset.filter(product=product)

    if location is not None:
        queryset = queryset.filter(location=location)

    return queryset


@transaction.atomic
def post_stock_movement(
    *,
    actor,
    distributor_profile,
    product,
    quantity,
    movement_type,
    from_location=None,
    to_location=None,
    reference="",
    require_distributor_actor=True,
    batch=None,
):
    """The single write path for a Distributor's own stock. Every other
    function in this module is a thin wrapper around this one — nothing
    else may touch DistributorStockBalance.

    require_distributor_actor=False is for callers already authorized by
    another check (e.g. the Owner recording a direct gift of stock, or
    the Purchase Order flow landing a confirmed receipt) — every other
    call site keeps the default self-service gate.
    """
    if require_distributor_actor:
        require_approved_distributor(actor)

        if actor.distributor_profile.pk != distributor_profile.pk:
            raise ValidationError(
                "You can only manage your own warehouse stock."
            )

    quantity = _as_decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    if not from_location and not to_location:
        raise ValidationError("A movement needs at least one location.")

    if from_location and from_location == to_location:
        raise ValidationError("Source and destination locations must differ.")

    if from_location:
        source_balance = _get_or_create_balance(
            distributor_profile=distributor_profile,
            product=product,
            location=from_location,
        )

        if source_balance.quantity < quantity:
            raise ValidationError(
                f"Not enough stock at {from_location.name} "
                f"({source_balance.quantity} available)."
            )

        source_balance.quantity -= quantity
        source_balance.save(update_fields=["quantity", "updated_at"])

        if batch is not None:
            batch = DistributorStockBatch.objects.select_for_update().get(pk=batch.pk)

            if batch.product_id != product.pk or batch.location_id != from_location.pk:
                raise ValidationError(
                    "The selected batch does not match this product/warehouse."
                )

            if batch.quantity_remaining < quantity:
                raise ValidationError(
                    f"Only {batch.quantity_remaining} remaining in that batch."
                )

            batch.quantity_remaining -= quantity
            batch.updated_by = actor
            batch.save(update_fields=["quantity_remaining", "updated_at", "updated_by"])

    if to_location:
        destination_balance = _get_or_create_balance(
            distributor_profile=distributor_profile,
            product=product,
            location=to_location,
        )
        destination_balance.quantity += quantity
        destination_balance.save(update_fields=["quantity", "updated_at"])

    movement = DistributorStockMovement(
        distributor_profile=distributor_profile,
        product=product,
        from_location=from_location,
        to_location=to_location,
        quantity=quantity,
        movement_type=movement_type,
        reference=reference,
        batch=batch,
        created_by=actor,
        updated_by=actor,
    )
    movement.full_clean()
    movement.save()

    record_audit_event(
        user=actor,
        action="distributor_inventory.stock_movement_posted",
        instance=movement,
        after_data={
            "distributor_profile_id": str(distributor_profile.pk),
            "product_id": str(product.pk),
            "quantity": str(quantity),
            "movement_type": movement_type,
            "from_location_id": (
                str(from_location.pk) if from_location else None
            ),
            "to_location_id": (
                str(to_location.pk) if to_location else None
            ),
        },
        reason=reference,
    )

    return movement


def get_or_create_unallocated_location(
    *, actor, distributor_profile, distributor_inventory=None
):
    """The Distributor's own holding pool for stock that's arrived but
    hasn't been placed into a named warehouse yet: one pool per
    Distributor (distributor_inventory=None) for stock not yet assigned
    to a region, and one per region for stock assigned to a region but
    not yet split across its warehouses. Mirrors the Owner's unallocated
    pool concept exactly, scoped to this Distributor only."""
    from apps.distributor_warehouse.models import DistributorLocation

    location = DistributorLocation.objects.filter(
        distributor_profile=distributor_profile,
        location_type=DistributorLocation.LocationType.UNALLOCATED,
        distributor_inventory=distributor_inventory,
    ).first()

    if location is not None:
        return location

    if distributor_inventory is None:
        code = "UNALLOCATED"
        name = "Unallocated"
    else:
        code = f"UNALLOCATED-{distributor_inventory.code}"[:50]
        name = f"Unallocated — {distributor_inventory.name}"

    location = DistributorLocation(
        distributor_profile=distributor_profile,
        code=code,
        name=name,
        location_type=DistributorLocation.LocationType.UNALLOCATED,
        distributor_inventory=distributor_inventory,
        created_by=actor,
        updated_by=actor,
    )
    location.full_clean()
    location.save()

    return location


def unallocated_batches(*, distributor_profile, distributor_inventory=None, product=None):
    """Stock sitting in a holding pool, waiting to be placed into a named
    warehouse."""
    from apps.distributor_warehouse.models import DistributorLocation

    location = DistributorLocation.objects.filter(
        distributor_profile=distributor_profile,
        location_type=DistributorLocation.LocationType.UNALLOCATED,
        distributor_inventory=distributor_inventory,
    ).first()

    if location is None:
        return DistributorStockBatch.objects.none()

    return available_batches_fefo(
        distributor_profile=distributor_profile, location=location, product=product
    )


@transaction.atomic
def reallocate_batch(
    *, actor, distributor_profile, batch, quantity, destination_location, reference=""
):
    """The "Allocate" action: moves stock out of an unallocated holding
    pool into somewhere specific — either another region's holding pool
    (stage 1: pick the region) or a named warehouse (stage 2: pick the
    warehouse within that region). Preserves the batch's original
    received/expiry dates and batch number."""
    from apps.distributor_warehouse.models import DistributorLocation

    require_approved_distributor(actor)

    if actor.distributor_profile.pk != distributor_profile.pk:
        raise ValidationError("You can only manage your own warehouse stock.")

    if batch is None:
        raise ValidationError("A batch to allocate is required.")

    if batch.location.location_type != DistributorLocation.LocationType.UNALLOCATED:
        raise ValidationError("Only unallocated stock can be allocated.")

    if destination_location.distributor_profile_id != distributor_profile.pk:
        raise ValidationError("That destination belongs to a different distributor.")

    quantity = _as_decimal(quantity)

    post_stock_movement(
        actor=actor,
        distributor_profile=distributor_profile,
        product=batch.product,
        quantity=quantity,
        movement_type=DistributorStockMovement.MovementType.TRANSFER,
        from_location=batch.location,
        to_location=destination_location,
        reference=reference,
        batch=batch,
    )

    new_batch = DistributorStockBatch(
        distributor_profile=distributor_profile,
        product=batch.product,
        location=destination_location,
        batch_number=batch.batch_number,
        received_date=batch.received_date,
        expiry_date=batch.expiry_date,
        quantity_received=quantity,
        quantity_remaining=quantity,
        reference=reference or f"Allocated from {batch.location.name}",
        created_by=actor,
        updated_by=actor,
    )
    new_batch.full_clean()
    new_batch.save()

    return new_batch


@transaction.atomic
def receive_stock(
    *,
    actor,
    distributor_profile,
    product,
    quantity,
    to_location=None,
    reference="",
    received_date=None,
    expiry_date=None,
    batch_number="",
    require_distributor_actor=True,
):
    """Stock arriving into the Distributor's own tracked system — either
    a confirmed Purchase Order shipment from the Owner, or a manual entry.
    Lands as its own dated batch in the unallocated pool by default,
    prompting the Distributor to allocate it into a named warehouse —
    the same "receive, then allocate" shape as the Owner's own inventory."""
    if to_location is None:
        to_location = get_or_create_unallocated_location(
            actor=actor, distributor_profile=distributor_profile
        )

    quantity = _as_decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    batch = DistributorStockBatch(
        distributor_profile=distributor_profile,
        product=product,
        location=to_location,
        batch_number=(batch_number or "").strip(),
        received_date=received_date or date.today(),
        expiry_date=expiry_date,
        quantity_received=quantity,
        quantity_remaining=quantity,
        reference=reference,
        created_by=actor,
        updated_by=actor,
    )
    batch.full_clean()
    batch.save()

    return post_stock_movement(
        actor=actor,
        distributor_profile=distributor_profile,
        product=product,
        quantity=quantity,
        movement_type=DistributorStockMovement.MovementType.RECEIVED,
        from_location=None,
        to_location=to_location,
        reference=reference,
        batch=batch,
        require_distributor_actor=require_distributor_actor,
    )
