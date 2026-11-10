from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import StockBalance, StockBatch, StockMovement


def _as_decimal(quantity):
    try:
        return Decimal(quantity)
    except (InvalidOperation, TypeError):
        raise ValidationError("Quantity must be a number.")


def _get_or_create_balance(*, product, location):
    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        product=product,
        location=location,
        defaults={"quantity": Decimal("0")},
    )
    return balance


def available_batches_fefo(*, product=None, location=None):
    """FEFO-ordered batches with stock left — the first one is the
    suggested default whenever the Owner ships stock out."""
    queryset = StockBatch.objects.available().fefo_ordered()

    if product is not None:
        queryset = queryset.filter(product=product)

    if location is not None:
        queryset = queryset.filter(location=location)

    return queryset


def shippable_batches_fefo(*, product=None):
    """Batches the Owner can actually ship out: in an owned warehouse
    that's currently marked sellable."""
    from apps.owner_warehouse.models import Location

    return available_batches_fefo(product=product).filter(
        location__location_type=Location.LocationType.OWN,
        location__is_sellable=True,
    )


@transaction.atomic
def post_stock_movement(
    *,
    actor,
    product,
    quantity,
    movement_type,
    from_location=None,
    to_location=None,
    reference="",
    require_owner_actor=True,
    batch=None,
    channel="",
    shopify_order=None,
):
    """The single write path for stock. Every other function in this
    module is a thin wrapper around this one — nothing else may touch
    StockBalance.

    require_owner_actor=False is only for callers that have already done
    their own equally-strict authorization check — every other call site
    keeps the default Owner-only gate.

    batch, when a movement draws stock OUT of an owned warehouse
    (from_location set), is the specific StockBatch it's drawn from —
    its quantity_remaining is decremented alongside the balance. When a
    movement is a RECEIVED movement, batch is the lot it just created,
    attached to the movement for traceability only (no draw-down).

    channel records who the stock went to or came from (Manufacturer,
    Shopify); shopify_order names the exact Shopify order, so a batch
    trace can say who got it. Once the transaction commits, Shopify is told
    the new sellable total — see apps.owner_inventory.shopify_sync.
    """
    if require_owner_actor:
        require_owner(actor)

    quantity = _as_decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    if not from_location and not to_location:
        raise ValidationError("A movement needs at least one location.")

    if from_location and from_location == to_location:
        raise ValidationError("Source and destination locations must differ.")

    if from_location:
        source_balance = _get_or_create_balance(
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
            batch = StockBatch.objects.select_for_update().get(pk=batch.pk)

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
            product=product,
            location=to_location,
        )
        destination_balance.quantity += quantity
        destination_balance.save(update_fields=["quantity", "updated_at"])

    movement = StockMovement(
        product=product,
        from_location=from_location,
        to_location=to_location,
        quantity=quantity,
        movement_type=movement_type,
        channel=channel,
        shopify_order=shopify_order,
        reference=reference,
        batch=batch,
        created_by=actor,
        updated_by=actor,
    )
    movement.full_clean()
    movement.save()

    record_audit_event(
        user=actor,
        action="inventory.stock_movement_posted",
        instance=movement,
        after_data={
            "product_id": str(product.pk),
            "quantity": str(quantity),
            "movement_type": movement_type,
            "channel": channel,
            "batch_number": batch.batch_number if batch is not None else "",
            "shopify_order": shopify_order.name if shopify_order else None,
            "from_location_id": (
                str(from_location.pk) if from_location else None
            ),
            "to_location_id": (
                str(to_location.pk) if to_location else None
            ),
        },
        reason=reference,
    )

    from .shopify_sync import schedule_push

    schedule_push()

    return movement


@transaction.atomic
def receive_stock(
    *,
    actor,
    product,
    quantity,
    to_location,
    reference="",
    received_date=None,
    expiry_date=None,
    batch_number="",
    source_batch=None,
    channel="",
):
    """What the Owner's "Add Inventory" form calls: stock arriving from
    outside the system (e.g. from a Manufacturer) into one of the
    Owner's own warehouses. Every call creates its own dated batch/lot —
    herbal products expire, so stock added today is never merged into
    stock added last week."""
    from apps.owner_warehouse.models import Location

    require_owner(actor)

    if to_location.location_type not in (
        Location.LocationType.OWN,
        Location.LocationType.UNALLOCATED,
    ):
        raise ValidationError(
            "Inventory can only be received into one of the Owner's own "
            "warehouses or as unallocated stock."
        )

    quantity = _as_decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    batch = StockBatch(
        product=product,
        location=to_location,
        source_batch=source_batch,
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
        product=product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.RECEIVED,
        from_location=None,
        to_location=to_location,
        reference=reference,
        batch=batch,
        channel=channel,
    )


def get_or_create_unallocated_location(*, actor, inventory=None):
    """The holding pool for stock that's arrived but hasn't been placed
    yet: one global pool (inventory=None) for stock not yet assigned to
    a region, and one per Inventory for stock assigned to a region but
    not yet split across its warehouses."""
    from apps.owner_warehouse.models import Location

    location = Location.objects.filter(
        location_type=Location.LocationType.UNALLOCATED,
        inventory=inventory,
    ).first()

    if location is not None:
        return location

    if inventory is None:
        code = "UNALLOCATED"
        name = "Unallocated"
    else:
        code = f"UNALLOCATED-{inventory.code}"[:50]
        name = f"Unallocated — {inventory.name}"

    location = Location(
        code=code,
        name=name,
        location_type=Location.LocationType.UNALLOCATED,
        inventory=inventory,
        on_book=True,
        is_physical=True,
        is_sellable=False,
        created_by=actor,
        updated_by=actor,
    )
    location.full_clean()
    location.save()

    return location


def unallocated_batches(*, inventory=None, product=None):
    """Stock sitting in a holding pool, waiting to be placed. inventory=None
    is the global pool (not yet assigned to any region); pass an Inventory
    for stock assigned to that region but not yet split across warehouses."""
    from apps.owner_warehouse.models import Location

    location = Location.objects.filter(
        location_type=Location.LocationType.UNALLOCATED,
        inventory=inventory,
    ).first()

    if location is None:
        return StockBatch.objects.none()

    return available_batches_fefo(location=location, product=product)


@transaction.atomic
def reallocate_batch(*, actor, batch, quantity, destination_location, reference=""):
    """The "Allocate" action: moves stock out of an unallocated holding
    pool into somewhere specific — either another Inventory's holding
    pool (stage 1: pick the region) or a named warehouse (stage 2: pick
    the warehouse within that region). Preserves the batch's original
    received/expiry dates and batch number, since moving stock between
    rooms doesn't change how old it is or which delivery it came from."""
    from apps.owner_warehouse.models import Location

    require_owner(actor)

    if batch is None:
        raise ValidationError("A batch to allocate is required.")

    if batch.location.location_type != Location.LocationType.UNALLOCATED:
        raise ValidationError("Only unallocated stock can be allocated.")

    if destination_location.location_type not in (
        Location.LocationType.UNALLOCATED,
        Location.LocationType.OWN,
    ):
        raise ValidationError(
            "Stock can only be allocated to a region or a warehouse."
        )

    quantity = _as_decimal(quantity)

    post_stock_movement(
        actor=actor,
        product=batch.product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.TRANSFER,
        from_location=batch.location,
        to_location=destination_location,
        reference=reference,
        batch=batch,
        channel=StockMovement.Channel.INTERNAL,
    )

    new_batch = StockBatch(
        product=batch.product,
        location=destination_location,
        source_batch=batch.source_batch,
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

