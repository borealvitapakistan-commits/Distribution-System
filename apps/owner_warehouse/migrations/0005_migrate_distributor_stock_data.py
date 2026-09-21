from datetime import date

from django.db import migrations


def migrate_distributor_stock(apps, schema_editor):
    """One-time backfill: the old design tracked a Distributor's stock as
    a flat balance on an Owner-owned Location row. Now that Distributors
    have their own, fully separate warehouse system, port whatever
    balance exists there into that Distributor's own unallocated pool —
    same as any other stock they've received, waiting to be allocated."""
    Location = apps.get_model('owner_warehouse', 'Location')
    StockBalance = apps.get_model('owner_inventory', 'StockBalance')
    DistributorLocation = apps.get_model('distributor_warehouse', 'DistributorLocation')
    DistributorStockBatch = apps.get_model('distributor_inventory', 'DistributorStockBatch')
    DistributorStockMovement = apps.get_model('distributor_inventory', 'DistributorStockMovement')
    DistributorStockBalance = apps.get_model('distributor_inventory', 'DistributorStockBalance')

    for location in Location.objects.filter(location_type='DISTRIBUTOR'):
        distributor_profile = location.distributor_profile

        if distributor_profile is None:
            continue

        balances = StockBalance.objects.filter(location=location, quantity__gt=0)

        if not balances.exists():
            continue

        unallocated, _ = DistributorLocation.objects.get_or_create(
            distributor_profile=distributor_profile,
            location_type='UNALLOCATED',
            distributor_inventory=None,
            defaults={'code': 'UNALLOCATED', 'name': 'Unallocated'},
        )

        for balance in balances:
            batch = DistributorStockBatch.objects.create(
                distributor_profile=distributor_profile,
                product=balance.product,
                location=unallocated,
                batch_number='',
                received_date=date.today(),
                expiry_date=None,
                quantity_received=balance.quantity,
                quantity_remaining=balance.quantity,
                reference='Migrated from legacy distributor balance',
            )
            DistributorStockMovement.objects.create(
                distributor_profile=distributor_profile,
                product=balance.product,
                from_location=None,
                to_location=unallocated,
                quantity=balance.quantity,
                movement_type='RECEIVED',
                reference='Migrated from legacy distributor balance',
                batch=batch,
            )
            new_balance, created = DistributorStockBalance.objects.get_or_create(
                distributor_profile=distributor_profile,
                product=balance.product,
                location=unallocated,
                defaults={'quantity': balance.quantity},
            )
            if not created:
                new_balance.quantity += balance.quantity
                new_balance.save()

            # Ported — zero the old cache row so it stops showing (and
            # double-counting) on the Owner's own balance list. The
            # StockMovement history stays untouched, for the record.
            balance.quantity = 0
            balance.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('owner_warehouse', '0004_alter_location_location_type_inventory_and_more'),
        ('owner_inventory', '0004_stockbatch_batch_number'),
        ('distributor_warehouse', '0001_initial'),
        ('distributor_inventory', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(migrate_distributor_stock, noop_reverse),
    ]
