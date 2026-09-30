from decimal import Decimal

from django.test import Client, TestCase

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


class BatchTraceTests(TestCase):
    """One lot followed end to end: ordered from the Manufacturer,
    received and placed by the Owner, given to a Distributor, placed in
    their warehouse and sold on to a sub-distributor."""

    def setUp(self):
        from apps.distributor_inventory.models import DistributorStockBatch
        from apps.distributor_inventory.services import (
            reallocate_batch as distributor_reallocate_batch,
            sell_to_sub_distributor,
        )
        from apps.distributor_warehouse.models import DistributorLocation
        from apps.distributor_warehouse.services import (
            create_inventory as create_distributor_inventory,
            create_location as create_distributor_location,
        )
        from apps.distributors.models import DistributorProfile
        from apps.distributors.services import approve_distributor, create_distributor
        from apps.owner_inventory.models import StockBatch
        from apps.owner_inventory.services import give_to_distributor, reallocate_batch
        from apps.owner_warehouse.models import Location
        from apps.owner_warehouse.services import create_inventory, create_location

        self.owner = User.objects.create_user(
            email="owner@trace.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        brand = Brand.objects.create(name="Boreal Vita")
        manufacturer = create_manufacturer(actor=self.owner, name="Trace Manufacturer")
        product = create_product(
            actor=self.owner,
            sku="TRACE-A",
            barcode="",
            name="Ashwagandha Root Extract",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        order = create_manufacturer_order(
            actor=self.owner,
            manufacturer=manufacturer,
            brand=brand,
            items=[{"product": product, "quantity": Decimal("100"), "unit_price": Decimal("60.00")}],
        )
        self.batch = order.items.get().batch
        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)
        record_manufacturer_invoice(actor=self.owner, order_id=order.pk, item_prices={})

        region = create_inventory(actor=self.owner, code="TR-1", name="Trace Region")
        warehouse = create_location(
            actor=self.owner,
            code="TR-WH",
            name="Owner Main Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=region,
        )
        owner_warehouse_batch = reallocate_batch(
            actor=self.owner,
            batch=StockBatch.objects.get(source_batch=self.batch),
            quantity=Decimal("100"),
            destination_location=warehouse,
        )

        distributor_user = create_distributor(
            actor=self.owner,
            email="dist@trace.test",
            temporary_password="TemporaryPassword123!",
            name="Lahore Distributors",
        )
        approve_distributor(user=self.owner, distributor_id=distributor_user.pk)
        distributor_user.refresh_from_db()
        profile = DistributorProfile.objects.get(user=distributor_user)

        give_to_distributor(
            actor=self.owner,
            product=product,
            quantity=Decimal("40"),
            from_location=warehouse,
            distributor_profile=profile,
            batch=owner_warehouse_batch,
        )

        distributor_region = create_distributor_inventory(
            actor=distributor_user, code="LHR", name="Lahore"
        )
        distributor_warehouse = create_distributor_location(
            actor=distributor_user,
            code="LHR-WH1",
            name="Gulberg Warehouse",
            location_type=DistributorLocation.LocationType.WAREHOUSE,
            distributor_inventory=distributor_region,
        )
        placed = distributor_reallocate_batch(
            actor=distributor_user,
            distributor_profile=profile,
            batch=DistributorStockBatch.objects.get(distributor_profile=profile),
            quantity=Decimal("40"),
            destination_location=distributor_warehouse,
        )
        sell_to_sub_distributor(
            actor=distributor_user,
            distributor_profile=profile,
            sub_distributor_name="Ali Traders",
            batch=placed,
            quantity=Decimal("15"),
        )

    def test_trace_follows_the_batch_to_the_sub_distributor(self):
        from .services import trace_batch_code

        trace = trace_batch_code(self.batch.code.lower())

        self.assertTrue(trace["found"])
        self.assertEqual(trace["batch"], self.batch)
        self.assertEqual(
            trace["totals"],
            {
                "ordered": Decimal("100"),
                "received_by_owner": Decimal("100"),
                "owner_on_hand": Decimal("60"),
                "in_transit": Decimal("0"),
                "received_by_distributors": Decimal("40"),
                "distributor_on_hand": Decimal("25"),
                "sold_to_sub_distributors": Decimal("15"),
            },
        )

        stages = [event.stage for event in trace["events"]]
        self.assertEqual(stages[0], "Owner → Manufacturer (ordered)")
        self.assertEqual(stages[-1], "Distributor → Sub-distributor (sold)")
        self.assertIn("Manufacturer → Owner (received)", stages)
        self.assertIn("Owner → Distributor (received)", stages)

        sale = trace["events"][-1]
        self.assertEqual(sale.to_label, "Ali Traders")
        self.assertEqual(sale.from_label, "Lahore Distributors — Gulberg Warehouse")

        [row] = trace["distributors"]
        self.assertEqual(
            (row.name, row.received, row.on_hand, row.sold),
            ("Lahore Distributors", Decimal("40"), Decimal("25"), Decimal("15")),
        )

    def test_owner_trace_page_shows_the_journey(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/inventory/trace/", {"code": self.batch.code})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ali Traders")
        self.assertContains(response, "Lahore Distributors")
        self.assertContains(response, "Trace Manufacturer")

    def test_unknown_code_reports_not_found(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/inventory/trace/", {"code": "NOPE-000"})

        self.assertContains(response, "No batch with the code")
