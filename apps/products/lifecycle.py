import hashlib
import json

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import Product


def product_history(product):
    history = []
    for relation in product._meta.related_objects:
        model = relation.related_model
        if model._meta.app_label == "products":
            continue
        count = model._base_manager.filter(**{relation.field.name: product.pk}).count()
        if count:
            label = model._meta.verbose_name if count == 1 else model._meta.verbose_name_plural
            history.append({"label": str(label), "count": count})
    return history


def deletion_review(products):
    rows = [{"product": product, "history": product_history(product)} for product in sorted(products, key=lambda p: str(p.pk))]
    snapshot = [
        {"id": str(row["product"].pk), "updated": str(row["product"].updated_at),
         "history": row["history"],
         "packages": list(row["product"].package_prices.order_by("pk").values_list("pk", "updated_at"))}
        for row in rows
    ]
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    return {"rows": rows, "token": signing.Signer(salt="product-delete").sign(digest)}


@transaction.atomic
def apply_product_action(*, actor, product_ids, action, token=None):
    require_owner(actor)
    if action not in {"active", "hold", "delete"}:
        raise ValidationError("Choose a valid product action.")
    try:
        products = list(Product.objects.for_user(actor).filter(pk__in=product_ids).order_by("pk").select_for_update())
    except (ValueError, ValidationError) as exc:
        raise ValidationError("Choose valid products.") from exc
    if not products or {str(p.pk) for p in products} != {str(pk) for pk in product_ids}:
        raise ValidationError("Some selected products are no longer available. Refresh the list.")
    review = deletion_review(products)
    if action == "delete":
        signer = signing.Signer(salt="product-delete")
        try:
            valid = signer.unsign(token or "") == signer.unsign(review["token"])
        except signing.BadSignature:
            valid = False
        if not valid:
            raise ValidationError("The product records changed. Review the updated products and confirm again.")
    result = {"deleted": 0, "archived": 0, "updated": 0}
    for row in review["rows"]:
        product = row["product"]
        before = {"sku": product.sku, "name": product.name, "active": product.active}
        if action == "delete":
            archive = bool(row["history"])
            event = "product.archived" if archive else "product.deleted"
            result["archived" if archive else "deleted"] += 1
        else:
            event = "product.activated" if action == "active" else "product.put_on_hold"
            result["updated"] += 1
        record_audit_event(user=actor, action=event, instance=product, before_data=before)
        if event == "product.deleted":
            product.ingredients.all().delete()
            product.delete()
        else:
            product.active = action == "active"
            if action == "delete":
                product.archived_at = timezone.now()
            product.updated_by = actor
            product.save(update_fields=["active", "archived_at", "updated_by", "updated_at"])
    return result
