from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import Inventory, Location


def inventory_payload(inventory):
    return {
        "code": inventory.code,
        "name": inventory.name,
        "active": inventory.active,
    }


@transaction.atomic
def create_inventory(*, actor, **data):
    require_owner(actor)

    inventory = Inventory(
        created_by=actor,
        updated_by=actor,
        **data,
    )
    inventory.full_clean()
    inventory.save()

    record_audit_event(
        user=actor,
        action="warehouse.inventory_created",
        instance=inventory,
        after_data=inventory_payload(inventory),
    )

    return inventory


@transaction.atomic
def update_inventory(*, actor, inventory_id, **data):
    require_owner(actor)

    inventory = (
        Inventory.objects
        .select_for_update()
        .filter(pk=inventory_id)
        .first()
    )

    if inventory is None:
        raise ValidationError("Inventory was not found.")

    before = inventory_payload(inventory)

    for field, value in data.items():
        setattr(inventory, field, value)

    inventory.updated_by = actor
    inventory.full_clean()
    inventory.save()

    record_audit_event(
        user=actor,
        action="warehouse.inventory_updated",
        instance=inventory,
        before_data=before,
        after_data=inventory_payload(inventory),
    )

    return inventory


def location_payload(location):
    return {
        "code": location.code,
        "name": location.name,
        "location_type": location.location_type,
        "inventory_id": (
            str(location.inventory_id)
            if location.inventory_id
            else None
        ),
        "manufacturer_id": (
            str(location.manufacturer_id)
            if location.manufacturer_id
            else None
        ),
        "on_book": location.on_book,
        "is_physical": location.is_physical,
        "is_sellable": location.is_sellable,
        "active": location.active,
    }


def normalize_location_flags(data):
    data = dict(data)
    location_type = data.get("location_type")

    if location_type in Location.FIXED_FLAGS:
        flags = ("on_book", "is_physical", "is_sellable")

        for flag, value in zip(flags, Location.FIXED_FLAGS[location_type]):
            if value is not None:
                data[flag] = value

    return data


@transaction.atomic
def create_location(*, actor, **data):
    require_owner(actor)

    data.setdefault("is_sellable", True)

    location = Location(
        created_by=actor,
        updated_by=actor,
        **normalize_location_flags(data),
    )

    location.full_clean()
    location.save()

    record_audit_event(
        user=actor,
        action="warehouse.location_created",
        instance=location,
        after_data=location_payload(location),
    )

    return location


def has_stock_movements(location):
    return (
        location.movements_out.exists()
        or location.movements_in.exists()
    )


@transaction.atomic
def update_location(*, actor, location_id, **data):
    require_owner(actor)

    location = (
        Location.objects
        .select_for_update()
        .filter(pk=location_id)
        .first()
    )

    if location is None:
        raise ValidationError(
            "Location was not found."
        )

    before = location_payload(location)

    new_type = data.get(
        "location_type",
        location.location_type,
    )

    if (
        new_type != location.location_type
        and has_stock_movements(location)
    ):
        raise ValidationError(
            "A location type cannot be changed "
            "after stock movements exist."
        )

    data = normalize_location_flags(data)

    for field, value in data.items():
        setattr(location, field, value)

    location.updated_by = actor
    location.full_clean()
    location.save()

    record_audit_event(
        user=actor,
        action="warehouse.location_updated",
        instance=location,
        before_data=before,
        after_data=location_payload(location),
    )

    if (before["is_sellable"], before["active"]) != (location.is_sellable, location.active):
        # Shopify shows the sellable warehouses' total, so holding a
        # warehouse back (or releasing it) changes what it can sell.
        from apps.owner_inventory.shopify_sync import schedule_push

        schedule_push()

    return location


def location_utilization(location):
    from apps.owner_inventory.models import StockBalance

    quantity = (
        StockBalance.objects
        .filter(location=location)
        .aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    capacity = location.capacity_units

    percentage = None
    warning = False

    if capacity > 0:
        percentage = (
            quantity / capacity
        ) * Decimal("100")
        warning = quantity > capacity

    return {
        "quantity_on_hand": quantity,
        "capacity_units": capacity,
        "utilization_percentage": percentage,
        "warning": warning,
    }


@transaction.atomic
def place_batch(*, actor, batch_id, allocations):
    """Validate and move an entire split together, preserving the stock ledger."""
    from apps.owner_inventory.models import StockBatch
    from apps.owner_inventory.services import get_or_create_unallocated_location, reallocate_batch

    require_owner(actor)
    batch = StockBatch.objects.select_for_update().get(pk=batch_id)
    source = batch.location
    if source.location_type != Location.LocationType.UNALLOCATED:
        raise ValidationError("This batch is already in a warehouse. Choose stock that is waiting to be placed.")
    if not allocations:
        raise ValidationError("Choose a destination and enter a quantity.")
    total, selected, checked = Decimal("0"), set(), []
    for destination, quantity in allocations:
        quantity = Decimal(quantity)
        if not quantity.is_finite() or quantity <= 0:
            raise ValidationError("Enter a quantity greater than zero for each destination.")
        key = (type(destination), destination.pk)
        if key in selected:
            raise ValidationError("Choose each destination once.")
        selected.add(key)
        if isinstance(destination, Inventory):
            destination = Inventory.objects.select_for_update().get(pk=destination.pk)
            if source.inventory_id or not destination.active:
                raise ValidationError("Choose an active region for this stock.")
        else:
            destination = Location.objects.select_for_update().get(pk=destination.pk)
            if (destination.location_type == Location.LocationType.UNALLOCATED
                    and destination.inventory_id and source.inventory_id is None):
                # Older clients can still submit the region's holding location.
                if not destination.active or not destination.inventory.active:
                    raise ValidationError("Choose an active region for this stock.")
            elif (destination.location_type != Location.LocationType.OWN
                  or not destination.active or not destination.inventory_id
                  or not destination.inventory.active):
                raise ValidationError("Choose an active warehouse in an active region.")
            if source.inventory_id and destination.inventory_id != source.inventory_id:
                raise ValidationError("Choose a warehouse in the batch's current region.")
        total += quantity
        checked.append((destination, quantity))
    if total > batch.quantity_remaining:
        raise ValidationError(
            f"Only {batch.quantity_remaining.normalize():f} units are still available. "
            "Another placement may have used some of this batch. Review the quantities and try again."
        )
    created = []
    for destination, quantity in checked:
        if isinstance(destination, Inventory):
            destination = get_or_create_unallocated_location(actor=actor, inventory=destination)
        created.append(reallocate_batch(
            actor=actor, batch=batch, quantity=quantity, destination_location=destination,
        ))
    return created
