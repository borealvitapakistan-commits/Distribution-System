"""Review and resolve supplier links without rewriting trading history."""

import hashlib
import json
import uuid

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event
from apps.products.models import Product, ProductManufacturer, ProductPackagePrice
from apps.products.lifecycle import product_history as _history

from .models import Manufacturer


REVIEW_SIGNER = signing.Signer(salt="manufacturer-deletion-review")


def review_manufacturer(manufacturer):
    products = Product.objects.filter(
        Q(manufacturer=manufacturer) | Q(package_prices__manufacturer=manufacturer)
        | Q(allocations__manufacturer=manufacturer),
        archived_at__isnull=True,
    ).distinct().select_related("manufacturer").prefetch_related("package_prices__manufacturer")
    rows = []
    snapshot = {
        "manufacturer": [str(manufacturer.pk), str(manufacturer.updated_at)],
        "products": [],
        "orders": list(manufacturer.orders.order_by("pk").values_list("pk", "updated_at")),
        "locations": list(manufacturer.locations.order_by("pk").values_list("pk", "updated_at")),
        "all_product_links": list(manufacturer.products.order_by("pk").values_list("pk", "updated_at")),
        "all_prices": list(manufacturer.package_prices.order_by("pk").values_list("pk", "updated_at")),
        "all_allocations": list(manufacturer.product_allocations.order_by("pk").values_list("pk", "product_id")),
    }
    for product in products:
        packages = list(product.package_prices.all())
        history = _history(product)
        rows.append({
            "product": product,
            "packages": [row for row in packages if row.manufacturer_id == manufacturer.pk],
            "other_suppliers": sorted({row.manufacturer.name for row in packages if row.manufacturer_id != manufacturer.pk}),
            "history": history,
        })
        snapshot["products"].append({
            "id": str(product.pk), "updated_at": str(product.updated_at),
            "manufacturer": str(product.manufacturer_id), "active": product.active,
            "history": history,
            "packages": sorted(
                (str(p.pk), str(p.manufacturer_id), str(p.amount), str(p.price), str(p.updated_at))
                for p in packages
            ),
        })
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "products": rows,
        "orders": manufacturer.orders.all(),
        "locations": manufacturer.locations.all(),
        "token": REVIEW_SIGNER.sign(digest),
    }


def _product_payload(product):
    return {
        "name": product.name, "sku": product.sku,
        "manufacturer_id": str(product.manufacturer_id) if product.manufacturer_id else None,
        "active": product.active,
        "archived_at": str(product.archived_at) if product.archived_at else None,
    }


