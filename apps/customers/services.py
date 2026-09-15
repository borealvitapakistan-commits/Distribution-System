from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import Customer


def _customer_payload(customer):
    return {
        "name": customer.name,
        "phone": customer.phone,
        "email": customer.email,
        "active": customer.active,
    }


@transaction.atomic
def create_customer(*, actor, **data):
    require_owner(actor)

    customer = Customer(
        created_by=actor,
        updated_by=actor,
        **data,
    )
    customer.full_clean()
    customer.save()

    record_audit_event(
        user=actor,
        action="customer.created",
        instance=customer,
        after_data=_customer_payload(customer),
    )
    return customer


@transaction.atomic
def update_customer(*, actor, customer_id, **data):
    require_owner(actor)

    customer = (
        Customer.objects.select_for_update()
        .filter(pk=customer_id)
        .first()
    )
    if customer is None:
        raise ValidationError("Customer was not found.")

    before = _customer_payload(customer)
    for field, value in data.items():
        setattr(customer, field, value)
    customer.updated_by = actor
    customer.full_clean()
    customer.save()

    record_audit_event(
        user=actor,
        action="customer.updated",
        instance=customer,
        before_data=before,
        after_data=_customer_payload(customer),
    )
    return customer
