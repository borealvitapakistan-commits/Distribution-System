import shutil
import tempfile
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.distributors.services import approve_distributor, create_distributor
from apps.products.services import create_product

from .models import Manufacturer, ManufacturerOrder, ManufacturerOrderPayment
from .services import (
    create_manufacturer,
    create_manufacturer_order,
    mark_manufacturer_order_received,
    receive_manufacturer_order,
    record_advance_decision,
    record_manufacturer_invoice,
    record_manufacturer_payment,
    set_manufacturer_order_outcome,
)

MEDIA_ROOT = tempfile.mkdtemp()


def proof_file(name="proof.png"):
    return SimpleUploadedFile(name, b"proof-bytes", content_type="image/png")


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


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


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
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
            proof=proof_file(),
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
            proof=proof_file(),
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

        # Receiving, the invoice and the payment answer are one page.
        response = self.client.post(
            reverse("manufacturer-order-received", kwargs={"pk": order.pk}),
            {
                f"item_qty_{item.pk}": "10",
                f"item_price_{item.pk}": "62.50",
                f"item_expiry_{item.pk}": "2027-01-01",
                "answer": "no",
            },
        )
        # Approving the invoice redirects straight to allocating the new stock.
        self.assertRedirects(response, reverse("inventory-list"))

        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.RECEIVED)
        item.refresh_from_db()
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
        self.assertNotContains(detail_response, "Save &amp; Approve Invoice")

        response = self.client.post(
            reverse("manufacturer-order-payment", kwargs={"pk": order.pk}),
            {
                "amount": "100.00",
                "paid_at": "2026-01-01",
                "proof": proof_file(),
                "note": "Partial",
            },
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


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ManufacturerPaymentFlowTests(TestCase):
    """Place order → answer the advance → receive and pay the rest, in
    each of the three ways an order can be paid for."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@mpay.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.manufacturer = create_manufacturer(
            actor=self.owner,
            name="Split Pay Manufacturer",
            upfront_payment_percentage=Decimal("30"),
        )
        self.product = create_product(
            actor=self.owner,
            sku="MPAY-001",
            barcode="",
            name="Payment Flow Product",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        # Grand total 1,000.00
        self.order = create_manufacturer_order(
            actor=self.owner,
            manufacturer=self.manufacturer,
            items=[
                {"product": self.product, "quantity": Decimal("10"), "unit_price": Decimal("100.00")}
            ],
        )

    def _refresh(self):
        self.order.refresh_from_db()
        return self.order

    def test_full_advance_leaves_nothing_to_pay_on_receipt(self):
        record_advance_decision(
            actor=self.owner,
            order_id=self.order.pk,
            pays_advance=True,
            amount=Decimal("1000.00"),
            paid_at="2026-01-01",
            proof=proof_file(),
        )
        receive_manufacturer_order(actor=self.owner, order_id=self.order.pk)

        order = self._refresh()
        self.assertEqual(order.status, ManufacturerOrder.Status.RECEIVED)
        self.assertEqual(order.advance_paid, Decimal("1000.00"))
        self.assertEqual(order.final_paid, Decimal("0.00"))
        self.assertEqual(order.remaining_amount, Decimal("0.00"))
        self.assertEqual(order.payment_scenario, "100% paid in advance")

    def test_no_advance_pays_everything_on_receipt(self):
        record_advance_decision(actor=self.owner, order_id=self.order.pk, pays_advance=False)
        self.assertIs(self._refresh().pays_advance, False)
        self.assertEqual(self.order.payments.count(), 0)

        receive_manufacturer_order(
            actor=self.owner,
            order_id=self.order.pk,
            paid_remaining=True,
            amount=Decimal("1000.00"),
            paid_at="2026-02-01",
            proof=proof_file(),
        )

        order = self._refresh()
        self.assertEqual(order.final_paid, Decimal("1000.00"))
        self.assertTrue(order.is_fully_paid)
        self.assertEqual(order.payment_scenario, "No advance — paid in full on receipt")

    def test_split_advance_and_final_each_keep_their_proof(self):
        record_advance_decision(
            actor=self.owner,
            order_id=self.order.pk,
            pays_advance=True,
            amount=Decimal("300.00"),
            paid_at="2026-01-01",
            proof=proof_file("advance.png"),
        )
        receive_manufacturer_order(
            actor=self.owner,
            order_id=self.order.pk,
            paid_remaining=True,
            amount=Decimal("700.00"),
            paid_at="2026-02-01",
            proof=proof_file("final.png"),
        )

        order = self._refresh()
        self.assertEqual(order.advance_percentage, Decimal("30.0"))
        self.assertEqual(order.final_percentage, Decimal("70.0"))
        self.assertEqual(order.payment_scenario, "30.0% advance + 70.0% on receipt")

        kinds = {payment.kind: payment for payment in order.payments.all()}
        self.assertIn("advance", kinds[ManufacturerOrderPayment.Kind.ADVANCE].proof.name)
        self.assertIn("final", kinds[ManufacturerOrderPayment.Kind.FINAL].proof.name)

    def test_payment_needs_proof_and_cannot_overpay(self):
        with self.assertRaises(ValidationError):
            record_advance_decision(
                actor=self.owner,
                order_id=self.order.pk,
                pays_advance=True,
                amount=Decimal("100.00"),
                paid_at="2026-01-01",
                proof=None,
            )

        with self.assertRaises(ValidationError):
            record_advance_decision(
                actor=self.owner,
                order_id=self.order.pk,
                pays_advance=True,
                amount=Decimal("1000.01"),
                paid_at="2026-01-01",
                proof=proof_file(),
            )

        # Neither failed attempt left the question answered.
        self.assertIsNone(self._refresh().pays_advance)

    def test_advance_is_answered_only_once(self):
        record_advance_decision(actor=self.owner, order_id=self.order.pk, pays_advance=False)

        with self.assertRaises(ValidationError):
            record_advance_decision(actor=self.owner, order_id=self.order.pk, pays_advance=False)

    def test_pages_walk_through_place_advance_receive(self):
        from apps.core.models import Brand

        brand = Brand.objects.create(name="Flow Brand")
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("manufacturer-order-create"),
            {
                "manufacturer": self.manufacturer.pk,
                "brand": brand.pk,
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-product": self.product.pk,
                "items-0-quantity": "5",
                "items-0-unit_price": "200",
            },
        )

        order = ManufacturerOrder.objects.exclude(pk=self.order.pk).get()
        advance_url = reverse("manufacturer-order-advance", kwargs={"pk": order.pk})
        self.assertRedirects(response, advance_url)

        page = self.client.get(advance_url)
        self.assertContains(page, "Are you paying in advance?")
        self.assertContains(page, "Full amount (100%)")

        # "Yes" without the details is sent back with errors.
        response = self.client.post(advance_url, {"answer": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Required when you&#x27;ve paid.")

        response = self.client.post(
            advance_url,
            {"answer": "yes", "amount": "400", "paid_at": "2026-01-01", "proof": proof_file()},
        )
        self.assertRedirects(
            response, reverse("manufacturer-order-detail", kwargs={"pk": order.pk})
        )

        receive_url = reverse("manufacturer-order-received", kwargs={"pk": order.pk})
        page = self.client.get(receive_url)
        self.assertContains(page, "Have you paid the remaining amount?")
        self.assertContains(page, "Goods received")
        self.assertNotContains(page, "Invoice number")
        self.assertNotContains(page, "Invoice file")

        item = order.items.get()
        invoice = {
            f"item_qty_{item.pk}": "5",
            f"item_price_{item.pk}": "200",
        }

        # Medicines: a batch can't go into Inventory without its expiry.
        response = self.client.post(receive_url, {**invoice, "answer": "no"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.SENT)

        invoice[f"item_expiry_{item.pk}"] = "2028-06-30"

        # Something is still owed, so the question must be answered.
        response = self.client.post(receive_url, invoice)
        self.assertContains(response, "Please answer")
        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.SENT)

        response = self.client.post(
            receive_url,
            {
                **invoice,
                "answer": "yes",
                "amount": "600",
                "paid_at": "2026-02-01",
                "proof": proof_file(),
            },
        )
        self.assertRedirects(response, reverse("inventory-list"))

        order.refresh_from_db()
        self.assertEqual(order.status, ManufacturerOrder.Status.RECEIVED)
        self.assertIsNotNone(order.invoice_approved_at)
        self.assertEqual(order.advance_paid, Decimal("400.00"))
        self.assertEqual(order.final_paid, Decimal("600.00"))

        detail = self.client.get(reverse("manufacturer-order-detail", kwargs={"pk": order.pk}))
        self.assertContains(detail, "40.0% advance + 60.0% on receipt")
        self.assertContains(detail, "View proof", count=2)

    def test_final_payment_follows_the_invoiced_total(self):
        """The invoice lowers the cost from 1,000 to 800 — with 300 paid in
        advance, only 500 is left, and paying more is refused."""
        record_advance_decision(
            actor=self.owner,
            order_id=self.order.pk,
            pays_advance=True,
            amount=Decimal("300.00"),
            paid_at="2026-01-01",
            proof=proof_file(),
        )
        item = self.order.items.get()
        invoiced = {str(item.pk): {"quantity": Decimal("8"), "unit_price": Decimal("100.00")}}

        with self.assertRaises(ValidationError):
            receive_manufacturer_order(
                actor=self.owner,
                order_id=self.order.pk,
                item_prices=invoiced,
                paid_remaining=True,
                amount=Decimal("700.00"),
                paid_at="2026-02-01",
                proof=proof_file(),
            )

        # All-or-nothing: the refused payment undid the receipt and invoice too.
        order = self._refresh()
        self.assertEqual(order.status, ManufacturerOrder.Status.SENT)
        self.assertIsNone(order.invoice_approved_at)

        order = receive_manufacturer_order(
            actor=self.owner,
            order_id=self.order.pk,
            item_prices=invoiced,
            paid_remaining=True,
            amount=Decimal("500.00"),
            paid_at="2026-02-01",
            proof=proof_file(),
        )
        self.assertTrue(order.stock_created)

        order = self._refresh()
        self.assertEqual(order.grand_total, Decimal("800.00"))
        self.assertEqual(order.remaining_amount, Decimal("0.00"))

    def test_received_but_never_invoiced_order_can_still_be_invoiced(self):
        mark_manufacturer_order_received(actor=self.owner, order_id=self.order.pk)
        item = self.order.items.get()

        self.client.force_login(self.owner)
        receive_url = reverse("manufacturer-order-received", kwargs={"pk": self.order.pk})
        page = self.client.get(receive_url)
        self.assertContains(page, "Add Stock to Inventory")

        response = self.client.post(
            receive_url,
            {
                f"item_qty_{item.pk}": "10",
                f"item_price_{item.pk}": "100",
                f"item_expiry_{item.pk}": "2028-06-30",
                "answer": "no",
            },
        )
        self.assertRedirects(response, reverse("inventory-list"))
        self.assertIsNotNone(self._refresh().invoice_approved_at)

        # Nothing left to do — the page sends you back to the order.
        response = self.client.get(receive_url)
        self.assertRedirects(
            response, reverse("manufacturer-order-detail", kwargs={"pk": self.order.pk})
        )


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class BatchIdentityTests(TestCase):
    """The same product ordered on two days is two separate batches —
    its own code, quantity and expiry — never one merged quantity."""

    def setUp(self):
        from apps.core.models import Brand

        self.owner = User.objects.create_user(
            email="owner@identity.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.brand = Brand.objects.create(name="Boreal Vita")
        self.manufacturer = create_manufacturer(actor=self.owner, name="Identity Manufacturer")
        self.product = create_product(
            actor=self.owner,
            sku="ASHW-500",
            barcode="",
            name="Ashwagandha Root Extract",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )

    def _order_and_receive(self, quantity, expiry):
        order = create_manufacturer_order(
            actor=self.owner,
            manufacturer=self.manufacturer,
            brand=self.brand,
            items=[{"product": self.product, "quantity": Decimal(quantity), "unit_price": Decimal("50.00")}],
        )
        item = order.items.get()
        receive_manufacturer_order(
            actor=self.owner,
            order_id=order.pk,
            item_prices={str(item.pk): {
                "quantity": Decimal(quantity),
                "unit_price": Decimal("50.00"),
                "expiry_date": expiry,
            }},
        )
        return item.batch

    def test_same_product_on_two_orders_stays_two_batches(self):
        from datetime import date

        from apps.owner_inventory.models import StockBatch

        yesterday = self._order_and_receive("100", date(2027, 3, 31))
        today = self._order_and_receive("40", date(2028, 1, 31))

        self.assertNotEqual(yesterday.code, today.code)

        lots = {
            lot.batch_number: (lot.quantity_remaining, lot.expiry_date)
            for lot in StockBatch.objects.filter(product=self.product)
        }
        self.assertEqual(
            lots,
            {
                yesterday.code: (Decimal("100"), date(2027, 3, 31)),
                today.code: (Decimal("40"), date(2028, 1, 31)),
            },
        )

        # The stock page lists each batch on its own row, not one total.
        self.client.force_login(self.owner)
        response = self.client.get(reverse("stock-balance-list"))
        rows = list(response.context["batches"])
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.batch_number for row in rows], [yesterday.code, today.code])
        self.assertNotContains(response, "140")


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
