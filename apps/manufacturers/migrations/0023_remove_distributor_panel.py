# The Distributor panel moved to D:/Boriel-Vita/Boreal Beta. Drops the
# column that linked a Manufacturer order line to the Distributor purchase
# order line it restocked (the links are saved there in
# data/owner_side_links.json).

from django.db import migrations


def drop_distributor_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return  # A fresh database never had this column.

    schema_editor.execute(
        "ALTER TABLE manufacturers_manufacturerorderitem "
        "DROP COLUMN IF EXISTS source_purchase_order_item_id"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('manufacturers', '0022_order_workflow_and_stamped_po'),
    ]

    operations = [
        migrations.RunPython(drop_distributor_columns, migrations.RunPython.noop),
    ]
