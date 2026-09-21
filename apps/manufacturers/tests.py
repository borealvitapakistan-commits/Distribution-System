from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.distributors.services import approve_distributor, create_distributor
from apps.products.services import create_product

from .models import Manufacturer, ManufacturerOrder
from .services import (
    create_manufacturer,
    create_manufacturer_order,
    mark_manufacturer_order_received,
    record_manufacturer_invoice,
    record_manufacturer_payment,
    set_manufacturer_order_outcome,
)


class ManufacturerRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@manufacturer.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def test_manufacturer_name_is_globally_unique(self):
        create_manufacturer(actor=self.owner, name="Same Name")

        with self.assertRaises(ValidationError):
            create_manufacturer(actor=self.owner, name="Same Name")

    def test_owner_manufacturer_pages_are_connected(self):
        manufacturer = create_manufacturer(actor=self.owner, name="Page Manufacturer")
        self.client.force_login(self.owner)

        for route in [
            reverse("manufacturer-list"),
            reverse("manufacturer-create"),
            reverse("manufacturer-detail", kwargs={"pk": manufacturer.pk}),
            reverse("manufacturer-edit", kwargs={"pk": manufacturer.pk}),
        ]:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)


class ManufacturerOrderTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@morder.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.manufacturer = create_manufacturer(actor=self.owner, name="ACME Supplies")
        self.product = create_product(
            actor=self.owner,
            sku="MORDER-001",
            barcode="",
            name="Manufacturer Order Product",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@morder.test",
            temporary_password="TemporaryPassword123!",
            name="MOrder Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()

    def make_order(self):
        return create_manufacturer_order(
            actor=self.owner,
            manufacturer=self.manufacturer,
            items=[
                {"product": self.product, "quantity": Decimal("10"), "unit_price": Decimal("60.00")}
            ],
            tax_percentage=Decimal("5"),
            shipping_amount=Decimal("20"),
        )

    def test_creates_order_with_po_number_and_totals(self):
        order = self.make_order()

        self.assertTrue(order.po_number.startswith("MO-"))
        self.assertEqual(order.status, ManufacturerOrder.Status.SENT)
        self.assertEqual(order.subtotal, Decimal("600.00"))
        self.assertEqual(order.tax_amount, Decimal("30.00"))
        self.assertEqual(order.grand_total, Decimal("650.00"))

    def test_only_owner_can_create(self):
        with self.assertRaises(PermissionDenied):
            create_manufacturer_order(
                actor=self.distributor_user,
                manufacturer=self.manufacturer,
                items=[
                    {"product": self.product, "quantity": Decimal("1"), "unit_price": Decimal("1")}
                ],
            )

    def test_full_lifecycle_received_invoice_outcome_payment(self):
        order = self.make_order()
        item = order.items.get()

        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.RECEIVED)
        self.assertIsNotNone(order.received_at)

        from apps.owner_inventory.services import unallocated_batches

        # Nothing in inventory yet — receiving alone doesn't create stock.
        self.assertIsNone(unallocated_batches(product=item.product).first())

        # The manufacturer actually sent less than was ordered (8, not 10)
        # — the invoice step is where that correction happens.
        approved_order = record_manufacturer_invoice(
            actor=self.owner,
            order_id=order.pk,
            invoice_number="INV-123",
            item_prices={
                str(item.pk): {"quantity": Decimal("8"), "unit_price": Decimal("65.00")},
            },
        )
        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.invoice_number, "INV-123")
        self.assertIsNotNone(order.invoice_approved_at)
        self.assertEqual(order.invoice_approved_by, self.owner)
        self.assertEqual(item.unit_price, Decimal("65.00"))
        self.assertEqual(item.quantity, Decimal("8"))
        self.assertTrue(approved_order.stock_created)

        # Approving the invoice for the first time is what actually
        # creates the unallocated stock, using the corrected quantity.
        unallocated = unallocated_batches(product=item.product).first()
        self.assertIsNotNone(unallocated)
        self.assertEqual(unallocated.quantity_remaining, Decimal("8"))
        self.assertIsNone(unallocated.location.inventory_id)

        # Re-approving (editing the invoice again) must not create more.
        second_approval = record_manufacturer_invoice(
            actor=self.owner,
            order_id=order.pk,
            invoice_number="INV-123-CORRECTED",
            item_prices={
                str(item.pk): {"quantity": Decimal("8"), "unit_price": Decimal("65.00")},
            },
        )
        self.assertFalse(second_approval.stock_created)
        self.assertEqual(
            unallocated_batches(product=item.product).count(), 1
        )

        payment = record_manufacturer_payment(
            actor=self.owner,
            order_id=order.pk,
            amount=order.grand_total,
            paid_at="2026-01-01",
            note="Bank transfer",
        )
        order.refresh_from_db()
        self.assertTrue(order.is_fully_paid)
        self.assertEqual(order.payments.count(), 1)
        self.assertEqual(payment.amount, order.grand_total)

        set_manufacturer_order_outcome(
            actor=self.owner, order_id=order.pk, outcome=ManufacturerOrder.Status.GOOD,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.GOOD)

    def test_cannot_mark_received_twice(self):
        order = self.make_order()
        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)

        with self.assertRaises(ValidationError):
            mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)

    def test_outcome_requires_received_first(self):
        order = self.make_order()

        with self.assertRaises(ValidationError):
            set_manufacturer_order_outcome(
                actor=self.owner, order_id=order.pk, outcome=ManufacturerOrder.Status.GOOD,
            )

    def test_disputed_outcome_can_be_corrected(self):
        order = self.make_order()
        mark_manufacturer_order_received(actor=self.owner, order_id=order.pk)

        set_manufacturer_order_outcome(
            actor=self.owner,
            order_id=order.pk,
            outcome=ManufacturerOrder.Status.DISPUTED,
            note="Wrong quantity sent",
        )
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.DISPUTED)

        set_manufacturer_order_outcome(
            actor=self.owner, order_id=order.pk, outcome=ManufacturerOrder.Status.REFUNDED,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.REFUNDED)

        # The invoice was never approved, so the line's Batch was still
        # Pending — a refund with nothing ever received closes it out.
        item = order.items.get()
        item.batch.refresh_from_db()
        self.assertEqual(item.batch.status, "CANCELLED")

    def test_manufacturer_payment_terms_track_required_upfront(self):
        manufacturer = create_manufacturer(
            actor=self.owner,
            name="Split Terms Manufacturer",
            upfront_payment_percentage=Decimal("40"),
        )
        order = create_manufacturer_order(
            actor=self.owner,
            manufacturer=manufacturer,
            items=[
                {"product": self.product, "quantity": Decimal("10"), "unit_price": Decimal("60.00")}
            ],
        )

        self.assertEqual(order.grand_total, Decimal("600.00"))
        self.assertEqual(order.required_upfront_amount, Decimal("240.00"))
        self.assertFalse(order.upfront_amount_satisfied)

        record_manufacturer_payment(
            actor=self.owner,
            order_id=order.pk,
            amount=Decimal("240.00"),
            paid_at="2026-01-01",
        )
        order.refresh_from_db()
        self.assertTrue(order.upfront_amount_satisfied)

    def test_pages_are_connected_and_pdf_downloads(self):
        order = self.make_order()
        self.client.force_login(self.owner)

        for route in [
            reverse("manufacturer-order-list"),
            reverse("manufacturer-order-create"),
            reverse("manufacturer-order-detail", kwargs={"pk": order.pk}),
        ]:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)

        pdf_response = self.client.get(reverse("manufacturer-order-pdf", kwargs={"pk": order.pk}))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_create_form_has_no_tax_shipping_or_note_fields(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("manufacturer-order-create"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="tax_percentage"')
        self.assertNotContains(response, 'name="shipping_amount"')
        self.assertNotContains(response, 'name="note"')

    def test_distributor_cannot_access_manufacturer_order_pages(self):
        order = self.make_order()
        self.client.force_login(self.distributor_user)

        response = self.client.get(
            reverse("manufacturer-order-detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(response.status_code, 403)

    def test_full_lifecycle_via_views(self):
        order = self.make_order()
        item = order.items.get()
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("manufacturer-order-received", kwargs={"pk": order.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.RECEIVED)

        response = self.client.post(
            reverse("manufacturer-order-invoice", kwargs={"pk": order.pk}),
            {
                "invoice_number": "INV-999",
                f"item_qty_{item.pk}": "10",
                f"item_price_{item.pk}": "62.50",
                f"item_expiry_{item.pk}": "2027-01-01",
            },
        )
        # First approval redirects straight to allocating the new stock.
        self.assertRedirects(response, reverse("inventory-list"))

        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.invoice_number, "INV-999")
        self.assertEqual(item.unit_price, Decimal("62.50"))

        item.batch.refresh_from_db()
        self.assertEqual(item.batch.status, "RECEIVED")
        self.assertEqual(str(item.batch.expiry_date), "2027-01-01")

        from apps.owner_inventory.services import unallocated_batches

        created_batch = unallocated_batches(product=item.product).first()
        self.assertEqual(created_batch.quantity_remaining, Decimal("10"))
        self.assertEqual(created_batch.batch_number, item.batch.code)
        self.assertEqual(created_batch.source_batch_id, item.batch.pk)

        # Regression: the invoice number must show up pre-filled next time
        # the page loads, not appear blank even though it saved fine.
        detail_response = self.client.get(
            reverse("manufacturer-order-detail", kwargs={"pk": order.pk})
        )
        self.assertContains(detail_response, 'value="INV-999"')

        response = self.client.post(
            reverse("manufacturer-order-payment", kwargs={"pk": order.pk}),
            {"amount": "100.00", "paid_at": "2026-01-01", "note": "Partial"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payments.count(), 1)

        response = self.client.post(
            reverse("manufacturer-order-outcome", kwargs={"pk": order.pk}),
            {"outcome": "GOOD"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.GOOD)


class ManufacturerApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@manufacturer-api.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def test_manufacturer_api_creates_manufacturer(self):
        response = self.api.post(
            reverse("api-manufacturer-list"),
            {"name": "API Manufacturer"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            Manufacturer.objects.filter(name="API Manufacturer").exists()
        )
