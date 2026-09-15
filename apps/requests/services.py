from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_approved_distributor, require_owner
from apps.audit.services import record_audit_event
from apps.inventory.services import give_to_distributor

from .models import StockRequest, StockRequestItem


def _as_decimal(quantity):
    try:
        return Decimal(quantity)
    except (InvalidOperation, TypeError):
        raise ValidationError("Quantity must be a number.")


def _validate_items(items):
    if not items:
        raise ValidationError("A request needs at least one product.")

    rows = []
    seen = set()

    for row in items:
        product = row.get("product")
        quantity = row.get("quantity_requested")

        if not product:
            raise ValidationError("Every row needs a product.")

        if product.pk in seen:
            raise ValidationError(
                "A product can appear only once on a request."
            )

        seen.add(product.pk)

        quantity = _as_decimal(quantity)

        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        rows.append({"product": product, "quantity_requested": quantity})

    return rows


@transaction.atomic
def create_stock_request(*, actor, items):
    require_approved_distributor(actor)

    rows = _validate_items(items)

    distributor_profile = actor.distributor_profile

    request = StockRequest(
        distributor_profile=distributor_profile,
        created_by=actor,
        updated_by=actor,
    )
    request.full_clean()
    request.save()

    lines = [
        StockRequestItem(
            request=request,
            product=row["product"],
            quantity_requested=row["quantity_requested"],
            created_by=actor,
            updated_by=actor,
        )
        for row in rows
    ]

    for line in lines:
        line.full_clean()

    StockRequestItem.objects.bulk_create(lines)

    record_audit_event(
        user=actor,
        action="requests.stock_request_created",
        instance=request,
        after_data={
            "distributor_profile_id": str(distributor_profile.pk),
            "items": [
                {
                    "product_id": str(row["product"].pk),
                    "quantity_requested": str(row["quantity_requested"]),
                }
                for row in rows
            ],
        },
    )

    return request


def _recompute_status(request):
    items = list(request.items.all())

    if request.status == StockRequest.Status.DECLINED:
        return

    if all(item.quantity_fulfilled >= item.quantity_requested for item in items):
        request.status = StockRequest.Status.FULFILLED
    elif any(item.quantity_fulfilled > 0 for item in items):
        request.status = StockRequest.Status.PARTIALLY_FULFILLED
    else:
        request.status = StockRequest.Status.PENDING

    request.save(update_fields=["status", "updated_at"])


@transaction.atomic
def fulfill_request_item(*, actor, item_id, quantity, from_location):
    require_owner(actor)

    item = (
        StockRequestItem.objects
        .select_for_update()
        .select_related("request")
        .filter(pk=item_id)
        .first()
    )

    if item is None:
        raise ValidationError("Request line was not found.")

    if item.request.status == StockRequest.Status.DECLINED:
        raise ValidationError("This request has been declined.")

    quantity = _as_decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    if quantity > item.quantity_remaining:
        raise ValidationError(
            f"Only {item.quantity_remaining} remaining on this line."
        )

    give_to_distributor(
        actor=actor,
        product=item.product,
        quantity=quantity,
        from_location=from_location,
        distributor_profile=item.request.distributor_profile,
        reference=f"Stock request {item.request.pk}",
    )

    item.quantity_fulfilled += quantity
    item.updated_by = actor
    item.save(update_fields=["quantity_fulfilled", "updated_at", "updated_by"])

    _recompute_status(item.request)

    record_audit_event(
        user=actor,
        action="requests.stock_request_item_fulfilled",
        instance=item,
        after_data={
            "quantity_sent": str(quantity),
            "quantity_fulfilled": str(item.quantity_fulfilled),
            "from_location_id": str(from_location.pk),
        },
    )

    return item


@transaction.atomic
def decline_request(*, actor, request_id, comment):
    require_owner(actor)

    comment = (comment or "").strip()

    if not comment:
        raise ValidationError("A comment is required to decline a request.")

    request = (
        StockRequest.objects
        .select_for_update()
        .filter(pk=request_id)
        .first()
    )

    if request is None:
        raise ValidationError("Request was not found.")

    request.status = StockRequest.Status.DECLINED
    request.owner_comment = comment
    request.updated_by = actor
    request.save(
        update_fields=["status", "owner_comment", "updated_at", "updated_by"]
    )

    record_audit_event(
        user=actor,
        action="requests.stock_request_declined",
        instance=request,
        after_data={"owner_comment": comment},
    )

    return request


@transaction.atomic
def add_owner_comment(*, actor, request_id, comment):
    require_owner(actor)

    request = (
        StockRequest.objects
        .select_for_update()
        .filter(pk=request_id)
        .first()
    )

    if request is None:
        raise ValidationError("Request was not found.")

    request.owner_comment = (comment or "").strip()
    request.updated_by = actor
    request.save(update_fields=["owner_comment", "updated_at", "updated_by"])

    record_audit_event(
        user=actor,
        action="requests.stock_request_commented",
        instance=request,
        after_data={"owner_comment": request.owner_comment},
    )

    return request
