from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from apps.accounts.services import require_approved_distributor
from apps.audit.services import record_audit_event

from .models import DistributorInventory, DistributorLocation


def _actor_profile(actor):
    require_approved_distributor(actor)
    return actor.distributor_profile


def inventory_payload(inventory):
    return {
        "code": inventory.code,
        "name": inventory.name,
        "active": inventory.active,
    }


@transaction.atomic
def create_inventory(*, actor, **data):
    profile = _actor_profile(actor)

    inventory = DistributorInventory(
        distributor_profile=profile,
        created_by=actor,
        updated_by=actor,
        **data,
    )
    inventory.full_clean()
    inventory.save()

    record_audit_event(
        user=actor,
        action="distributor_warehouse.inventory_created",
        instance=inventory,
        after_data=inventory_payload(inventory),
    )

    return inventory


@transaction.atomic
def update_inventory(*, actor, inventory_id, **data):
    profile = _actor_profile(actor)

    inventory = (
        DistributorInventory.objects
        .select_for_update()
        .filter(pk=inventory_id, distributor_profile=profile)
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
        action="distributor_warehouse.inventory_updated",
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
        "distributor_inventory_id": (
            str(location.distributor_inventory_id)
            if location.distributor_inventory_id
            else None
        ),
        "active": location.active,
    }


@transaction.atomic
def create_location(*, actor, **data):
    profile = _actor_profile(actor)

    location = DistributorLocation(
        distributor_profile=profile,
        created_by=actor,
        updated_by=actor,
        **data,
    )
    location.full_clean()
    location.save()

    record_audit_event(
        user=actor,
        action="distributor_warehouse.location_created",
        instance=location,
        after_data=location_payload(location),
    )

    return location


def has_stock_movements(location):
    from apps.distributor_inventory.models import DistributorStockMovement

    return DistributorStockMovement.objects.filter(
        Q(from_location=location) | Q(to_location=location)
    ).exists()


@transaction.atomic
def update_location(*, actor, location_id, **data):
    profile = _actor_profile(actor)

    location = (
        DistributorLocation.objects
        .select_for_update()
        .filter(pk=location_id, distributor_profile=profile)
        .first()
    )

    if location is None:
        raise ValidationError("Location was not found.")

    before = location_payload(location)

    new_type = data.get("location_type", location.location_type)

    if new_type != location.location_type and has_stock_movements(location):
        raise ValidationError(
            "A location type cannot be changed after stock movements exist."
        )

    for field, value in data.items():
        setattr(location, field, value)

    location.updated_by = actor
    location.full_clean()
    location.save()

    record_audit_event(
        user=actor,
        action="distributor_warehouse.location_updated",
        instance=location,
        before_data=before,
        after_data=location_payload(location),
    )

    return location


def location_utilization(location):
    from apps.distributor_inventory.models import DistributorStockBalance

    quantity = (
        DistributorStockBalance.objects
        .filter(location=location)
        .aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    capacity = location.capacity_units

    percentage = None
    warning = False

    if capacity > 0:
        percentage = (quantity / capacity) * Decimal("100")
        warning = quantity > capacity

    return {
        "quantity_on_hand": quantity,
        "capacity_units": capacity,
        "utilization_percentage": percentage,
        "warning": warning,
    }
