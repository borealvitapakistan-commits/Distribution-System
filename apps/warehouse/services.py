from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import Location


def location_payload(location):
    return {
        "code": location.code,
        "name": location.name,
        "location_type": location.location_type,
        "manufacturer_id": (
            str(location.manufacturer_id)
            if location.manufacturer_id
            else None
        ),
        "distributor_profile_id": (
            str(location.distributor_profile_id)
            if location.distributor_profile_id
            else None
        ),
        "customer_id": (
            str(location.customer_id)
            if location.customer_id
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

    fixed_flags = {
        Location.LocationType.OWN: (
            True,
            True,
            True,
        ),
        Location.LocationType.SHOPIFY: (
            True,
            True,
            True,
        ),
        Location.LocationType.SUPPLIER: (
            False,
            False,
            False,
        ),
        Location.LocationType.CUSTOMER: (
            False,
            False,
            False,
        ),
    }

    if location_type in fixed_flags:
        (
            data["on_book"],
            data["is_physical"],
            data["is_sellable"],
        ) = fixed_flags[location_type]

    elif location_type == Location.LocationType.DISTRIBUTOR:
        data["on_book"] = True
        data["is_physical"] = True
        data.setdefault("is_sellable", False)

    return data


@transaction.atomic
def create_location(*, actor, **data):
    require_owner(actor)

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

    return location


def location_utilization(location):
    from apps.inventory.models import StockBalance

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
