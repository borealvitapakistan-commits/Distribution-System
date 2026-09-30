import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from apps.accounts.models import User
from apps.distributor_warehouse.models import DistributorLocation
from apps.distributor_warehouse.services import create_inventory, create_location
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.products.services import create_product

from .models import (
    DistributorStockBalance,
    DistributorStockBatch,
    DistributorStockMovement,
    SubDistributorSale,
)
from .services import (
    get_or_create_unallocated_location,
    reallocate_batch,
    receive_stock,
    sell_to_sub_distributor,
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
            "/distributor/sales/",
            "/distributor/sales/new/",
        ]:
            with self.subTest(route=route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_owner_cannot_access_distributor_stock_pages(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/distributor/inventory/balances/")
        self.assertEqual(response.status_code, 403)


class SubDistributorSaleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@subsale.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@subsale.test",
            temporary_password="TemporaryPassword123!",
            name="Sale Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()
        self.distributor_profile = DistributorProfile.objects.get(
            user=self.distributor_user
        )
        self.product = create_product(
            actor=self.owner,
            sku="SUB-SALE-001",
            barcode="",
            name="Sale Product",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.region = create_inventory(
            actor=self.distributor_user, code="LHR", name="Lahore"
        )
        self.warehouse = create_location(
            actor=self.distributor_user,
            code="LHR-WH1",
            name="Lahore Warehouse 1",
            location_type=DistributorLocation.LocationType.WAREHOUSE,
            distributor_inventory=self.region,
        )
        self.late_batch = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
            batch_number="LATE",
            expiry_date=date(2031, 1, 1),
        ).batch
        self.early_batch = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
            batch_number="EARLY",
            expiry_date=date(2030, 1, 1),
        ).batch

    def _sell(self, **overrides):
        kwargs = {
            "actor": self.distributor_user,
            "distributor_profile": self.distributor_profile,
            "sub_distributor_name": "Ali Traders",
            "batch": self.late_batch,
            "quantity": Decimal("4"),
        }
        kwargs.update(overrides)
        return sell_to_sub_distributor(**kwargs)

    def test_sale_draws_only_from_the_chosen_batch(self):
        sale = self._sell(sub_distributor_name="  Ali Traders ")

        self.assertEqual(sale.sub_distributor_name, "Ali Traders")
        self.assertEqual(sale.sale_date, date.today())
        self.assertEqual(sale.batch, self.late_batch)
        self.assertEqual(sale.product, self.product)
        self.assertEqual(sale.from_location, self.warehouse)

        remaining = dict(
            DistributorStockBatch.objects.values_list("batch_number", "quantity_remaining")
        )
        self.assertEqual(remaining, {"EARLY": Decimal("10"), "LATE": Decimal("6")})

        balance = DistributorStockBalance.objects.get(
            product=self.product, location=self.warehouse
        )
        self.assertEqual(balance.quantity, Decimal("16"))

        sold = DistributorStockMovement.objects.get(
            movement_type=DistributorStockMovement.MovementType.SOLD
        )
        self.assertEqual(sold.batch, self.late_batch)
        self.assertEqual(sold.quantity, Decimal("4"))
        self.assertEqual(sold.reference, "Sold to Ali Traders")

    def test_cannot_sell_more_than_the_batch_holds(self):
        # 20 in the warehouse overall, but only 10 in this batch.
        with self.assertRaises(ValidationError):
            self._sell(quantity=Decimal("11"))

        self.assertFalse(SubDistributorSale.objects.exists())
        self.late_batch.refresh_from_db()
        self.assertEqual(self.late_batch.quantity_remaining, Decimal("10"))

    def test_cannot_sell_from_unallocated_stock(self):
        pooled = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=self.product,
            quantity=Decimal("5"),
        ).batch

        with self.assertRaises(ValidationError):
            self._sell(batch=pooled, quantity=Decimal("1"))

    def test_sub_distributor_name_is_required(self):
        with self.assertRaises(ValidationError):
            self._sell(sub_distributor_name="   ", quantity=Decimal("1"))

    def _form_data(self, quantities, **overrides):
        """quantities: {batch: quantity} — every other batch is sent as 0,
        the way the page submits it."""
        data = {
            "sub_distributor_name": "Form Sub Dist",
            "product": self.product.pk,
        }
        for batch in DistributorStockBatch.objects.filter(distributor_profile=self.distributor_profile):
            data[f"batch_qty_{batch.pk}"] = str(quantities.get(batch, 0))
        data.update(overrides)
        return data

    def test_sell_form_lists_every_batch_soonest_to_expire_first(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/distributor/sales/new/")

        self.assertEqual(
            [batch.pk for batch, _field in response.context["form"].batch_rows()],
            [self.early_batch.pk, self.late_batch.pk],
        )
        self.assertContains(response, "EARLY")
        self.assertContains(response, "Lahore Warehouse 1")

    def test_one_batch_sale_opens_its_detail_page(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.post("/distributor/sales/new/", self._form_data({self.early_batch: 5}))

        sale = SubDistributorSale.objects.get()
        self.assertEqual(sale.batch, self.early_batch)
        self.assertRedirects(response, f"/distributor/sales/{sale.pk}/")

        response = client.get(f"/distributor/sales/{sale.pk}/")
        self.assertContains(response, "Form Sub Dist")
        self.assertContains(response, "EARLY")
        self.assertContains(response, "Lahore")

    def test_one_sale_split_across_batches_keeps_a_record_per_batch(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.post(
            "/distributor/sales/new/",
            self._form_data({self.early_batch: 10, self.late_batch: 3}),
        )
        self.assertRedirects(response, "/distributor/sales/")

        sold = dict(
            SubDistributorSale.objects.values_list("batch__batch_number", "quantity")
        )
        self.assertEqual(sold, {"EARLY": Decimal("10"), "LATE": Decimal("3")})

        remaining = dict(
            DistributorStockBatch.objects.values_list("batch_number", "quantity_remaining")
        )
        self.assertEqual(remaining, {"EARLY": Decimal("0"), "LATE": Decimal("7")})

    def test_sell_form_rejects_more_than_a_batch_holds(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.post(
            "/distributor/sales/new/",
            self._form_data({self.early_batch: 5, self.late_batch: 11}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"batch_qty_{self.late_batch.pk}", response.context["form"].errors)
        # All or nothing — the valid batch wasn't sold either.
        self.assertFalse(SubDistributorSale.objects.exists())

    def test_sell_form_needs_a_quantity_for_the_chosen_product(self):
        other_product = create_product(
            actor=self.owner,
            sku="SUB-SALE-002",
            barcode="",
            name="Other Product",
            base_retail_price=Decimal("50.00"),
            currency="pkr",
        )
        other_batch = receive_stock(
            actor=self.distributor_user,
            distributor_profile=self.distributor_profile,
            product=other_product,
            quantity=Decimal("5"),
            to_location=self.warehouse,
            batch_number="OTHER",
            expiry_date=date(2030, 6, 1),
        ).batch

        client = Client()
        client.force_login(self.distributor_user)

        # A quantity typed against another product's batch doesn't count.
        response = client.post("/distributor/sales/new/", self._form_data({other_batch: 2}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a quantity to sell from at least one batch.")
        self.assertFalse(SubDistributorSale.objects.exists())

    def test_distributor_only_sees_own_sales(self):
        sale = self._sell(sub_distributor_name="Private Buyer", quantity=Decimal("1"))
        other = create_distributor(
            actor=self.owner,
            email="other@subsale.test",
            temporary_password="TemporaryPassword123!",
            name="Other Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=other.pk)
        other.refresh_from_db()

        client = Client()
        client.force_login(other)
        response = client.get("/distributor/sales/")
        self.assertNotContains(response, "Private Buyer")
        response = client.get(f"/distributor/sales/{sale.pk}/")
        self.assertEqual(response.status_code, 404)


MEDIA_ROOT = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class SubDistributorPaymentProofTests(TestCase):
    """Proof the sub-distributor paid — uploaded with the sale, or later."""

    setUp = SubDistributorSaleTests.setUp
    _form_data = SubDistributorSaleTests._form_data

    def _proof(self, name="receipt.png"):
        return SimpleUploadedFile(name, b"receipt-bytes", content_type="image/png")

    def test_proof_uploaded_with_a_split_sale_covers_every_batch(self):
        client = Client()
        client.force_login(self.distributor_user)

        client.post(
            "/distributor/sales/new/",
            self._form_data(
                {self.early_batch: 4, self.late_batch: 2},
                payment_proof=self._proof(),
            ),
        )

        sales = list(SubDistributorSale.objects.all())
        self.assertEqual(len(sales), 2)
        self.assertTrue(all(sale.payment_proof for sale in sales))
        self.assertEqual(len({sale.payment_proof.name for sale in sales}), 1)
        self.assertEqual(len({sale.sale_group for sale in sales}), 1)

        response = client.get("/distributor/sales/")
        self.assertContains(response, "View proof", count=2)
        self.assertNotContains(response, "Not received")

    def test_sale_without_proof_is_flagged_and_proof_can_be_added_later(self):
        client = Client()
        client.force_login(self.distributor_user)

        client.post(
            "/distributor/sales/new/",
            self._form_data({self.early_batch: 4, self.late_batch: 2}),
        )
        response = client.get("/distributor/sales/")
        self.assertContains(response, "Not received", count=2)

        first = SubDistributorSale.objects.first()
        response = client.post(
            f"/distributor/sales/{first.pk}/payment-proof/",
            {"proof": self._proof("paid-later.png")},
        )
        self.assertRedirects(response, f"/distributor/sales/{first.pk}/")

        # Added once, it covers both batches of that hand-off.
        self.assertTrue(all(sale.payment_proof for sale in SubDistributorSale.objects.all()))
        response = client.get("/distributor/sales/")
        self.assertNotContains(response, "Not received")

