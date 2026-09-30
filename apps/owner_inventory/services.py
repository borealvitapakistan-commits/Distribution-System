from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_approved_distributor, require_owner
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
):
    """The single write path for stock. Every other function in this
    module is a thin wrapper around this one — nothing else may touch
    StockBalance.

    require_owner_actor=False is only for callers that have already done
    their own equally-strict authorization check (e.g. a Distributor
    confirming receipt of their own shipment) — every other call site
    keeps the default Owner-only gate.

    batch, when a movement draws stock OUT of an owned warehouse
    (from_location set), is the specific StockBatch it's drawn from —
    its quantity_remaining is decremented alongside the balance. When a
    movement is a RECEIVED movement, batch is the lot it just created,
    attached to the movement for traceability only (no draw-down).
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
        Location.LocationType.SHOPIFY,
        Location.LocationType.UNALLOCATED,
    ):
        raise ValidationError(
            "Inventory can only be received into one of the Owner's own "
            "warehouses, the Shopify location, or as unallocated stock."
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


@transaction.atomic
def give_to_distributor(
    *,
    actor,
    product,
    quantity,
    from_location,
    distributor_profile,
    batch,
    reference="",
):
    """What the Owner's "Give to Distributor" form calls. The batch
    chosen (normally the oldest/soonest-to-expire one, FEFO) determines
    which of the Owner's own warehouses this ships from. The stock leaves
    the Owner's tracked system entirely and lands in the Distributor's own
    (separately tracked) unallocated pool, the same as any other stock
    arriving in their warehouse system — they never see the Owner's
    batches or warehouse names, and this Owner ledger has no relationship
    to theirs beyond the one-time hand-off."""
    from apps.distributor_inventory.services import receive_stock as distributor_receive_stock
    from apps.owner_warehouse.models import Location

    if batch is None:
        raise ValidationError("A batch to ship from is required.")

    if from_location.location_type != Location.LocationType.OWN:
        raise ValidationError(
            "Stock can only be shipped from one of the Owner's own warehouses."
        )

    movement = post_stock_movement(
        actor=actor,
        product=product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.TRANSFER,
        from_location=from_location,
        to_location=None,
        reference=reference,
        batch=batch,
    )

    distributor_receive_stock(
        actor=actor,
        distributor_profile=distributor_profile,
        product=product,
        quantity=quantity,
        reference=reference or f"Gift from {from_location.name}",
        batch_number=batch.batch_number,
        expiry_date=batch.expiry_date,
        require_distributor_actor=False,
    )

    return movement


def _get_or_create_transit_location(*, actor):
    from apps.owner_warehouse.models import Location

    location = Location.objects.filter(
        location_type=Location.LocationType.TRANSIT,
    ).first()

    if location is not None:
        return location

    location = Location(
        code="TRANSIT",
        name="In Transit",
        location_type=Location.LocationType.TRANSIT,
        on_book=True,
        is_physical=True,
        is_sellable=False,
        created_by=actor,
        updated_by=actor,
    )
    location.full_clean()
    location.save()

    return location


@transaction.atomic
def ship_stock_to_transit(
    *, actor, product, quantity, from_location, batch, reference="", shipped_for=None
):
    """What a Purchase Order's "Ship" action calls: stock leaves one of the
    Owner's own warehouses but isn't credited to the Distributor yet — it
    sits in Transit until the Distributor confirms receipt. The batch it's
    drawn from (the Owner's own choice, normally FEFO-suggested) still
    gets decremented here, since that's when it physically leaves the
    Owner's warehouse — and a new lot lands in Transit carrying the same
    batch code/expiry forward, so that choice survives all the way to the
    Distributor's own stock."""
    from apps.owner_warehouse.models import Location

    if batch is None:
        raise ValidationError("A batch to ship from is required.")

    if from_location.location_type != Location.LocationType.OWN:
        raise ValidationError(
            "Stock can only be shipped from one of the Owner's own warehouses."
        )

    transit = _get_or_create_transit_location(actor=actor)
    quantity = _as_decimal(quantity)

    movement = post_stock_movement(
        actor=actor,
        product=product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.TRANSFER,
        from_location=from_location,
        to_location=transit,
        reference=reference,
        batch=batch,
    )

    transit_batch = StockBatch(
        product=product,
        location=transit,
        source_batch=batch.source_batch,
        shipped_for=shipped_for,
        batch_number=batch.batch_number,
        received_date=batch.received_date,
        expiry_date=batch.expiry_date,
        quantity_received=quantity,
        quantity_remaining=quantity,
        reference=reference or f"Shipped from {from_location.name}",
        created_by=actor,
        updated_by=actor,
    )
    transit_batch.full_clean()
    transit_batch.save()

    return movement


@transaction.atomic
def release_stock_from_transit(
    *, actor, product, quantity, reference="", shipped_for=None
):
    """What a Purchase Order's "Received" action calls: draws down the
    specific Transit batch(es) for this product (FEFO — a receipt can
    span more than one, if separate shipments landed there), closing out
    the Owner's side of the hand-off. Returns [(batch, quantity), ...]
    consumed, so the caller can hand each one to the Distributor's own
    (separately tracked) system with its correct batch code and expiry —
    see apps.distributor_inventory.services.receive_stock. Triggered by
    the Distributor confirming their own shipment — not an Owner action,
    so it does its own authorization (approved Distributor, acting on
    their own profile) before writing to this ledger."""
    require_approved_distributor(actor)

    transit = _get_or_create_transit_location(actor=actor)
    quantity = _as_decimal(quantity)

    transit_batches = (
        StockBatch.objects
        .select_for_update()
        .filter(location=transit, product=product)
        .available()
        .fefo_ordered()
    )

    # Only the lots shipped on this order line — every batch keeps its own
    # identity, so another order's lot of the same product is never used.
    if shipped_for is not None:
        transit_batches = transit_batches.filter(shipped_for=shipped_for)

    remaining = quantity
    consumed = []

    for batch in transit_batches:
        if remaining <= 0:
            break

        take = min(remaining, batch.quantity_remaining)

        post_stock_movement(
            actor=actor,
            product=product,
            quantity=take,
            movement_type=StockMovement.MovementType.TRANSFER,
            from_location=transit,
            to_location=None,
            reference=reference,
            batch=batch,
            require_owner_actor=False,
        )
        consumed.append((batch, take))
        remaining -= take

    if remaining > 0:
        raise ValidationError(
            f"Only {quantity - remaining} of {product.name} available in Transit."
        )

    return consumed
