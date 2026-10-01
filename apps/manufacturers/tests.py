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
    create_vendor,
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
        vendor = create_vendor(actor=self.owner, name="Flow Vendor")
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("manufacturer-order-create"),
            {
                "vendor": vendor.pk,
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

        # Step one is a Request to Quote: nothing is stocked yet.
        order = ManufacturerOrder.objects.exclude(pk=self.order.pk).get()
        detail_url = reverse("manufacturer-order-detail", kwargs={"pk": order.pk})
        self.assertRedirects(response, detail_url)
        self.assertEqual(order.status, ManufacturerOrder.Status.QUOTE)

        item = order.items.get()
        response = self.client.post(
            reverse("manufacturer-order-quote", kwargs={"pk": order.pk}),
            {
                "quote_file": proof_file("quote.png"),
                f"quote_price_{item.pk}": "200",
                f"quote_qty_{item.pk}": "5",
            },
        )
        self.assertRedirects(response, detail_url)

        advance_url = reverse("manufacturer-order-advance", kwargs={"pk": order.pk})
        response = self.client.post(
            reverse("manufacturer-order-confirm", kwargs={"pk": order.pk})
        )
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
        # The Manufacturer's invoice is recorded here too.
        self.assertContains(page, "Invoice number")
        self.assertContains(page, "Invoice document")

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


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class RequestToQuoteFlowTests(TestCase):
    """Request to Quote → Manufacturer's quote → Purchase Order."""

    def setUp(self):
        from apps.core.models import Brand
        from apps.products.models import BottleSize
        from apps.products.services import save_bottle_price

        self.owner = User.objects.create_user(
            email="owner@rtq.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.brand = Brand.objects.create(name="Herbal Aid", primary_color="#1f7a4d")
        self.manufacturer = create_manufacturer(actor=self.owner, name="Glorious Labs")
        self.vendor = create_vendor(
            actor=self.owner, name="Middleman Traders", email="buy@middleman.test",
            phone="0300-1234567", address="Shop 4, Main Market",
        )
        self.ashwagandha = create_product(
            actor=self.owner, sku="RTQ-ASH", barcode="", name="Ashwagandha",
            base_retail_price=Decimal("100"), currency="pkr",
        )
        self.vitamin_d = create_product(
            actor=self.owner, sku="RTQ-D3", barcode="", name="Vitamin D3",
            base_retail_price=Decimal("100"), currency="pkr",
        )
        save_bottle_price(
            actor=self.owner, product=self.ashwagandha,
            bottle_size=BottleSize.CAPS_60, price=Decimal("10"),
        )
        save_bottle_price(
            actor=self.owner, product=self.vitamin_d,
            bottle_size=BottleSize.CAPS_60, price=Decimal("10"),
        )

    def make_quote(self):
        from .services import create_request_to_quote

        return create_request_to_quote(
            actor=self.owner,
            vendor=self.vendor,
            manufacturer=self.manufacturer,
            brand=self.brand,
            terms="50% advance.",
            items=[
                {"product": self.ashwagandha, "bottle_size": 60, "quantity": "100", "unit_price": "10"},
                {"product": self.ashwagandha, "bottle_size": 120, "quantity": "50", "unit_price": None},
                {"product": self.vitamin_d, "bottle_size": 60, "quantity": "100", "unit_price": "10"},
            ],
        )

    def lines(self, order):
        return {(item.product_id, item.bottle_size): item for item in order.items.all()}

    def test_quote_has_optional_prices_and_no_batches(self):
        order = self.make_quote()

        self.assertEqual(order.status, ManufacturerOrder.Status.QUOTE)
        self.assertEqual(order.document_title, "Request to Quote")
        self.assertEqual(order.items.count(), 3)
        unpriced = self.lines(order)[(self.ashwagandha.pk, 120)]
        self.assertIsNone(unpriced.unit_price)
        self.assertEqual(order.subtotal, Decimal("2000.00"))
        self.assertFalse(any(item.price_changed for item in order.items.all()))
        self.assertFalse(order.items.filter(batch__isnull=False).exists())

    def test_same_product_and_size_twice_is_rejected(self):
        from .services import create_request_to_quote

        with self.assertRaises(ValidationError):
            create_request_to_quote(
                actor=self.owner,
                vendor=self.vendor,
                manufacturer=self.manufacturer,
                items=[
                    {"product": self.ashwagandha, "bottle_size": 60, "quantity": "1"},
                    {"product": self.ashwagandha, "bottle_size": 60, "quantity": "2"},
                ],
            )

    def test_quote_highlights_changes_and_saves_only_chosen_prices(self):
        from apps.products.models import ProductBottlePrice

        from .services import record_manufacturer_quote

        order = self.make_quote()
        lines = self.lines(order)
        ash60 = lines[(self.ashwagandha.pk, 60)]
        ash120 = lines[(self.ashwagandha.pk, 120)]
        d3 = lines[(self.vitamin_d.pk, 60)]

        # No document, no quote.
        with self.assertRaises(ValidationError):
            record_manufacturer_quote(
                actor=self.owner, order_id=order.pk,
                item_updates={str(ash60.pk): {"unit_price": Decimal("20")}},
            )

        record_manufacturer_quote(
            actor=self.owner,
            order_id=order.pk,
            quote_file=proof_file("quote.png"),
            item_updates={
                str(ash60.pk): {"unit_price": Decimal("20"), "quantity": Decimal("100")},
                str(ash120.pk): {"unit_price": Decimal("30"), "quantity": Decimal("50")},
                str(d3.pk): {"unit_price": Decimal("10"), "quantity": Decimal("100")},
            },
            save_price_item_ids=[ash60.pk],
        )

        order.refresh_from_db()
        lines = self.lines(order)
        self.assertTrue(lines[(self.ashwagandha.pk, 60)].price_changed)
        self.assertTrue(lines[(self.ashwagandha.pk, 120)].price_changed)
        self.assertFalse(lines[(self.vitamin_d.pk, 60)].price_changed)
        self.assertIsNotNone(order.quoted_at)

        saved = {
            (row.product_id, row.bottle_size): row.price
            for row in ProductBottlePrice.objects.all()
        }
        self.assertEqual(saved[(self.ashwagandha.pk, 60)], Decimal("20"))
        self.assertNotIn((self.ashwagandha.pk, 120), saved)
        self.assertEqual(saved[(self.vitamin_d.pk, 60)], Decimal("10"))

    def test_confirm_needs_quote_then_creates_batches(self):
        from .services import confirm_purchase_order, record_manufacturer_quote

        order = self.make_quote()

        with self.assertRaises(ValidationError):
            confirm_purchase_order(actor=self.owner, order_id=order.pk)

        with self.assertRaises(ValidationError):
            record_manufacturer_payment(
                actor=self.owner, order_id=order.pk, amount="1",
                paid_at="2026-01-01", proof=proof_file(),
            )

        ash120 = self.lines(order)[(self.ashwagandha.pk, 120)]
        record_manufacturer_quote(
            actor=self.owner,
            order_id=order.pk,
            quote_file=proof_file("quote.png"),
            item_updates={
                str(item.pk): {"unit_price": Decimal("12")} for item in order.items.all()
            },
            remove_item_ids=[ash120.pk],
        )

        order = confirm_purchase_order(actor=self.owner, order_id=order.pk)

        self.assertEqual(order.status, ManufacturerOrder.Status.SENT)
        self.assertIsNotNone(order.po_sent_at)
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(order.items.filter(batch__isnull=False).count(), 2)
        self.assertEqual(order.subtotal, Decimal("2400.00"))

        with self.assertRaises(ValidationError):
            confirm_purchase_order(actor=self.owner, order_id=order.pk)

    def test_pages_and_pdfs(self):
        from .services import record_manufacturer_quote

        order = self.make_quote()
        self.client.force_login(self.owner)

        create_page = self.client.get(reverse("manufacturer-order-create"))
        self.assertContains(create_page, 'id="moBottlePrices"')
        self.assertContains(create_page, "60 capsules")

        for route in ["manufacturer-order-detail", "manufacturer-order-edit", "manufacturer-order-quote"]:
            with self.subTest(route=route):
                response = self.client.get(reverse(route, kwargs={"pk": order.pk}))
                self.assertEqual(response.status_code, 200)

        for query in ["", "?copy=team"]:
            response = self.client.get(
                reverse("manufacturer-order-pdf", kwargs={"pk": order.pk}) + query
            )
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertIn("RTQ-", response["Content-Disposition"])

        record_manufacturer_quote(
            actor=self.owner,
            order_id=order.pk,
            quote_file=proof_file("quote.png"),
            item_updates={
                str(item.pk): {"unit_price": Decimal("20")} for item in order.items.all()
            },
        )

        # Once the quote is in, the request itself is locked.
        response = self.client.get(reverse("manufacturer-order-edit", kwargs={"pk": order.pk}))
        self.assertRedirects(response, reverse("manufacturer-order-detail", kwargs={"pk": order.pk}))

        detail = self.client.get(reverse("manufacturer-order-detail", kwargs={"pk": order.pk}))
        self.assertContains(detail, "mo-row-changed")
        self.assertContains(detail, "Send Purchase Order")

        list_page = self.client.get(reverse("manufacturer-order-list") + "?status=QUOTE&q=Glorious")
        self.assertContains(list_page, order.po_number)
        list_page = self.client.get(reverse("manufacturer-order-list") + "?status=GOOD")
        self.assertNotContains(list_page, order.po_number)

    def test_product_page_saves_bottle_prices(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("product-bottle-prices", kwargs={"pk": self.ashwagandha.pk}),
            {"price_30": "5.5", "price_60": "", "price_90": "", "price_120": "15.250"},
        )
        self.assertRedirects(response, reverse("product-detail", kwargs={"pk": self.ashwagandha.pk}))

        prices = {row.bottle_size: row.price for row in self.ashwagandha.bottle_prices.all()}
        self.assertEqual(prices, {30: Decimal("5.5"), 120: Decimal("15.25")})

    def test_brand_colour_must_be_a_hex_code(self):
        self.brand.primary_color = "green"
        with self.assertRaises(ValidationError):
            self.brand.full_clean()

        self.brand.primary_color = "#000000"
        self.assertEqual(self.brand.tinted_color(1), "#ffffff")

    def test_vendor_is_required_and_shown(self):
        from .services import create_request_to_quote

        with self.assertRaises(ValidationError):
            create_request_to_quote(
                actor=self.owner,
                vendor=None,
                manufacturer=self.manufacturer,
                items=[{"product": self.ashwagandha, "bottle_size": 60, "quantity": "1"}],
            )

        order = self.make_quote()
        self.assertEqual(order.vendor, self.vendor)
        self.client.force_login(self.owner)

        detail = self.client.get(reverse("manufacturer-order-detail", kwargs={"pk": order.pk}))
        self.assertContains(detail, "Middleman Traders")
        self.assertContains(detail, "Glorious Labs")

        list_page = self.client.get(reverse("manufacturer-order-list") + "?q=Middleman")
        self.assertContains(list_page, order.po_number)

        vendor_page = self.client.get(reverse("vendor-detail", kwargs={"pk": self.vendor.pk}))
        self.assertContains(vendor_page, order.po_number)

    def test_vendor_pages(self):
        from .models import Vendor

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("vendor-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("vendor-create")).status_code, 200)

        response = self.client.post(
            reverse("vendor-create"),
            {"name": "New Vendor", "email": "v@vendor.test", "phone": "123",
             "address": "Lahore", "notes": "", "active": "on"},
        )
        vendor = Vendor.objects.get(name="New Vendor")
        self.assertRedirects(response, reverse("vendor-detail", kwargs={"pk": vendor.pk}))

        response = self.client.post(
            reverse("vendor-edit", kwargs={"pk": vendor.pk}),
            {"name": "Renamed Vendor", "email": "v@vendor.test", "phone": "123",
             "address": "Lahore", "notes": "", "active": "on"},
        )
        vendor.refresh_from_db()
        self.assertEqual(vendor.name, "Renamed Vendor")

    def test_document_groups_bottle_sizes_into_price_columns(self):
        from .views import document_table

        order = self.make_quote()
        columns, rows = document_table(list(order.items.all()))

        self.assertEqual([c["label"] for c in columns], ["Unit price (60 caps)", "Unit price (120 caps)"])
        self.assertEqual([row["product"].name for row in rows], ["Ashwagandha", "Vitamin D3"])

        ashwagandha = rows[0]
        # 100 x 60-cap and 50 x 120-cap: quantities differ, so each cell shows its own.
        self.assertIsNone(ashwagandha["quantity"])
        self.assertTrue(ashwagandha["cells"][0]["show_quantity"])
        self.assertEqual(ashwagandha["total"], Decimal("1000.00"))

        vitamin_d = rows[1]
        self.assertEqual(vitamin_d["quantity"], Decimal("100"))
        self.assertIsNone(vitamin_d["cells"][1]["item"])
