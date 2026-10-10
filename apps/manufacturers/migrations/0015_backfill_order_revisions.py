from django.db import migrations


def backfill(apps, schema_editor):
    """Orders from before the conversation trail existed get the steps
    we can still reconstruct: the Request to Quote at the asked-for
    prices, the Manufacturer's quote, the Purchase Order and the invoice
    — each with the order's current lines, dated when it happened. The
    rounds in between were overwritten back then and can't be recovered."""
    ManufacturerOrder = apps.get_model("manufacturers", "ManufacturerOrder")
    OrderRevision = apps.get_model("manufacturers", "OrderRevision")
    OrderRevisionLine = apps.get_model("manufacturers", "OrderRevisionLine")

    orders = ManufacturerOrder.objects.filter(revisions__isnull=True).prefetch_related("items")

    for order in orders:
        items = list(order.items.all())
        steps = []

        if order.status == "QUOTE" or order.quoted_at:
            steps.append(("REQUEST", order.created_at, None, "requested"))
            if order.quoted_at:
                steps.append(("QUOTE", order.quoted_at, order.quote_file, "current"))
        if order.status != "QUOTE":
            steps.append(("PURCHASE_ORDER", order.po_sent_at or order.created_at, None, "current"))
        if order.invoice_approved_at:
            steps.append(("INVOICE", order.invoice_approved_at, order.invoice_file, "current"))

        for number, (stage, at, attachment, prices) in enumerate(steps, start=1):
            revision = OrderRevision.objects.create(
                order=order,
                number=number,
                stage=stage,
                attachment=attachment.name if attachment else None,
                created_by_id=order.created_by_id,
                updated_by_id=order.created_by_id,
            )
            OrderRevision.objects.filter(pk=revision.pk).update(created_at=at, updated_at=at)
            OrderRevisionLine.objects.bulk_create(
                [
                    OrderRevisionLine(
                        revision=revision,
                        item=item,
                        product_id=item.product_id,
                        bottle_size=item.bottle_size,
                        quantity=item.quantity,
                        unit_price=(
                            item.requested_unit_price if prices == "requested" else item.unit_price
                        ),
                        created_by_id=order.created_by_id,
                        updated_by_id=order.created_by_id,
                    )
                    for item in items
                ]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("manufacturers", "0014_order_revisions"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
