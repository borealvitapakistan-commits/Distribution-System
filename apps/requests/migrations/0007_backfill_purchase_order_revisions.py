from django.db import migrations
from django.utils.text import slugify


def _quantity_ordered(item):
    return item.quantity_shipped if item.unavailable else item.quantity_requested


def backfill(apps, schema_editor):
    """Orders from before the Request to Quote existed were placed
    straight as Purchase Orders. Each gets that step in its conversation,
    dated when it was placed.

    invoiced_at used to be stamped on the first shipment. It now means
    the Owner's invoice was issued, which happens once everything has
    shipped: orders already fully shipped keep it and get their invoice
    number and step; orders still part-way through have it cleared, so
    the Owner issues the invoice when the rest ships."""
    PurchaseOrder = apps.get_model("requests", "PurchaseOrder")
    Revision = apps.get_model("requests", "PurchaseOrderRevision")
    RevisionLine = apps.get_model("requests", "PurchaseOrderRevisionLine")
    Brand = apps.get_model("core", "Brand")

    brand = Brand.objects.filter(active=True).first()
    code = slugify(brand.name).upper() if brand else ""
    prefix = f"SI-{code}-" if code else "SI-"
    used = [
        n[len(prefix):]
        for n in PurchaseOrder.objects.filter(invoice_number__startswith=prefix)
        .values_list("invoice_number", flat=True)
    ]
    next_number = max([int(n) for n in used if n.isdigit()], default=0) + 1

    orders = (
        PurchaseOrder.objects.filter(revisions__isnull=True)
        .prefetch_related("items")
        .order_by("created_at")
    )

    for order in orders:
        items = list(order.items.all())
        steps = [("PURCHASE_ORDER", order.created_at, False)]

        if order.invoiced_at:
            fully_shipped = items and all(
                item.quantity_shipped >= _quantity_ordered(item) for item in items
            )
            if fully_shipped or order.status in ("SHIPPED", "RECEIVED", "DECLINED"):
                order.invoice_number = f"{prefix}{next_number:04d}"
                next_number += 1
                steps.append(("INVOICE", order.invoiced_at, True))
            else:
                order.invoiced_at = None

        for number, (stage, at, by_owner) in enumerate(steps, start=1):
            revision = Revision.objects.create(
                purchase_order=order,
                number=number,
                stage=stage,
                by_owner=by_owner,
                message=f"Invoice {order.invoice_number}" if stage == "INVOICE" else "",
                created_by_id=order.created_by_id,
                updated_by_id=order.created_by_id,
            )
            Revision.objects.filter(pk=revision.pk).update(created_at=at, updated_at=at)
            RevisionLine.objects.bulk_create(
                [
                    RevisionLine(
                        revision=revision,
                        item=item,
                        product_id=item.product_id,
                        quantity=_quantity_ordered(item),
                        unit_price=item.unit_price,
                        removed=stage == "INVOICE" and _quantity_ordered(item) <= 0,
                        created_by_id=order.created_by_id,
                        updated_by_id=order.created_by_id,
                    )
                    for item in items
                ]
            )

        order.po_placed_at = order.po_placed_at or order.created_at
        order.owner_seen_revision = len(steps)
        order.distributor_seen_revision = len(steps)
        order.save(
            update_fields=[
                "invoice_number", "invoiced_at", "po_placed_at",
                "owner_seen_revision", "distributor_seen_revision",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("requests", "0006_quote_stages_and_revisions"),
        ("core", "0003_brand_primary_color"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
