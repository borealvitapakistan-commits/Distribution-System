from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.products.services import create_product, export_products_csv
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.warehouse.models import Location
from apps.warehouse.services import create_location

from .models import StockBalance, StockMovement
from .services import give_to_distributor, receive_stock


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
        self.warehouse = create_location(
            actor=self.owner,
            code="WH-1",
            name="Main Warehouse",
            location_type=Location.LocationType.OWN,
        )
        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@inventory.test",
            temporary_password="TemporaryPassword123!",
            name="Inventory Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_user.pk)
        self.distributor_profile = DistributorProfile.objects.get(user=distributor_user)

    def test_receive_stock_creates_movement_and_balance(self):
        receive_stock(
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
        movement = StockMovement.objects.get()
        self.assertEqual(movement.movement_type, StockMovement.MovementType.RECEIVED)
        self.assertIsNone(movement.from_location)
        self.assertEqual(movement.to_location, self.warehouse)

    def test_give_to_distributor_moves_stock_to_their_own_location(self):
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
        )

        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("20"),
            from_location=self.warehouse,
            distributor_profile=self.distributor_profile,
        )

        source_balance = StockBalance.objects.get(
            product=self.product,
            location=self.warehouse,
        )
        self.assertEqual(source_balance.quantity, Decimal("30"))

        distributor_location = Location.objects.get(
            location_type=Location.LocationType.DISTRIBUTOR,
            distributor_profile=self.distributor_profile,
        )
        distributor_balance = StockBalance.objects.get(
            product=self.product,
            location=distributor_location,
        )
        self.assertEqual(distributor_balance.quantity, Decimal("20"))

    def test_cannot_give_more_than_available(self):
        receive_stock(
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
            )

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

    def test_distributor_balance_scoping_hides_warehouse_choice(self):
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
        )
        give_to_distributor(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("15"),
            from_location=self.warehouse,
            distributor_profile=self.distributor_profile,
        )

        distributor_user = self.distributor_profile.user
        distributor_visible = StockBalance.objects.for_user(distributor_user)

        self.assertEqual(distributor_visible.count(), 1)
        self.assertEqual(distributor_visible.first().quantity, Decimal("15"))

        owner_visible = StockBalance.objects.for_user(self.owner)
        self.assertEqual(owner_visible.count(), 2)


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
        self.warehouse = create_location(
            actor=self.owner,
            code="VIEW-WH",
            name="View Warehouse",
            location_type=Location.LocationType.OWN,
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

        receive_stock(
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
        )

    def test_owner_inventory_list_shows_warehouse_balance(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/inventory/")
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

    def test_distributor_stock_view_hides_warehouse_name(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/distributor/inventory/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "12")
        self.assertNotContains(response, "View Warehouse")
