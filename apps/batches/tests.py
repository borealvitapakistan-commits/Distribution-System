from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.core.models import Brand
from apps.manufacturers.services import (
    create_manufacturer,
    create_manufacturer_order,
    mark_manufacturer_order_received,
    record_manufacturer_invoice,
    set_manufacturer_order_outcome,
)
from apps.manufacturers.models import ManufacturerOrder
from apps.products.services import create_product

from .models import Batch


class BatchLifecycleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@batches.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.brand = Brand.objects.create(name="Boreal Vita")
        self.manufacturer = create_manufacturer(actor=self.owner, name="Batch Manufacturer")
        self.product_a = create_product(
            actor=self.owner,
            sku="BATCH-A",
            barcode="",
            name="Ashwagandha Root Extract",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.product_b = create_product(
            actor=self.owner,
            sku="BATCH-B",
            barcode="",
            name="Immunity Booster Syrup",
            base_retail_price=Decimal("50.00"),
            currency="pkr",
        )

    def make_order(self, products=None):
        products = products or [self.product_a, self.product_b]
        return create_manufacturer_order(
            actor=self.owner,
            manufacturer=self.manufacturer,
            brand=self.brand,
            items=[
                {"product": p, "quantity": Decimal("10"), "unit_price": Decimal("60.00")}
                for p in products
            ],
        )

    def test_one_batch_is_created_per_line_item_at_order_time(self):
        order = self.make_order()

        self.assertEqual(Batch.objects.filter(manufacturer_order_item__order=order).count(), 2)

        for batch in Batch.objects.filter(manufacturer_order_item__order=order):
            self.assertEqual(batch.status, Batch.Status.PENDING)

    def test_batch_code_format_is_brand_product_sequence(self):
        order = self.make_order(products=[self.product_a])
        item = order.items.get()

        self.assertEqual(item.batch.code, "BOR-ASH-001")

    def test_batch_code_sequence_increments_per_brand_product_pair(self):
        self.make_order(products=[self.product_a])
        order2 = self.make_order(products=[self.product_a])

        item2 = order2.items.get()
        self.assertEqual(item2.batch.code, "BOR-ASH-002")

    def test_batch_has_no_direct_product_field(self):
        self.assertNotIn("product", [f.name for f in Batch._meta.fields])

    def test_batch_reaches_product_through_order_item(self):
        order = self.make_order(products=[self.product_a])
        item = order.items.get()

        self.assertEqual(item.batch.manufacturer_order_item.product, self.product_a)

    def test_invoice_approval_marks_batch_received_and_links_stock_batch(self):
        order = self.make_order(products=[self.product_a])
        item = order.items.get()

        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)
        record_manufacturer_invoice(
            actor=self.owner,
            order_id=order.pk,
            item_prices={
                str(item.pk): {"expiry_date": "2027-06-01"},
            },
        )

        item.batch.refresh_from_db()
        self.assertEqual(item.batch.status, Batch.Status.RECEIVED)
        self.assertIsNotNone(item.batch.received_at)
        self.assertEqual(str(item.batch.expiry_date), "2027-06-01")

        from apps.owner_inventory.models import StockBatch

        stock_batch = StockBatch.objects.get(source_batch=item.batch)
        self.assertEqual(stock_batch.batch_number, item.batch.code)
        self.assertEqual(str(stock_batch.expiry_date), "2027-06-01")

    def test_refund_without_invoice_cancels_the_pending_batch(self):
        order = self.make_order(products=[self.product_a])
        item = order.items.get()

        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)
        set_manufacturer_order_outcome(
            actor=self.owner,
            order_id=order.pk,
            outcome=ManufacturerOrder.Status.REFUNDED,
        )

        item.batch.refresh_from_db()
        self.assertEqual(item.batch.status, Batch.Status.CANCELLED)
        self.assertIsNotNone(item.batch.cancelled_at)

    def test_refund_after_invoice_does_not_uncancel_a_received_batch(self):
        order = self.make_order(products=[self.product_a])
        item = order.items.get()

        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)
        record_manufacturer_invoice(actor=self.owner, order_id=order.pk, item_prices={})
        set_manufacturer_order_outcome(
            actor=self.owner,
            order_id=order.pk,
            outcome=ManufacturerOrder.Status.REFUNDED,
        )

        item.batch.refresh_from_db()
        self.assertEqual(item.batch.status, Batch.Status.RECEIVED)

    def test_missing_brand_falls_back_to_generic_prefix(self):
        order = create_manufacturer_order(
            actor=self.owner,
            manufacturer=self.manufacturer,
            items=[
                {"product": self.product_a, "quantity": Decimal("5"), "unit_price": Decimal("10")}
            ],
        )
        item = order.items.get()
        self.assertTrue(item.batch.code.startswith("GEN-ASH-"))
