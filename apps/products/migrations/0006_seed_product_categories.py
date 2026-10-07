from django.db import migrations

CATEGORY_NAMES = [
    "Capsule",
    "Tablet",
    "Soft Gel",
    "Liquid",
    "Syrup",
    "Oil",
    "Essential Oil",
    "Carrier Oil",
    "Powder",
    "Herbal Tea",
]


def seed_categories(apps, schema_editor):
    ProductCategory = apps.get_model("products", "ProductCategory")

    for order, name in enumerate(CATEGORY_NAMES, start=1):
        category = ProductCategory.objects.filter(
            parent__isnull=True, name__iexact=name
        ).first()

        if category is None:
            ProductCategory.objects.create(name=name, sort_order=order)


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0005_product_size"),
    ]

    operations = [
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
    ]
