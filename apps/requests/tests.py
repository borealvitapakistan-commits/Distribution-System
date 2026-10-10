from decimal import Decimal
from io import BytesIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.distributor_inventory.models import DistributorStockBalance
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.owner_inventory.models import StockBalance
from apps.owner_inventory.services import receive_stock
from apps.products.services import create_product
from apps.owner_warehouse.models import Location
from apps.owner_warehouse.services import create_inventory, create_location

from .models import PurchaseOrder, PurchaseOrderPayment, PurchaseOrderRevision
from .services import (
    accept_quote,
    add_owner_comment,
    confirm_payment,
    create_purchase_order,
    decline_purchase_order,
    issue_invoice,
    order_steps,
    receive_purchase_order_item,
    record_advance_decision,
    record_payment,
    reject_payment,
    revision_timeline,
    send_quote,
    ship_purchase_order_item,
    update_line_status,
    update_pricing,
)


def _proof_file():
    return SimpleUploadedFile(
        "proof.jpg", b"fake-image-bytes", content_type="image/jpeg"
    )


class PurchaseOrderWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@po.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product_x = create_product(
            actor=self.owner,
            sku="PO-X",
            barcode="",
            name="Product X",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.product_y = create_product(
            actor=self.owner,
            sku="PO-Y",
            barcode="",
            name="Product Y",
            base_retail_price=Decimal("50.00"),
            currency="pkr",
        )
        self.inventory = create_inventory(
            actor=self.owner, code="PO-INV", name="PO Region"
        )
        self.warehouse = create_location(
            actor=self.owner,
            code="PO-WH",
            name="PO Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        self.batch_x = receive_stock(
            actor=self.owner,
            product=self.product_x,
            quantity=Decimal("100"),
            to_location=self.warehouse,
        ).batch
        self.batch_y = receive_stock(
            actor=self.owner,
            product=self.product_y,
            quantity=Decimal("100"),
            to_location=self.warehouse,
        ).batch

        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@po.test",
            temporary_password="TemporaryPassword123!",
            name="PO Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()
        self.distributor_profile = DistributorProfile.objects.get(
            user=self.distributor_user
        )

        self.other_distributor_user = create_distributor(
            actor=self.owner,
            email="other@po.test",
            temporary_password="TemporaryPassword123!",
            name="Other Distributor",
        )
        approve_distributor(
            user=self.owner, distributor_id=self.other_distributor_user.pk
        )
        self.other_distributor_user.refresh_from_db()

    def make_po(self):
        return create_purchase_order(
            actor=self.distributor_user,
            items=[
                {"product": self.product_x, "quantity_requested": Decimal("4")},
                {"product": self.product_y, "quantity_requested": Decimal("5")},
            ],
        )

    def test_creation_generates_po_number_and_snapshots_price(self):
        po = self.make_po()

        self.assertTrue(po.po_number.startswith("PO-"))
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING)

        item_x = po.items.get(product=self.product_x)
        self.assertEqual(item_x.unit_price, Decimal("100.00"))
        self.assertEqual(po.subtotal, Decimal("650.00"))

    def test_without_an_agreement_lines_are_at_list_price(self):
        po = self.make_po()

        self.assertIsNone(po.agreement)
        for item in po.items.all():
            self.assertEqual(item.discount_percentage, Decimal("0"))
            self.assertEqual(item.unit_price, item.list_price)

    def test_only_approved_distributor_can_create(self):
        with self.assertRaises(PermissionDenied):
            create_purchase_order(
                actor=self.owner,
                items=[
                    {"product": self.product_x, "quantity_requested": Decimal("1")}
                ],
            )

    def test_scoping_hides_other_distributors_purchase_orders(self):
        po = self.make_po()

        self.assertEqual(
            PurchaseOrder.objects.for_user(self.distributor_user).count(), 1
        )
        self.assertEqual(
            PurchaseOrder.objects.for_user(self.other_distributor_user).count(), 0
        )
        self.assertEqual(PurchaseOrder.objects.for_user(self.owner).count(), 1)
        self.assertEqual(po.pk, po.pk)

    def test_ship_moves_stock_to_transit_not_distributor(self):
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item_x.pk,
            allocations=[(self.batch_x, Decimal("4"))],
        )

        warehouse_balance = StockBalance.objects.get(
            product=self.product_x, location=self.warehouse
        )
        self.assertEqual(warehouse_balance.quantity, Decimal("96"))

        transit_location = Location.objects.get(
            location_type=Location.LocationType.TRANSIT
        )
        transit_balance = StockBalance.objects.get(
            product=self.product_x, location=transit_location
        )
        self.assertEqual(transit_balance.quantity, Decimal("4"))

        po.refresh_from_db()
        # Product Y hasn't shipped yet, so the order stays open — and
        # isn't invoiced: the invoice goes out once everything has shipped.
        self.assertEqual(po.status, PurchaseOrder.Status.PARTIALLY_SHIPPED)
        self.assertIsNone(po.invoiced_at)
        self.assertFalse(po.can_issue_invoice)

    def test_receive_moves_stock_from_transit_to_the_distributors_own_system(self):
        """Confirming receipt closes out the Owner's Transit balance and
        lands the stock in the Distributor's own, separately tracked
        unallocated pool — not on any Owner-owned Location."""
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item_x.pk,
            allocations=[(self.batch_x, Decimal("4"))],
        )

        receive_purchase_order_item(
            actor=self.distributor_user,
            item_id=item_x.pk,
            quantity=Decimal("4"),
        )

        distributor_balance = DistributorStockBalance.objects.get(
            distributor_profile=self.distributor_profile,
            product=self.product_x,
        )
        self.assertEqual(distributor_balance.quantity, Decimal("4"))
        self.assertEqual(distributor_balance.location.location_type, "UNALLOCATED")

        transit_location = Location.objects.get(
            location_type=Location.LocationType.TRANSIT
        )
        transit_balance = StockBalance.objects.get(
            product=self.product_x, location=transit_location
        )
        self.assertEqual(transit_balance.quantity, Decimal("0"))

    def test_cannot_ship_more_than_requested(self):
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)

        with self.assertRaises(ValidationError):
            ship_purchase_order_item(
                actor=self.owner,
                item_id=item_x.pk,
                allocations=[(self.batch_x, Decimal("999"))],
            )

    def test_cannot_receive_more_than_shipped(self):
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item_x.pk,
            allocations=[(self.batch_x, Decimal("2"))],
        )

        with self.assertRaises(ValidationError):
            receive_purchase_order_item(
                actor=self.distributor_user,
                item_id=item_x.pk,
                quantity=Decimal("3"),
            )

    def test_distributor_cannot_receive_someone_elses_po(self):
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item_x.pk,
            allocations=[(self.batch_x, Decimal("4"))],
        )

        with self.assertRaises(ValidationError):
            receive_purchase_order_item(
                actor=self.other_distributor_user,
                item_id=item_x.pk,
                quantity=Decimal("4"),
            )

    def test_decline_requires_comment_and_stays_sticky(self):
        po = self.make_po()

        with self.assertRaises(ValidationError):
            decline_purchase_order(actor=self.owner, purchase_order_id=po.pk, comment="")

        decline_purchase_order(
            actor=self.owner,
            purchase_order_id=po.pk,
            comment="Out of stock right now.",
        )
        po.refresh_from_db()

        self.assertEqual(po.status, PurchaseOrder.Status.DECLINED)

        item_x = po.items.get(product=self.product_x)
        with self.assertRaises(ValidationError):
            ship_purchase_order_item(
                actor=self.owner,
                item_id=item_x.pk,
                allocations=[(self.batch_x, Decimal("1"))],
            )

    def test_add_owner_comment_does_not_change_status(self):
        po = self.make_po()

        add_owner_comment(
            actor=self.owner,
            purchase_order_id=po.pk,
            comment="Sending some now.",
        )
        po.refresh_from_db()

        self.assertEqual(po.status, PurchaseOrder.Status.PENDING)
        self.assertIn("Sending some now", po.owner_comment)

    def test_update_pricing_changes_grand_total(self):
        po = self.make_po()

        update_pricing(
            actor=self.owner,
            purchase_order_id=po.pk,
            tax_percentage=Decimal("10"),
            shipping_amount=Decimal("50"),
        )
        po.refresh_from_db()

        self.assertEqual(po.tax_percentage, Decimal("10"))
        self.assertEqual(po.tax_amount, Decimal("65.00"))
        self.assertEqual(po.grand_total, Decimal("650.00") + Decimal("65.00") + Decimal("50"))

    def test_record_payment_is_pending_until_owner_confirms(self):
        po = self.make_po()

        self.assertFalse(po.is_fully_paid)

        payment = record_payment(
            actor=self.distributor_user,
            purchase_order_id=po.pk,
            amount=po.grand_total,
            paid_at="2026-01-01",
            proof=_proof_file(),
        )

        self.assertEqual(payment.status, PurchaseOrderPayment.Status.PENDING)

        po.refresh_from_db()
        self.assertFalse(po.is_fully_paid)
        self.assertEqual(po.total_paid, Decimal("0.00"))
        self.assertEqual(po.payments.count(), 1)

        confirm_payment(actor=self.owner, payment_id=payment.pk)

        po.refresh_from_db()
        self.assertTrue(po.is_fully_paid)
        self.assertEqual(po.total_paid, po.grand_total)

    def test_rejected_payment_never_counts(self):
        po = self.make_po()

        payment = record_payment(
            actor=self.distributor_user,
            purchase_order_id=po.pk,
            amount=po.grand_total,
            paid_at="2026-01-01",
            proof=_proof_file(),
        )

        reject_payment(actor=self.owner, payment_id=payment.pk, reason="Wrong amount")

        payment.refresh_from_db()
        self.assertEqual(payment.status, PurchaseOrderPayment.Status.REJECTED)

        po.refresh_from_db()
        self.assertEqual(po.total_paid, Decimal("0.00"))

        with self.assertRaises(ValidationError):
            confirm_payment(actor=self.owner, payment_id=payment.pk)

    def test_only_owner_can_confirm_payment(self):
        po = self.make_po()

        payment = record_payment(
            actor=self.distributor_user,
            purchase_order_id=po.pk,
            amount=po.grand_total,
            paid_at="2026-01-01",
            proof=_proof_file(),
        )

        with self.assertRaises(PermissionDenied):
            confirm_payment(actor=self.distributor_user, payment_id=payment.pk)

    def test_other_distributor_cannot_pay_on_someone_elses_po(self):
        po = self.make_po()

        with self.assertRaises(ValidationError):
            record_payment(
                actor=self.other_distributor_user,
                purchase_order_id=po.pk,
                amount=Decimal("10"),
                paid_at="2026-01-01",
                proof=_proof_file(),
            )


class PaymentGateAndMultiWarehouseShipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@gate.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="GATE-001",
            barcode="",
            name="Gate Product",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.inventory = create_inventory(
            actor=self.owner, code="GATE-INV", name="Gate Region"
        )
        self.warehouse_a = create_location(
            actor=self.owner,
            code="GATE-WH-A",
            name="Warehouse A",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        self.warehouse_b = create_location(
            actor=self.owner,
            code="GATE-WH-B",
            name="Warehouse B",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        self.batch_a = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("30"),
            to_location=self.warehouse_a,
        ).batch
        self.batch_b = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("30"),
            to_location=self.warehouse_b,
        ).batch

    def make_distributor(self, upfront_payment_percentage):
        user = create_distributor(
            actor=self.owner,
            email=f"dist{upfront_payment_percentage}@gate.test",
            temporary_password="TemporaryPassword123!",
            name=f"Distributor {upfront_payment_percentage}",
            upfront_payment_percentage=Decimal(upfront_payment_percentage),
        )
        approve_distributor(user=self.owner, distributor_id=user.pk)
        user.refresh_from_db()
        return user

    def test_each_order_receives_the_batch_shipped_on_it(self):
        """Two orders for the same product in Transit at once: each
        Distributor gets the exact lot shipped to them — not whichever
        expires first."""
        from datetime import date

        from apps.distributor_inventory.models import DistributorStockBatch

        late = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse_a,
            batch_number="LOT-LATE",
            expiry_date=date(2029, 1, 1),
        ).batch
        early = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse_a,
            batch_number="LOT-EARLY",
            expiry_date=date(2027, 1, 1),
        ).batch

        first_user = self.make_distributor(0)
        second_user = create_distributor(
            actor=self.owner,
            email="second@gate.test",
            temporary_password="TemporaryPassword123!",
            name="Second Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=second_user.pk)
        second_user.refresh_from_db()

        first_item = create_purchase_order(
            actor=first_user,
            items=[{"product": self.product, "quantity_requested": Decimal("10")}],
        ).items.get()
        second_item = create_purchase_order(
            actor=second_user,
            items=[{"product": self.product, "quantity_requested": Decimal("10")}],
        ).items.get()

        ship_purchase_order_item(
            actor=self.owner, item_id=first_item.pk, allocations=[(late, Decimal("10"))]
        )
        ship_purchase_order_item(
            actor=self.owner, item_id=second_item.pk, allocations=[(early, Decimal("10"))]
        )

        receive_purchase_order_item(actor=first_user, item_id=first_item.pk, quantity=Decimal("10"))
        receive_purchase_order_item(actor=second_user, item_id=second_item.pk, quantity=Decimal("10"))

        def lots_of(user):
            return list(
                DistributorStockBatch.objects
                .filter(distributor_profile__user=user)
                .values_list("batch_number", "expiry_date", "quantity_remaining")
            )

        self.assertEqual(lots_of(first_user), [("LOT-LATE", date(2029, 1, 1), Decimal("10"))])
        self.assertEqual(lots_of(second_user), [("LOT-EARLY", date(2027, 1, 1), Decimal("10"))])

    def test_ship_splits_across_multiple_warehouses_in_one_action(self):
        distributor_user = self.make_distributor(0)
        po = create_purchase_order(
            actor=distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("20")}],
        )
        item = po.items.get()

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item.pk,
            allocations=[
                (self.batch_a, Decimal("12")),
                (self.batch_b, Decimal("8")),
            ],
        )

        item.refresh_from_db()
        self.assertEqual(item.quantity_shipped, Decimal("20"))

        self.batch_a.refresh_from_db()
        self.batch_b.refresh_from_db()
        self.assertEqual(self.batch_a.quantity_remaining, Decimal("18"))
        self.assertEqual(self.batch_b.quantity_remaining, Decimal("22"))

        transit_location = Location.objects.get(location_type=Location.LocationType.TRANSIT)
        transit_balance = StockBalance.objects.get(
            product=self.product, location=transit_location
        )
        self.assertEqual(transit_balance.quantity, Decimal("20"))

    def test_shipping_blocked_until_full_upfront_payment_confirmed(self):
        distributor_user = self.make_distributor(100)
        po = create_purchase_order(
            actor=distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("5")}],
        )
        item = po.items.get()

        with self.assertRaises(ValidationError):
            ship_purchase_order_item(
                actor=self.owner,
                item_id=item.pk,
                allocations=[(self.batch_a, Decimal("5"))],
            )

        payment = record_payment(
            actor=distributor_user,
            purchase_order_id=po.pk,
            amount=po.grand_total,
            paid_at="2026-01-01",
            proof=_proof_file(),
        )

        with self.assertRaises(ValidationError):
            ship_purchase_order_item(
                actor=self.owner,
                item_id=item.pk,
                allocations=[(self.batch_a, Decimal("5"))],
            )

        confirm_payment(actor=self.owner, payment_id=payment.pk)

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item.pk,
            allocations=[(self.batch_a, Decimal("5"))],
        )

        item.refresh_from_db()
        self.assertEqual(item.quantity_shipped, Decimal("5"))

    def test_split_terms_require_only_the_upfront_portion(self):
        distributor_user = self.make_distributor(30)
        po = create_purchase_order(
            actor=distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("10")}],
        )
        item = po.items.get()

        self.assertEqual(po.required_upfront_amount, Decimal("300.00"))

        payment = record_payment(
            actor=distributor_user,
            purchase_order_id=po.pk,
            amount=Decimal("300.00"),
            paid_at="2026-01-01",
            proof=_proof_file(),
        )
        confirm_payment(actor=self.owner, payment_id=payment.pk)

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item.pk,
            allocations=[(self.batch_a, Decimal("10"))],
        )

        item.refresh_from_db()
        self.assertEqual(item.quantity_shipped, Decimal("10"))

    def test_pay_after_receiving_terms_never_block_shipping(self):
        distributor_user = self.make_distributor(0)
        po = create_purchase_order(
            actor=distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("5")}],
        )
        item = po.items.get()

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item.pk,
            allocations=[(self.batch_a, Decimal("5"))],
        )

        item.refresh_from_db()
        self.assertEqual(item.quantity_shipped, Decimal("5"))


class PurchaseOrderViewWiringTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@poviews.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="POVIEW-001",
            barcode="",
            name="View Product",
            base_retail_price=Decimal("20.00"),
            currency="pkr",
        )
        self.inventory = create_inventory(
            actor=self.owner, code="POVIEW-INV", name="POView Region"
        )
        self.warehouse = create_location(
            actor=self.owner,
            code="POVIEW-WH",
            name="POView Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        self.batch = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=self.warehouse,
        ).batch
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@poviews.test",
            temporary_password="TemporaryPassword123!",
            name="POView Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()

    def test_distributor_can_submit_po_via_form(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/distributor/purchase-orders/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-price")

        response = client.post(
            "/distributor/purchase-orders/new/",
            {
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-product": str(self.product.pk),
                "items-0-quantity_requested": "3",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_owner_can_ship_from_detail_page_and_pdf_downloads(self):
        po = create_purchase_order(
            actor=self.distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("3")}],
        )
        item = po.items.get()

        client = Client()
        client.force_login(self.owner)

        response = client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertEqual(response.status_code, 200)

        response = client.post(
            f"/owner/purchase-orders/{po.pk}/items/{item.pk}/ship/",
            {f"batch_qty_{self.batch.pk}": "3"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        self.assertEqual(item.quantity_shipped, Decimal("3"))

        pdf_response = client.get(f"/purchase-orders/{po.pk}/pdf/")
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_distributor_can_receive_and_pay_from_detail_page(self):
        po = create_purchase_order(
            actor=self.distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("3")}],
        )
        item = po.items.get()

        ship_purchase_order_item(
            actor=self.owner,
            item_id=item.pk,
            allocations=[(self.batch, Decimal("3"))],
        )

        client = Client()
        client.force_login(self.distributor_user)

        response = client.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "Order Received")

        response = client.post(
            f"/distributor/purchase-orders/{po.pk}/received/",
            {f"qty_{item.pk}": "3", "answer": "no"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        self.assertEqual(item.quantity_received, Decimal("3"))

        response = client.post(
            f"/distributor/purchase-orders/{po.pk}/payments/",
            {
                "amount": "60.00",
                "paid_at": "2026-01-01",
                "note": "Bank transfer",
                "proof": _proof_file(),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        po.refresh_from_db()
        self.assertEqual(po.payments.count(), 1)
        # Paid after the goods were shipped, so it's the final payment.
        self.assertEqual(po.payments.get().kind, PurchaseOrderPayment.Kind.FINAL)

    def test_sidebar_shows_purchase_orders_nav_for_both_roles(self):
        owner_client = Client()
        owner_client.force_login(self.owner)
        owner_response = owner_client.get("/owner/")
        self.assertEqual(owner_response.status_code, 200)
        self.assertContains(owner_response, "Purchase Orders")

        distributor_client = Client()
        distributor_client.force_login(self.distributor_user)
        distributor_response = distributor_client.get("/distributor/")
        self.assertEqual(distributor_response.status_code, 200)
        self.assertContains(distributor_response, "Orders")


class DistributorPaymentFlowTests(TestCase):
    """Place order → answer the advance → Owner confirms and ships →
    Order Received with the rest paid: the same flow as an order to a
    Manufacturer, with the Owner confirming each payment."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@poflow.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="POFLOW-001",
            barcode="",
            name="Flow Product",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        inventory = create_inventory(actor=self.owner, code="FLOW-INV", name="Flow Region")
        warehouse = create_location(
            actor=self.owner,
            code="FLOW-WH",
            name="Flow Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=inventory,
        )
        self.batch = receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("50"),
            to_location=warehouse,
            batch_number="FLOW-LOT-1",
        ).batch
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@poflow.test",
            temporary_password="TemporaryPassword123!",
            name="Flow Distributor",
            upfront_payment_percentage=Decimal("30"),
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()
        self.client.force_login(self.distributor_user)

    def _place_order(self):
        # 10 x 100.00 = 1,000.00
        return create_purchase_order(
            actor=self.distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("10")}],
        )

    def test_split_advance_and_final_through_the_pages(self):
        response = self.client.post(
            "/distributor/purchase-orders/new/",
            {
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-product": self.product.pk,
                "items-0-quantity_requested": "10",
            },
        )
        po = PurchaseOrder.objects.get()
        self.assertEqual(po.status, PurchaseOrder.Status.QUOTE)

        # The Owner quotes from their panel; accepting it places the order.
        owner = Client()
        owner.force_login(self.owner)
        item = po.items.get()
        owner.post(
            f"/owner/purchase-orders/{po.pk}/quote/",
            {f"line_price_{item.pk}": "100.00", f"line_qty_{item.pk}": "10"},
        )
        response = self.client.post(f"/distributor/purchase-orders/{po.pk}/accept/")
        advance_url = f"/distributor/purchase-orders/{po.pk}/advance/"
        self.assertRedirects(response, advance_url)

        page = self.client.get(advance_url)
        self.assertContains(page, "Are you paying in advance?")
        self.assertContains(page, "Required 30%")

        self.client.post(
            advance_url,
            {"answer": "yes", "amount": "300", "paid_at": "2026-01-01", "proof": _proof_file()},
        )
        advance = po.payments.get()
        self.assertEqual(advance.kind, PurchaseOrderPayment.Kind.ADVANCE)

        # The Owner can't ship on an unconfirmed advance...
        item = po.items.get()
        with self.assertRaises(ValidationError):
            ship_purchase_order_item(
                actor=self.owner, item_id=item.pk, allocations=[(self.batch, Decimal("10"))]
            )

        # ...but can once it's confirmed.
        confirm_payment(actor=self.owner, payment_id=advance.pk)
        ship_purchase_order_item(
            actor=self.owner, item_id=item.pk, allocations=[(self.batch, Decimal("10"))]
        )

        receive_url = f"/distributor/purchase-orders/{po.pk}/received/"
        page = self.client.get(receive_url)
        self.assertContains(page, "Have you paid the remaining amount?")

        # Something is still owed, so the question must be answered.
        response = self.client.post(receive_url, {f"qty_{item.pk}": "10"})
        self.assertContains(response, "Please answer")

        response = self.client.post(
            receive_url,
            {
                f"qty_{item.pk}": "10",
                "answer": "yes",
                "amount": "700",
                "paid_at": "2026-02-01",
                "proof": _proof_file(),
            },
        )
        self.assertRedirects(response, f"/distributor/purchase-orders/{po.pk}/")

        po.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(item.quantity_received, Decimal("10"))
        self.assertEqual(po.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(po.advance_paid, Decimal("300.00"))
        self.assertEqual(po.final_paid, Decimal("700.00"))
        self.assertEqual(po.payment_scenario, "30.0% advance + 70.0% on receipt")

        # Final payment awaits the Owner; both parts show on both sides.
        self.assertEqual(po.total_paid, Decimal("300.00"))
        detail = self.client.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertContains(detail, "View proof", count=2)
        self.assertContains(detail, "Awaiting Owner")

        owner_client = Client()
        owner_client.force_login(self.owner)
        owner_detail = owner_client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertContains(owner_detail, "30.0% advance + 70.0% on receipt")
        self.assertContains(owner_detail, "View proof", count=2)

    def test_no_advance_then_everything_on_receipt(self):
        self.distributor_user.distributor_profile.upfront_payment_percentage = Decimal("0")
        self.distributor_user.distributor_profile.save()
        po = self._place_order()

        response = self.client.post(f"/distributor/purchase-orders/{po.pk}/advance/", {"answer": "no"})
        self.assertRedirects(response, f"/distributor/purchase-orders/{po.pk}/")
        po.refresh_from_db()
        self.assertIs(po.pays_advance, False)

        item = po.items.get()
        ship_purchase_order_item(
            actor=self.owner, item_id=item.pk, allocations=[(self.batch, Decimal("10"))]
        )
        self.client.post(
            f"/distributor/purchase-orders/{po.pk}/received/",
            {
                f"qty_{item.pk}": "10",
                "answer": "yes",
                "amount": "1000",
                "paid_at": "2026-02-01",
                "proof": _proof_file(),
            },
        )

        po.refresh_from_db()
        self.assertEqual(po.final_paid, Decimal("1000.00"))
        self.assertEqual(po.payment_scenario, "No advance — paid in full on receipt")

    def test_cannot_claim_more_than_is_owed(self):
        po = self._place_order()

        with self.assertRaises(ValidationError):
            record_payment(
                actor=self.distributor_user,
                purchase_order_id=po.pk,
                amount=Decimal("1000.01"),
                paid_at="2026-01-01",
                proof=_proof_file(),
            )

    def test_rejected_advance_can_be_paid_again_before_shipping(self):
        po = self._place_order()
        self.client.post(
            f"/distributor/purchase-orders/{po.pk}/advance/",
            {"answer": "yes", "amount": "300", "paid_at": "2026-01-01", "proof": _proof_file()},
        )
        reject_payment(actor=self.owner, payment_id=po.payments.get().pk, reason="Wrong slip")

        po.refresh_from_db()
        self.assertEqual(po.advance_paid, Decimal("0.00"))
        self.assertEqual(po.remaining_amount, Decimal("1000.00"))

        detail = self.client.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertContains(detail, "Pay advance")

        self.client.post(
            f"/distributor/purchase-orders/{po.pk}/payments/",
            {"amount": "300", "paid_at": "2026-01-02", "proof": _proof_file()},
        )
        retry = po.payments.exclude(status=PurchaseOrderPayment.Status.REJECTED).get()
        self.assertEqual(retry.kind, PurchaseOrderPayment.Kind.ADVANCE)


class OrdersHubAndNotificationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@notify.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="NOTIFY-001",
            barcode="",
            name="Notify Product",
            base_retail_price=Decimal("40.00"),
            currency="pkr",
        )
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@notify.test",
            temporary_password="TemporaryPassword123!",
            name="Notify Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()

    def test_distributor_orders_hub_has_both_buttons(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/distributor/orders/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Place Order")
        self.assertContains(response, "View Orders")

    def test_new_order_shows_badge_and_dashboard_card_until_viewed(self):
        po = create_purchase_order(
            actor=self.distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("2")}],
        )

        client = Client()
        client.force_login(self.owner)

        dashboard_response = client.get("/owner/")
        self.assertContains(dashboard_response, "1 new Purchase Order")
        self.assertContains(dashboard_response, po.po_number)

        owner_home_response = client.get("/owner/")
        self.assertContains(owner_home_response, "nav-badge")

        po.refresh_from_db()
        self.assertIsNone(po.owner_viewed_at)

        detail_response = client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertContains(detail_response, "new order you haven't opened")

        po.refresh_from_db()
        self.assertIsNotNone(po.owner_viewed_at)

        dashboard_after_response = client.get("/owner/")
        self.assertNotContains(dashboard_after_response, "new Purchase Order")

        second_detail_response = client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertNotContains(second_detail_response, "new order you haven't opened")


class QuoteToInvoiceTests(TestCase):
    """The four stages, each side on its own panel: the Distributor's
    Request to Quote → the Owner's quote (and counter-offers back and
    forth) → the Purchase Order once the Distributor accepts → the
    Owner's invoice. Every step lands in the order's conversation and
    notifies the other side."""

    def setUp(self):
        from apps.core.models import Brand

        Brand.objects.create(name="Boreal Vita", active=True)
        self.owner = User.objects.create_user(
            email="owner@rtq.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product_x = create_product(
            actor=self.owner, sku="RTQ-X", barcode="", name="Product X",
            base_retail_price=Decimal("100.00"), currency="pkr",
        )
        self.product_y = create_product(
            actor=self.owner, sku="RTQ-Y", barcode="", name="Product Y",
            base_retail_price=Decimal("50.00"), currency="pkr",
        )
        inventory = create_inventory(actor=self.owner, code="RTQ-INV", name="RTQ Region")
        warehouse = create_location(
            actor=self.owner,
            code="RTQ-WH",
            name="RTQ Warehouse",
            location_type=Location.LocationType.OWN,
            inventory=inventory,
        )
        self.batch_x = receive_stock(
            actor=self.owner, product=self.product_x, quantity=Decimal("100"), to_location=warehouse,
        ).batch
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@rtq.test",
            temporary_password="TemporaryPassword123!",
            name="RTQ Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()
        self.other_distributor_user = create_distributor(
            actor=self.owner,
            email="other@rtq.test",
            temporary_password="TemporaryPassword123!",
            name="Other RTQ Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.other_distributor_user.pk)
        self.other_distributor_user.refresh_from_db()

        self.owner_client = Client()
        self.owner_client.force_login(self.owner)
        self.distributor = Client()
        self.distributor.force_login(self.distributor_user)

    def _request_to_quote(self):
        response = self.distributor.post(
            "/distributor/purchase-orders/new/",
            {
                "items-TOTAL_FORMS": "2",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-product": str(self.product_x.pk),
                "items-0-quantity_requested": "10",
                "items-0-requested_unit_price": "90.00",
                "items-1-product": str(self.product_y.pk),
                "items-1-quantity_requested": "5",
                "items-1-requested_unit_price": "",
                "message": "Need these for the spring season.",
            },
        )
        po = PurchaseOrder.objects.get()
        self.assertRedirects(response, f"/distributor/purchase-orders/{po.pk}/")
        return po

    def test_request_to_quote_is_not_an_order_yet(self):
        po = self._request_to_quote()

        self.assertEqual(po.status, PurchaseOrder.Status.QUOTE)
        self.assertEqual(po.document_title, "Request to Quote")
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)
        self.assertEqual(item_x.requested_unit_price, Decimal("90.00"))
        self.assertIsNone(item_y.unit_price)
        self.assertEqual(po.subtotal, Decimal("900.00"))

        request = po.revisions.get()
        self.assertEqual(request.stage, PurchaseOrderRevision.Stage.REQUEST)
        self.assertFalse(request.by_owner)
        self.assertEqual(request.message, "Need these for the spring season.")

        with self.assertRaises(ValidationError):
            ship_purchase_order_item(
                actor=self.owner, item_id=item_x.pk, allocations=[(self.batch_x, Decimal("1"))],
            )
        with self.assertRaises(ValidationError):
            record_payment(
                actor=self.distributor_user, purchase_order_id=po.pk, amount=Decimal("10"),
                paid_at="2026-01-01", proof=_proof_file(),
            )
        with self.assertRaises(ValidationError):
            accept_quote(actor=self.distributor_user, purchase_order_id=po.pk)

        # The Owner is notified of the new request.
        self.assertContains(self.owner_client.get("/owner/"), "1 new Purchase Order")
        page = self.owner_client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertContains(page, "New Request to Quote")
        self.assertContains(page, "Need these for the spring season.")

    def test_distributor_can_edit_the_request_only_until_the_owner_quotes(self):
        po = self._request_to_quote()
        edit_url = f"/distributor/purchase-orders/{po.pk}/edit/"

        self.assertEqual(self.distributor.get(edit_url).status_code, 200)
        self.distributor.post(
            edit_url,
            {
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-product": str(self.product_x.pk),
                "items-0-quantity_requested": "12",
                "items-0-requested_unit_price": "85.00",
                "message": "Changed my mind.",
            },
        )
        po.refresh_from_db()
        item = po.items.get()
        self.assertEqual(item.quantity_requested, Decimal("12"))
        self.assertEqual(item.requested_unit_price, Decimal("85.00"))
        request = po.revisions.get()
        self.assertEqual(request.message, "Changed my mind.")
        self.assertEqual(request.lines.get().quantity, Decimal("12"))

        send_quote(
            actor=self.owner,
            purchase_order_id=po.pk,
            item_updates={str(item.pk): {"unit_price": Decimal("95.00")}},
        )
        response = self.distributor.get(edit_url)
        self.assertRedirects(response, f"/distributor/purchase-orders/{po.pk}/")

    def test_the_whole_conversation_from_request_to_invoice(self):
        po = self._request_to_quote()
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)
        self.owner_client.get(f"/owner/purchase-orders/{po.pk}/")

        # 2. The Owner quotes from the Owner's panel: a different price for
        #    X, and drops Y which they can't supply.
        quote_url = f"/owner/purchase-orders/{po.pk}/quote/"
        page = self.owner_client.get(quote_url)
        self.assertContains(page, f'name="line_price_{item_x.pk}"')
        self.owner_client.post(
            quote_url,
            {
                f"line_price_{item_x.pk}": "95.00",
                f"line_qty_{item_x.pk}": "10",
                f"line_price_{item_y.pk}": "",
                f"line_qty_{item_y.pk}": "5",
                f"line_remove_{item_y.pk}": "on",
                "message": "Y is out of stock this month.",
            },
        )
        po.refresh_from_db()
        item_x.refresh_from_db()
        self.assertIsNotNone(po.quoted_at)
        self.assertEqual(po.items.count(), 1)
        self.assertEqual(item_x.unit_price, Decimal("95.00"))
        self.assertEqual(item_x.discount_percentage, Decimal("5.00"))
        self.assertEqual(item_x.price_highlight, "changed")

        # The Distributor is notified and can't accept their own words.
        dashboard = self.distributor.get("/distributor/")
        self.assertContains(dashboard, "1 update from the Owner")
        self.assertContains(dashboard, "the Owner sent a quote")
        detail = self.distributor.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertContains(detail, "quote is in.")
        self.assertContains(detail, "Y is out of stock this month.")
        self.assertNotContains(self.distributor.get("/distributor/"), "update from the Owner")

        # Counter-offer from the Distributor's panel.
        counter_url = f"/distributor/purchase-orders/{po.pk}/counter-offer/"
        self.distributor.post(
            counter_url,
            {f"line_price_{item_x.pk}": "92.00", f"line_qty_{item_x.pk}": "10", "message": "Meet me at 92?"},
        )
        item_x.refresh_from_db()
        self.assertEqual(item_x.unit_price, Decimal("95.00"))  # only proposed
        with self.assertRaises(ValidationError):
            accept_quote(actor=self.distributor_user, purchase_order_id=po.pk)
        self.assertEqual(self.distributor.get(counter_url).status_code, 302)

        # The Owner is notified of the counter-offer.
        owner_dashboard = self.owner_client.get("/owner/")
        self.assertContains(owner_dashboard, "1 Purchase Order update from Distributors")
        self.assertContains(owner_dashboard, "counter-offer received")
        page = self.owner_client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertContains(page, "Counter-offer received.")

        # The Owner agrees: the quote page starts from the counter-offer.
        page = self.owner_client.get(quote_url)
        self.assertContains(page, 'value="92.00"')
        self.owner_client.post(
            quote_url,
            {f"line_price_{item_x.pk}": "92.00", f"line_qty_{item_x.pk}": "10", "message": "Deal."},
        )

        # 3. The Distributor accepts — it becomes their Purchase Order.
        response = self.distributor.post(
            f"/distributor/purchase-orders/{po.pk}/accept/", {"message": "Thanks!"}
        )
        self.assertRedirects(response, f"/distributor/purchase-orders/{po.pk}/advance/")
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING)
        self.assertIsNotNone(po.po_placed_at)
        self.assertEqual(po.grand_total, Decimal("920.00"))

        # The Owner changes the Purchase Order → a new version.
        change_url = f"/owner/purchase-orders/{po.pk}/change/"
        self.owner_client.post(
            change_url,
            {f"line_price_{item_x.pk}": "92.00", f"line_qty_{item_x.pk}": "8", "message": "Only 8 left."},
        )
        po.refresh_from_db()
        self.assertEqual(po.items.get().quantity_requested, Decimal("8"))
        self.assertContains(self.distributor.get("/distributor/"), "the Owner changed the Purchase Order")

        # Ship everything, then 4. the invoice.
        invoice_url = f"/owner/purchase-orders/{po.pk}/invoice/"
        self.assertEqual(self.owner_client.get(invoice_url).status_code, 302)
        record_advance_decision(actor=self.distributor_user, purchase_order_id=po.pk, pays_advance=False)
        ship_purchase_order_item(
            actor=self.owner, item_id=item_x.pk, allocations=[(self.batch_x, Decimal("8"))],
        )
        po.refresh_from_db()
        self.assertTrue(po.can_issue_invoice)
        self.assertFalse(po.can_change)

        self.assertEqual(self.owner_client.get(invoice_url).status_code, 200)
        self.owner_client.post(
            invoice_url, {"tax_percentage": "10", "shipping_amount": "20", "message": ""},
        )
        po.refresh_from_db()
        self.assertEqual(po.invoice_number, "SI-BOREAL-VITA-0001")
        self.assertIsNotNone(po.invoiced_at)
        self.assertEqual(po.grand_total, Decimal("829.60"))  # 736 + 73.60 tax + 20
        self.assertEqual(po.document_title, "Invoice")
        with self.assertRaises(ValidationError):
            update_pricing(
                actor=self.owner, purchase_order_id=po.pk,
                tax_percentage=Decimal("0"), shipping_amount=Decimal("0"),
            )
        with self.assertRaises(ValidationError):
            decline_purchase_order(actor=self.owner, purchase_order_id=po.pk, comment="No")

        # The whole conversation, in order, on both panels.
        timeline = revision_timeline(po)
        self.assertEqual(
            [step["label"] for step in timeline],
            [
                "Request to Quote",
                "Owner's quote",
                "Distributor's counter-offer",
                "Owner's quote",
                "Purchase Order",
                "Purchase Order v2",
                "Invoice",
            ],
        )
        self.assertEqual(
            [step["revision"].by_owner for step in timeline],
            [False, True, False, True, False, True, True],
        )
        self.assertEqual([step["state"] for step in order_steps(po)], ["done"] * 4)

        for client, url in (
            (self.owner_client, f"/owner/purchase-orders/{po.pk}/"),
            (self.distributor, f"/distributor/purchase-orders/{po.pk}/"),
        ):
            page = client.get(url)
            self.assertContains(page, "Conversation history")
            self.assertContains(page, "SI-BOREAL-VITA-0001")
            self.assertEqual(client.get(f"/purchase-orders/{po.pk}/pdf/").status_code, 200)
            record = client.get(f"/purchase-orders/{po.pk}/record/")
            self.assertEqual(record.status_code, 200)
            self.assertEqual(record["Content-Type"], "application/pdf")
            self.assertEqual(
                client.get(f"/purchase-orders/{po.pk}/conversation/").status_code, 200
            )

    def test_owner_quotes_straight_from_the_order_page(self):
        po = self._request_to_quote()
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)

        page = self.owner_client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertContains(page, f'name="line_price_{item_x.pk}"')
        self.assertContains(page, f'action="/owner/purchase-orders/{po.pk}/quote/"')
        self.assertContains(page, "Send Quote to Distributor")

        response = self.owner_client.post(
            f"/owner/purchase-orders/{po.pk}/quote/",
            {
                f"line_price_{item_x.pk}": "88.00",
                f"line_qty_{item_x.pk}": "10",
                f"line_price_{item_y.pk}": "45.00",
                f"line_qty_{item_y.pk}": "5",
            },
        )
        self.assertRedirects(response, f"/owner/purchase-orders/{po.pk}/")
        item_x.refresh_from_db()
        self.assertEqual(item_x.unit_price, Decimal("88.00"))

        # Revised quotes can still be sent from the same page.
        page = self.owner_client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertContains(page, "Send Revised Quote to Distributor")

        # Once accepted, the page shows the Purchase Order — no longer editable here.
        accept_quote(actor=self.distributor_user, purchase_order_id=po.pk)
        page = self.owner_client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertNotContains(page, f'name="line_price_{item_x.pk}"')

    def test_payment_form_only_shows_when_something_is_due(self):
        po = create_purchase_order(
            actor=self.distributor_user,
            items=[{"product": self.product_x, "quantity_requested": Decimal("4")}],
        )
        record_advance_decision(actor=self.distributor_user, purchase_order_id=po.pk, pays_advance=False)
        po.refresh_from_db()

        # "No advance" with no advance required: nothing is due until it arrives.
        self.assertFalse(po.can_pay_now)
        page = self.distributor.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertNotContains(page, "Pay advance")

        # Once shipped, the remaining payment can be made.
        ship_purchase_order_item(
            actor=self.owner,
            item_id=po.items.get().pk,
            allocations=[(self.batch_x, Decimal("4"))],
        )
        po.refresh_from_db()
        self.assertTrue(po.can_pay_now)
        page = self.distributor.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertContains(page, "Record remaining payment")

    def test_required_advance_can_still_be_paid_after_answering_no(self):
        profile = self.distributor_user.distributor_profile
        profile.upfront_payment_percentage = Decimal("30")
        profile.save()
        po = create_purchase_order(
            actor=self.distributor_user,
            items=[{"product": self.product_x, "quantity_requested": Decimal("4")}],
        )
        record_advance_decision(actor=self.distributor_user, purchase_order_id=po.pk, pays_advance=False)
        po.refresh_from_db()

        # Shipping is blocked until the required 30% is paid, so it stays payable.
        self.assertTrue(po.can_pay_now)
        page = self.distributor.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertContains(page, "Pay advance")

    def test_owner_can_decline_a_request_to_quote(self):
        po = self._request_to_quote()

        decline_purchase_order(actor=self.owner, purchase_order_id=po.pk, comment="Not this season.")
        po.refresh_from_db()

        self.assertTrue(po.declined_as_quote)
        self.assertIsNone(po.purchase_order_date)
        self.assertEqual(order_steps(po)[0]["note"], "Declined by the Owner")
        with self.assertRaises(ValidationError):
            send_quote(
                actor=self.owner,
                purchase_order_id=po.pk,
                item_updates={str(item.pk): {"unit_price": Decimal("1")} for item in po.items.all()},
            )
        page = self.distributor.get(f"/distributor/purchase-orders/{po.pk}/")
        self.assertContains(page, "The Owner declined this request.")

    def test_only_the_distributor_who_asked_can_answer_the_quote(self):
        po = self._request_to_quote()
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)
        send_quote(
            actor=self.owner,
            purchase_order_id=po.pk,
            item_updates={
                str(item_x.pk): {"unit_price": Decimal("90.00")},
                str(item_y.pk): {"unit_price": Decimal("50.00")},
            },
        )

        with self.assertRaises(ValidationError):
            accept_quote(actor=self.other_distributor_user, purchase_order_id=po.pk)

        other = Client()
        other.force_login(self.other_distributor_user)
        self.assertEqual(
            other.get(f"/distributor/purchase-orders/{po.pk}/counter-offer/").status_code, 404
        )
        other.post(f"/distributor/purchase-orders/{po.pk}/accept/")
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.QUOTE)

        with self.assertRaises(PermissionDenied):
            send_quote(
                actor=self.distributor_user,
                purchase_order_id=po.pk,
                item_updates={},
            )

    def test_a_partly_shipped_order_cannot_be_invoiced_until_the_rest_is_closed(self):
        po = create_purchase_order(
            actor=self.distributor_user,
            items=[
                {"product": self.product_x, "quantity_requested": Decimal("4")},
                {"product": self.product_y, "quantity_requested": Decimal("5")},
            ],
        )
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)
        ship_purchase_order_item(
            actor=self.owner, item_id=item_x.pk, allocations=[(self.batch_x, Decimal("4"))],
        )

        with self.assertRaises(ValidationError):
            issue_invoice(actor=self.owner, purchase_order_id=po.pk)

        update_line_status(actor=self.owner, item_id=item_y.pk, note="Discontinued", unavailable=True)
        po = issue_invoice(actor=self.owner, purchase_order_id=po.pk)

        self.assertEqual(po.subtotal, Decimal("400.00"))
        invoice = po.revisions.get(stage=PurchaseOrderRevision.Stage.INVOICE)
        lines = {line.product_id: line for line in invoice.lines.all()}
        self.assertTrue(lines[self.product_y.pk].removed)
        self.assertEqual(lines[self.product_x.pk].quantity, Decimal("4"))
