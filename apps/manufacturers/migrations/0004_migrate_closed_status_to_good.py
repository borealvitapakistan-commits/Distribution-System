from django.db import migrations


def closed_to_good(apps, schema_editor):
    ManufacturerOrder = apps.get_model("manufacturers", "ManufacturerOrder")
    ManufacturerOrder.objects.filter(status="CLOSED").update(status="GOOD")


def good_to_closed(apps, schema_editor):
    ManufacturerOrder = apps.get_model("manufacturers", "ManufacturerOrder")
    ManufacturerOrder.objects.filter(status="GOOD").update(status="CLOSED")


class Migration(migrations.Migration):
    dependencies = [
        ("manufacturers", "0003_manufacturer_upfront_payment_percentage_and_more"),
    ]

    operations = [
        migrations.RunPython(closed_to_good, good_to_closed),
    ]