@transaction.atomic
def delete_manufacturer(*, actor, manufacturer_id, product_actions=None, review_token=None):
    require_owner(actor)
    manufacturer = Manufacturer.objects.select_for_update().filter(
        pk=manufacturer_id, archived_at__isnull=True,
    ).first()
    if manufacturer is None:
        raise ValidationError("Manufacturer was not found or has already been removed.")

    product_ids = Product.objects.filter(
        Q(manufacturer=manufacturer) | Q(package_prices__manufacturer=manufacturer)
        | Q(allocations__manufacturer=manufacturer),
        archived_at__isnull=True,
    ).values_list("pk", flat=True)
    list(Product.objects.filter(pk__in=product_ids).order_by("pk").select_for_update())
    review = review_manufacturer(manufacturer)
    try:
        submitted_digest = REVIEW_SIGNER.unsign(review_token or "")
    except signing.BadSignature as exc:
        raise ValidationError("Review the linked records before confirming deletion.") from exc
    if submitted_digest != REVIEW_SIGNER.unsign(review["token"]):
        raise ValidationError("The linked records changed. Review the updated list and confirm again.")

    actions = product_actions or {}
    expected_ids = {str(row["product"].pk) for row in review["products"]}
    if set(actions) != expected_ids:
        raise ValidationError("Choose an action for every linked product and review the current list.")

    # Validate every decision before making changes; the whole operation is atomic.
    targets = {}
    for product_id, decision in actions.items():
        if not isinstance(decision, dict) or decision.get("action") not in {"reassign", "hold", "delete"}:
            raise ValidationError("Choose Reassign, Put on hold, or Delete for every product.")
        if decision["action"] == "reassign":
            try:
                target_id = uuid.UUID(str(decision.get("target_id", "")))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValidationError("Choose an active replacement manufacturer.") from exc
            target = Manufacturer.objects.select_for_update().filter(
                pk=target_id, active=True, archived_at__isnull=True,
            ).exclude(pk=manufacturer.pk).first()
            if target is None:
                raise ValidationError("Choose an active replacement manufacturer other than the one being deleted.")
            targets[product_id] = target

    results = []
    for row in review["products"]:
        product = Product.objects.select_for_update().get(pk=row["product"].pk)
        product_id = str(product.pk)
        action = actions[product_id]["action"]
        before = _product_payload(product)
        old_prices = list(ProductPackagePrice.objects.select_for_update().filter(
            product=product, manufacturer=manufacturer,
        ))
        price_changes = []

        if action == "reassign":
            target = targets[product_id]
            for price in old_prices:
                existing = ProductPackagePrice.objects.select_for_update().filter(
                    product=product, manufacturer=target, amount=price.amount,
                ).first()
                price_changes.append({
                    "amount": str(price.amount), "old_price": str(price.price),
                    "new_price": str(existing.price if existing else price.price),
                    "kept_existing": existing is not None,
                })
                if existing:
                    price.delete()
                else:
                    price.manufacturer = target
                    price.updated_by = actor
                    price.save(update_fields=["manufacturer", "updated_by", "updated_at"])
            ProductManufacturer.objects.get_or_create(
                product=product, manufacturer=target,
                defaults={"created_by": actor, "updated_by": actor},
            )
            product.manufacturer = target
            audit_action = "product.manufacturer_reassigned"
        elif action == "delete" and row["history"]:
            # Keep the archived catalog record intact for historical references.
            product.active = False
            product.archived_at = timezone.now()
            audit_action = "product.archived"
        else:
            ProductPackagePrice.objects.filter(pk__in=[p.pk for p in old_prices]).delete()
            price_changes = [{"amount": str(p.amount), "removed_price": str(p.price)} for p in old_prices]
            if product.manufacturer_id == manufacturer.pk:
                product.manufacturer = None
            product.active = False
            if action == "delete":
                audit_action = "product.deleted"
            else:
                audit_action = "product.put_on_hold"

        ProductManufacturer.objects.filter(product=product, manufacturer=manufacturer).delete()
        product.updated_by = actor
        outcome = {
            "product_id": product_id, "name": product.name, "action": action,
            "archived": product.archived_at is not None,
        }
        record_audit_event(
            user=actor, action=audit_action, instance=product,
            before_data={**before, "supplier_package_prices": price_changes},
            after_data={**_product_payload(product), "replacement": str(targets[product_id].pk) if action == "reassign" else None},
        )
        if audit_action == "product.deleted":
            # Ingredient links belong to the catalog, not trading history.
            product.ingredients.all().delete()
            product.delete()
        else:
            product.save(update_fields=["manufacturer", "active", "archived_at", "updated_by", "updated_at"])
        results.append(outcome)

    before = {"name": manufacturer.name, "email": manufacturer.email, "phone": manufacturer.phone, "active": manufacturer.active}
    # The savepoint also protects against unexpected new PROTECT relationships.
    try:
        with transaction.atomic():
            manufacturer.delete()
    except ProtectedError:
        manufacturer.active = False
        manufacturer.archived_at = timezone.now()
        manufacturer.updated_by = actor
        manufacturer.save(update_fields=["active", "archived_at", "updated_by", "updated_at"])
        archived = True
    else:
        archived = False
        # Model.delete() clears the primary key; audit keeps its original identity.
        manufacturer.pk = manufacturer_id

    record_audit_event(
        user=actor, action="manufacturer.archived" if archived else "manufacturer.deleted",
        instance=manufacturer, before_data=before,
        after_data={"archived": archived, "products": results},
    )
    return {"archived": archived, "products": results}
