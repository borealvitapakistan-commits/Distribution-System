from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.distributor_inventory.models import (
    DistributorStockBalance,
    DistributorStockBatch,
)
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.owner_warehouse.models import Location
from apps.owner_warehouse.services import create_inventory, create_location
from apps.products.services import create_product, export_products_csv

from .models import StockBalance, StockBatch, StockMovement
from .services import (
    available_batches_fefo,
    give_to_distributor,
    receive_stock,
    ship_stock_to_transit,
    shippable_batches_fefo,
)


class InventoryLedgerTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@inventory.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="INV-001",
            barcode="",
            name="Inventory Product",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.inventory = create_inventory(
            actor=self.owner, code="INV-1", name="Test Region"
        )
        self.warehouse = create_location(
            actor=self.owner,
            code="WH-1",
            name="Main Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@inventory.test",
            temporary_password="TemporaryPassword123!",
            name="Inventory Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_user.pk)
        self.distributor_profile = DistributorProfile.objects.get(user=distributor_user)

    def test_receive_stock_creates_movement_balance_and_batch(self):
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
        )

        balance = StockBalance.objects.get(
            product=self.product,
            location=self.warehouse,
        )
        self.assertEqual(balance.quantity, Decimal("50"))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.RECEIVED)
        self.assertIsNone(movement.from_location)
        self.assertEqual(movement.to_location, self.warehouse)

        batch = movement.batch
        self.assertIsNotNone(batch)
        self.assertEqual(batch.quantity_received, Decimal("50"))
        self.assertEqual(batch.quantity_remaining, Decimal("50"))
        self.assertEqual(batch.received_date, date.today())
        self.assertIsNone(batch.expiry_date)

    def test_receive_stock_accepts_explicit_dates(self):
        received = date(2026, 3, 10)
        expiry = date(2027, 3, 10)

        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
            received_date=received,
            expiry_date=expiry,
        )

        self.assertEqual(movement.batch.received_date, received)
        self.assertEqual(movement.batch.expiry_date, expiry)

    def test_receive_stock_accepts_batch_number(self):
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
            batch_number="BV-GL-AML-001",
        )

        self.assertEqual(movement.batch.batch_number, "BV-GL-AML-001")

    def test_each_add_inventory_is_its_own_batch_but_totals_sum(self):
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("5"),
            to_location=self.warehouse,
            received_date=date(2026, 3, 10),
        )
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
            received_date=date(2026, 3, 13),
        )

        self.assertEqual(StockBatch.objects.count(), 2)

        balance = StockBalance.objects.get(
            product=self.product, location=self.warehouse
        )
        self.assertEqual(balance.quantity, Decimal("15"))

    def test_batches_are_fefo_ordered_soonest_expiry_first(self):
        far_batch_movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
            expiry_date=date.today() + timedelta(days=90),
        )
        near_batch_movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
            expiry_date=date.today() + timedelta(days=10),
        )
        no_expiry_movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
        )

        ordered = list(available_batches_fefo(product=self.product))
        self.assertEqual(
            [b.pk for b in ordered],
            [
                near_batch_movement.batch.pk,
                far_batch_movement.batch.pk,
                no_expiry_movement.batch.pk,
            ],
        )

    def test_give_to_distributor_draws_down_the_chosen_batch(self):
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
        )
        batch = movement.batch

        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("20"),
            from_location=self.warehouse,
            distributor_profile=self.distributor_profile,
            batch=batch,
        )

        source_balance = StockBalance.objects.get(
            product=self.product,
            location=self.warehouse,
        )
        self.assertEqual(source_balance.quantity, Decimal("30"))

        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal("30"))

        # The gift lands in the Distributor's own, separately tracked
        # unallocated pool — not on any Owner-owned Location.
        distributor_balance = DistributorStockBalance.objects.get(
            distributor_profile=self.distributor_profile,
            product=self.product,
        )
        self.assertEqual(distributor_balance.quantity, Decimal("20"))
        self.assertEqual(distributor_balance.location.location_type, "UNALLOCATED")

    def test_batch_is_required_to_give_to_distributor(self):
        with self.assertRaises(ValidationError):
            give_to_distributor(
                actor=self.owner,
                product=self.product,
                quantity=Decimal("5"),
                from_location=self.warehouse,
                distributor_profile=self.distributor_profile,
                batch=None,
            )

    def test_cannot_give_more_than_batch_remaining(self):
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
        )

        with self.assertRaises(ValidationError):
            give_to_distributor(
                actor=self.owner,
                product=self.product,
                quantity=Decimal("20"),
                from_location=self.warehouse,
                distributor_profile=self.distributor_profile,
                batch=movement.batch,
            )

    def test_non_sellable_warehouse_cannot_ship(self):
        held = create_location(
            actor=self.owner,
            code="WH-HELD",
            name="Held Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
            is_sellable=False,
        )
        held_batch = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=held,
        ).batch
        open_batch = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
        ).batch

        self.assertEqual(
            list(shippable_batches_fefo(product=self.product)), [open_batch]
        )

        with self.assertRaises(ValidationError):
            give_to_distributor(
                actor=self.owner,
                product=self.product,
                quantity=Decimal("5"),
                from_location=held,
                distributor_profile=self.distributor_profile,
                batch=held_batch,
            )

        with self.assertRaises(ValidationError):
            ship_stock_to_transit(
                actor=self.owner,
                product=self.product,
                quantity=Decimal("5"),
                from_location=held,
                batch=held_batch,
            )

        held_batch.refresh_from_db()
        self.assertEqual(held_batch.quantity_remaining, Decimal("10"))

    def test_movement_is_immutable(self):
        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
        )

        with self.assertRaises(ValidationError):
            movement.quantity = Decimal("999")
            movement.save()

        with self.assertRaises(ValidationError):
            movement.delete()

    def test_distributor_has_no_access_to_owner_stock_balances(self):
        """The Owner's own inventory ledger is Owner-only now — a
        Distributor's stock lives entirely in apps.distributor_inventory,
        a separate set of tables with no FK relationship to these."""
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
        )

        distributor_user = self.distributor_profile.user
        self.assertEqual(StockBalance.objects.for_user(distributor_user).count(), 0)

        owner_visible = StockBalance.objects.for_user(self.owner)
        self.assertEqual(owner_visible.count(), 1)

    def test_distributor_cannot_see_stock_batches(self):
        distributor_user = self.distributor_profile.user
        self.assertEqual(StockBatch.objects.for_user(distributor_user).count(), 0)


class ShopifyCSVExportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@csv.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="CSV-001",
            barcode="",
            name="CSV Product",
            base_retail_price=Decimal("250.00"),
            currency="pkr",
        )
        self.shopify_location = create_location(
            actor=self.owner,
            code="SHOP-1",
            name="Shopify Stock",
            location_type=Location.LocationType.SHOPIFY,
            is_sellable=True,
        )

    def test_export_includes_header_and_computed_quantity(self):
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("7"),
            to_location=self.shopify_location,
        )

        csv_text = export_products_csv(self.product.__class__.objects.all())
        lines = csv_text.strip().splitlines()

        self.assertIn("Handle", lines[0])
        self.assertIn("Variant Inventory Qty", lines[0])

        data_line = lines[1]
        self.assertIn("CSV-001", data_line)
        self.assertIn("7", data_line)


class InventoryViewWiringTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@views.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="VIEW-001",
            barcode="",
            name="View Product",
            base_retail_price=Decimal("50.00"),
            currency="pkr",
        )
        self.inventory = create_inventory(
            actor=self.owner, code="INV-2", name="View Region"
        )
        self.warehouse = create_location(
            actor=self.owner,
            code="VIEW-WH",
            name="View Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@views.test",
            temporary_password="TemporaryPassword123!",
            name="View Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_user.pk)
        self.distributor_user = distributor_user
        self.distributor_profile = DistributorProfile.objects.get(user=distributor_user)

        movement = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("40"),
            to_location=self.warehouse,
        )
        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("12"),
            from_location=self.warehouse,
            distributor_profile=self.distributor_profile,
            batch=movement.batch,
        )

    def test_owner_stock_balance_list_shows_warehouse_balance(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/inventory/balances/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Product")
        self.assertContains(response, "View Warehouse")

    def test_stock_movement_list_shows_every_transaction(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/records/movements/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Product")
        self.assertContains(response, "Received")
        self.assertContains(response, "Transfer")

    def test_distributor_cannot_access_stock_movement_list(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/owner/records/movements/")
        self.assertEqual(response.status_code, 403)

    def test_csv_export_downloads_shopify_formatted_file(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/products/export/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(b"VIEW-001", response.content)

    def test_gift_lands_in_the_distributors_own_stock_system(self):
        """A "Give to Distributor" gift never touches the Owner's own
        warehouse pages, and shows up on the Distributor's own (separate)
        stock balance page instead."""
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/distributor/inventory/balances/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "12")
        self.assertNotContains(response, "View Warehouse")

    def test_owner_can_see_batch_list(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/inventory/batches/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Product")

    def test_distributor_cannot_access_batch_list(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/owner/inventory/batches/")
        self.assertEqual(response.status_code, 403)

    def test_give_to_distributor_form_lists_batches_not_warehouses(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/inventory/give/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="from_location"')
        self.assertContains(response, 'name="batch"')
