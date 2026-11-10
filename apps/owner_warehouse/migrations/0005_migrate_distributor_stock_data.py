# Distributor panel archived to D:/Boriel-Vita/Boreal Beta: this was a
# one-time move of old Distributor balances into the Distributor's own
# warehouse tables (already applied everywhere). Kept, empty, so later
# migrations still find it.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('owner_warehouse', '0004_alter_location_location_type_inventory_and_more'),
        ('owner_inventory', '0004_stockbatch_batch_number'),
    ]

    operations = []
