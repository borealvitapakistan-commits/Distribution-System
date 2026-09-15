from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import StockBalance, StockMovement


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
):
    """The single write path for stock. Every other function in this
    module is a thin wrapper around this one — nothing else may touch
    StockBalance."""
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


def receive_stock(*, actor, product, quantity, to_location, reference=""):
    """What the Owner's "Add Inventory" form calls: stock arriving from
    outside the system (e.g. from a Manufacturer) into one of the
    Owner's own warehouses."""
    return post_stock_movement(
        actor=actor,
        product=product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.RECEIVED,
        from_location=None,
        to_location=to_location,
        reference=reference,
    )


def _get_or_create_distributor_location(*, actor, distributor_profile):
    from apps.warehouse.models import Location

    location = Location.objects.filter(
        location_type=Location.LocationType.DISTRIBUTOR,
        distributor_profile=distributor_profile,
    ).first()

    if location is not None:
        return location

    code = (
        f"DIST-{slugify(distributor_profile.name).upper()}"[:50]
        or f"DIST-{distributor_profile.pk}"
    )

    location = Location(
        code=code,
        name=f"{distributor_profile.name} Stock",
        location_type=Location.LocationType.DISTRIBUTOR,
        distributor_profile=distributor_profile,
        on_book=True,
        is_physical=True,
        is_sellable=False,
        created_by=actor,
        updated_by=actor,
    )
    location.full_clean()
    location.save()

    return location


def give_to_distributor(
    *,
    actor,
    product,
    quantity,
    from_location,
    distributor_profile,
    reference="",
):
    """What the Owner's "Give to Distributor" form calls. The Owner picks
    which of their own warehouses this ships from; the Distributor's own
    stock location is resolved automatically (created on first use) —
    the Distributor never sees or picks a warehouse."""
    from apps.warehouse.models import Location

    if from_location.location_type != Location.LocationType.OWN:
        raise ValidationError(
            "Stock can only be shipped from one of the Owner's own warehouses."
        )

    destination = _get_or_create_distributor_location(
        actor=actor,
        distributor_profile=distributor_profile,
    )

    return post_stock_movement(
        actor=actor,
        product=product,
        quantity=quantity,
        movement_type=StockMovement.MovementType.TRANSFER,
        from_location=from_location,
        to_location=destination,
        reference=reference,
    )
