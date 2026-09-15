from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import Manufacturer


def _manufacturer_payload(manufacturer):
    return {
        "name": manufacturer.name,
        "phone": manufacturer.phone,
        "email": manufacturer.email,
        "active": manufacturer.active,
    }


@transaction.atomic
def create_manufacturer(*, actor, **data):
    require_owner(actor)

    manufacturer = Manufacturer(
        created_by=actor,
        updated_by=actor,
        **data,
    )
    manufacturer.full_clean()
    manufacturer.save()

    record_audit_event(
        user=actor,
        action="manufacturer.created",
        instance=manufacturer,
        after_data=_manufacturer_payload(manufacturer),
    )
    return manufacturer


@transaction.atomic
def update_manufacturer(*, actor, manufacturer_id, **data):
    require_owner(actor)

    manufacturer = (
        Manufacturer.objects.select_for_update()
        .filter(pk=manufacturer_id)
        .first()
    )
    if manufacturer is None:
        raise ValidationError("Manufacturer was not found.")

    before = _manufacturer_payload(manufacturer)
    for field, value in data.items():
        setattr(manufacturer, field, value)
    manufacturer.updated_by = actor
    manufacturer.full_clean()
    manufacturer.save()

    record_audit_event(
        user=actor,
        action="manufacturer.updated",
        instance=manufacturer,
        before_data=before,
        after_data=_manufacturer_payload(manufacturer),
    )
    return manufacturer
