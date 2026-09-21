from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.distributor_warehouse.models import DistributorLocation
from apps.distributor_warehouse.services import create_inventory, create_location
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.products.services import create_product

from .models import DistributorStockBalance, DistributorStockMovement
from .services import (
    get_or_create_unallocated_location,
    reallocate_batch,
    receive_stock,
    unallocated_batches,
)


class DistributorInventoryLedgerTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@distinventory.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@distinventory.test",
            temporary_password="TemporaryPassword123!",
            name="Inventory Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()
        self.distributor_profile = DistributorProfile.objects.get(
            user=self.distributor_user
        )
        self.product = create_product(
            actor=self.owner,
            sku="DIST-INV-001",
            barcode="",
            name="Distributor Inventory Product",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )

    def test_receive_stock_lands_unallocated_by_default(self):
        movement = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("50"),
        )

        self.assertEqual(movement.movement_type, DistributorStockMovement.MovementType.RECEIVED)
        self.assertEqual(movement.to_location.location_type, "UNALLOCATED")

        batch = movement.batch
        self.assertIsNotNone(batch)
        self.assertEqual(batch.quantity_remaining, Decimal("50"))
        self.assertEqual(batch.received_date, date.today())

        balance = DistributorStockBalance.objects.get(
            distributor_profile=self.distributor_profile, product=self.product
        )
        self.assertEqual(balance.quantity, Decimal("50"))

    def test_allocate_moves_stock_from_global_pool_to_region_to_warehouse(self):
        region = create_inventory(
            actor=self.distributor_user, code="LHR", name="Lahore"
        )
        warehouse = create_location(
            actor=self.distributor_user,
            code="LHR-WH1",
            name="Lahore Warehouse 1",
            location_type=DistributorLocation.LocationType.WAREHOUSE,
            distributor_inventory=region,
        )

        movement = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("100"),
        )
        batch = movement.batch

        region_pool = get_or_create_unallocated_location(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            distributor_inventory=region,
        )

        region_batch = reallocate_batch(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            batch=batch,
            quantity=Decimal("100"),
            destination_location=region_pool,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal("0"))
        self.assertEqual(region_batch.quantity_remaining, Decimal("100"))

        reallocate_batch(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            batch=region_batch,
            quantity=Decimal("100"),
            destination_location=warehouse,
        )

        warehouse_balance = DistributorStockBalance.objects.get(
            distributor_profile=self.distributor_profile, location=warehouse
        )
        self.assertEqual(warehouse_balance.quantity, Decimal("100"))
        self.assertEqual(unallocated_batches(distributor_profile=self.distributor_profile).count(), 0)

    def test_cannot_allocate_from_a_non_unallocated_location(self):
        region = create_inventory(actor=self.distributor_user, code="KHI", name="Karachi")
        warehouse = create_location(
            actor=self.distributor_user,
            code="KHI-WH1",
            name="Karachi Warehouse",
            location_type=DistributorLocation.LocationType.WAREHOUSE,
            distributor_inventory=region,
        )
        movement = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("10"),
            to_location=warehouse,
        )

        with self.assertRaises(ValidationError):
            reallocate_batch(
                actor=self.distributor_user,
                distributor_profile=self.distributor_profile,
                batch=movement.batch,
                quantity=Decimal("5"),
                destination_location=warehouse,
            )

    def test_movement_is_immutable(self):
        movement = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("10"),
        )

        with self.assertRaises(ValidationError):
            movement.quantity = Decimal("999")
            movement.save()

        with self.assertRaises(ValidationError):
            movement.delete()

    def test_distributor_cannot_manage_another_distributors_stock(self):
        other_user = create_distributor(
            actor=self.owner,
            email="other@distinventory.test",
            temporary_password="TemporaryPassword123!",
            name="Other Inventory Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=other_user.pk)
        other_user.refresh_from_db()

        with self.assertRaises(ValidationError):
            receive_stock(
                actor=other_user,
                distributor_profile=self.distributor_profile,
                product=self.product,
                quantity=Decimal("5"),
            )

    def test_stock_pages_are_connected(self):
        receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("10"),
        )

        client = Client()
        client.force_login(self.distributor_user)

        for route in [
            "/distributor/inventory/balances/",
            "/distributor/inventory/batches/",
            "/distributor/inventory/movements/",
            "/distributor/inventory/receive/",
        ]:
            with self.subTest(route=route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_owner_cannot_access_distributor_stock_pages(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/distributor/inventory/balances/")
        self.assertEqual(response.status_code, 403)
