# The Distributor panel moved to D:/Boriel-Vita/Boreal Beta. Drops the
# columns that named the Distributor a stock movement went to and the
# Distributor purchase order line a Transit lot was shipped on (the links
# are saved there in data/owner_side_links.json). The movements stay: the
# stock really did leave the warehouses.

from django.db import migrations


def drop_distributor_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return  # A fresh database never had these columns.

    schema_editor.execute(
        "ALTER TABLE owner_inventory_stockmovement "
        "DROP COLUMN IF EXISTS distributor_profile_id"
    )
    schema_editor.execute(
        "ALTER TABLE owner_inventory_stockbatch "
        "DROP COLUMN IF EXISTS shipped_for_id"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('owner_inventory', '0009_shopifyorderline_original_unit_price_and_more'),
    ]

    operations = [
        migrations.RunPython(drop_distributor_columns, migrations.RunPython.noop),
    ]
