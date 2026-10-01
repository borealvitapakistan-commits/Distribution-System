from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    """Orders placed before the Request to Quote step went straight to
    the Manufacturer as Purchase Orders: their asked-for price is the
    price on the order, and they became a PO when created."""
    ManufacturerOrder = apps.get_model("manufacturers", "ManufacturerOrder")
    ManufacturerOrderItem = apps.get_model("manufacturers", "ManufacturerOrderItem")

    ManufacturerOrderItem.objects.filter(requested_unit_price__isnull=True).update(
        requested_unit_price=F("unit_price")
    )
    ManufacturerOrder.objects.filter(po_sent_at__isnull=True).exclude(
        status="QUOTE"
    ).update(po_sent_at=F("created_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("manufacturers", "0008_request_to_quote_and_bottle_sizes"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
