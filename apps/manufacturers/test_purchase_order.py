from decimal import Decimal
from unittest import mock

from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Brand
from apps.products.models import ProductPackagePrice
from apps.products.services import create_product, save_manufacturer_price, set_product_manufacturers

from .models import ManufacturerOrder
from .services import create_manufacturer, update_manufacturer
from .views import default_payment_terms


class DirectPurchaseOrderTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="po@test.local", role="OWNER")
        self.brand = Brand.objects.create(name="Boreal Vita")
        self.glorious = create_manufacturer(actor=self.owner, name="Glorious", upfront_payment_percentage=30)
        self.other = create_manufacturer(actor=self.owner, name="Other Labs")
        self.mine = create_product(actor=self.owner, name="Ashwagandha", sku="PO-ASH")
        self.theirs = create_product(actor=self.owner, name="Moringa", sku="PO-MOR")
        set_product_manufacturers(actor=self.owner, product=self.mine, manufacturers=[self.glorious])
        set_product_manufacturers(actor=self.owner, product=self.theirs, manufacturers=[self.other])
        save_manufacturer_price(actor=self.owner, product=self.mine, manufacturer=self.glorious,
                                amount=60, price=Decimal("1650"))
        self.client.force_login(self.owner)
        self.url = reverse("manufacturer-po-create")

    def data(self, product, size="60", price="1650", **extra):
        return {
            "manufacturer": self.glorious.pk, "brand": self.brand.pk,
            "terms": "30% advance, 70% balance on completion of production.", "message": "Deliver to Lahore.",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
            "items-0-product": product.pk, "items-0-bottle_size": size,
            "items-0-quantity": "200", "items-0-unit_price": price, **extra,
        }

    def test_page_offers_payment_terms_and_allocations(self):
        response = self.client.get(self.url, {"manufacturer": self.glorious.pk})
        self.assertContains(response, "Save Purchase Order")
        self.assertEqual(response.context["advance_defaults"][str(self.glorious.pk)], "30")
        self.assertIn("70% balance on completion of production", default_payment_terms(self.glorious))
        allocated = response.context["manufacturer_prices"]["allocated"]
        self.assertEqual(allocated[str(self.glorious.pk)], [str(self.mine.pk)])

    def test_creates_a_purchase_order_with_terms_notes_and_any_size(self):
        response = self.client.post(self.url, self.data(self.mine, size="240", price="5000",
                                                        **{"items-0-save_price": "on"}))
        order = ManufacturerOrder.objects.get()
        self.assertRedirects(response, reverse("manufacturer-order-detail", args=[order.pk]))
        self.assertEqual(order.status, ManufacturerOrder.Status.SENT)
        self.assertEqual(order.terms, "30% advance, 70% balance on completion of production.")
        self.assertEqual(order.vendor_name, "Glorious")
        item = order.items.get()
        self.assertEqual((item.bottle_size, item.unit_price), (240, Decimal("5000")))
        # "Remember this price" saved the new 240-capsule price for Glorious.
        self.assertEqual(ProductPackagePrice.objects.get(manufacturer=self.glorious, amount=240).price, Decimal("5000"))

    def test_refuses_products_not_allocated_to_the_manufacturer(self):
        response = self.client.post(self.url, self.data(self.theirs))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not allocated to Glorious: Moringa")
        self.assertFalse(ManufacturerOrder.objects.exists())

    def test_every_line_needs_a_price(self):
        response = self.client.post(self.url, self.data(self.mine, price=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ManufacturerOrder.objects.exists())

    def test_pdf_has_terms_notes_and_stamp(self):
        import io

        from pypdf import PdfReader

        self.client.post(self.url, self.data(self.mine))
        order = ManufacturerOrder.objects.get()
        response = self.client.get(reverse("manufacturer-order-pdf", args=[order.pk]), {"download": "1"})
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f"PO-{order.po_number}.pdf", response["Content-Disposition"])
        text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
        for expected in ("Purchase Order", "Terms and conditions", "30% advance", "Notes", "Deliver to Lahore.",
                         "Company stamp"):
            self.assertIn(expected, text)

    def test_default_terms_follow_the_advance_percentage(self):
        self.assertEqual(default_payment_terms(self.other), "Full payment after receiving the products.")
        update_manufacturer(actor=self.owner, manufacturer_id=self.other.pk, upfront_payment_percentage=100)
        self.other.refresh_from_db()
        self.assertEqual(default_payment_terms(self.other), "100% advance payment before production.")


class PurchaseOrderDocumentTests(TestCase):
    def setUp(self):
        from apps.products.models import ProductCategory

        self.owner = User.objects.create_user(email="doc@test.local", role="OWNER")
        self.brand = Brand.objects.create(name="Boreal Vita", phone="+92 331 6504749")
        self.maker = create_manufacturer(actor=self.owner, name="Glorious", upfront_payment_percentage=30)
        powder, _ = ProductCategory.objects.get_or_create(name="Powder")
        self.creatine = create_product(actor=self.owner, name="Creatine Max", sku="DOC-CRE",
                                       category=powder, unit_of_measure="GRAM")
        self.ashwa = create_product(actor=self.owner, name="Ashwagandha", sku="DOC-ASH")
        for product in (self.creatine, self.ashwa):
            set_product_manufacturers(actor=self.owner, product=product, manufacturers=[self.maker])
        self.client.force_login(self.owner)

    def make_order(self, **extra):
        from .services import create_manufacturer_order

        return create_manufacturer_order(
            actor=self.owner, manufacturer=self.maker, brand=self.brand, require_allocated=True,
            items=[{"product": self.creatine, "bottle_size": 300, "quantity": "1000", "unit_price": "750"},
                   {"product": self.ashwa, "bottle_size": 30, "quantity": "1000", "unit_price": "343"}],
            **extra,
        )

    def test_lines_show_only_the_size_and_totals_show_advance_and_words(self):
        order = self.make_order()
        self.assertEqual(order.advance_percent, Decimal("30"))
        page = self.client.get(reverse("manufacturer-order-detail", args=[order.pk]))
        for text in ("300 g", "30 capsules", "Advance (30%)", "327,900.00", "Balance (70%)", "765,100.00",
                     "Rupees Ten Lakh Ninety-Three Thousand Only", "Phone: +92 331 6504749"):
            self.assertContains(page, text)
        codes = [item.batch.code for item in order.items.select_related("batch")]
        for text in ("DOC-CRE", "(pending)", *codes):
            self.assertNotContains(page, text)

    def test_advance_can_be_set_per_order(self):
        order = self.make_order(advance_percent=50)
        self.assertEqual(order.advance_amount, Decimal("546500.00"))
        self.assertEqual(order.balance_amount, Decimal("546500.00"))

    def test_in_progress_order_can_be_deleted_with_its_batches(self):
        from apps.batches.models import Batch

        order = self.make_order()
        self.assertEqual(Batch.objects.count(), 2)
        response = self.client.post(reverse("manufacturer-order-delete", args=[order.pk]))
        self.assertRedirects(response, reverse("manufacturer-order-list"))
        self.assertFalse(ManufacturerOrder.objects.exists())
        self.assertFalse(Batch.objects.exists())

    def test_order_with_a_payment_cannot_be_deleted(self):
        from .models import ManufacturerOrderPayment

        order = self.make_order()
        ManufacturerOrderPayment.objects.create(order=order, amount=Decimal("1000"), kind="ADVANCE",
                                                paid_at="2026-10-23")
        response = self.client.post(reverse("manufacturer-order-delete", args=[order.pk]), follow=True)
        self.assertContains(response, "Payments are recorded on this order")
        self.assertTrue(ManufacturerOrder.objects.filter(pk=order.pk).exists())
        self.assertNotContains(self.client.get(reverse("manufacturer-order-detail", args=[order.pk])),
                               "manufacturer-orders/%s/delete/" % order.pk)


class AdvanceBalancePagesTests(PurchaseOrderDocumentTests):
    def pages(self, order, **kwargs):
        from .record_pdf import payment_pages

        return payment_pages(order, **kwargs)

    def test_advance_order_prints_an_advance_page_and_a_balance_page(self):
        import io

        from pypdf import PdfReader

        order = self.make_order()
        advance, balance = self.pages(order)
        self.assertEqual((advance["part"], advance["now_amount"]), ("advance", Decimal("327900.00")))
        self.assertEqual((balance["part"], balance["now_amount"]), ("balance", Decimal("765100.00")))
        self.assertNotIn("advance", balance["statement"].lower())
        self.assertNotIn("balance", advance["statement"].lower())

        response = self.client.get(reverse("manufacturer-order-pdf", args=[order.pk]))
        reader = PdfReader(io.BytesIO(response.content))
        self.assertEqual(len(reader.pages), 2)
        self.assertIn("Advance payment", reader.pages[0].extract_text())
        self.assertIn("Balance payment", reader.pages[1].extract_text())

        one = self.client.get(reverse("manufacturer-order-pdf", args=[order.pk]), {"part": "balance", "download": "1"})
        self.assertEqual(len(PdfReader(io.BytesIO(one.content)).pages), 1)
        self.assertIn("-balance.pdf", one["Content-Disposition"])

    def test_balance_uses_the_advance_actually_paid(self):
        from .models import ManufacturerOrderPayment

        order = self.make_order()
        ManufacturerOrderPayment.objects.create(order=order, amount=Decimal("300000"), kind="ADVANCE",
                                                paid_at="2026-10-20")
        balance = self.pages(order, part="balance")[0]
        self.assertEqual(balance["now_amount"], Decimal("793000.00"))
        self.assertIn("Rs 793,000.00", balance["statement"])

    def test_no_advance_or_full_advance_stays_one_page(self):
        for percent in (0, 100):
            self.assertEqual(self.pages(self.make_order(advance_percent=percent)), [{"part": None}])


class DownloadChoiceTests(PurchaseOrderDocumentTests):
    def test_zip_holds_the_advance_and_balance_pdfs(self):
        import io
        import zipfile

        order = self.make_order()
        response = self.client.get(reverse("manufacturer-order-pdf", args=[order.pk]), {"download": "1", "format": "zip"})
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn(f"PO-{order.po_number}.zip", response["Content-Disposition"])
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        self.assertEqual(names, [f"PO-{order.po_number}-advance.pdf", f"PO-{order.po_number}-balance.pdf"])

    def test_chooser_only_shows_for_two_page_orders(self):
        split = self.make_order()
        self.assertContains(self.client.get(reverse("manufacturer-order-detail", args=[split.pk])), "data-download-choice")
        single = self.make_order(advance_percent=0)
        self.assertNotContains(self.client.get(reverse("manufacturer-order-detail", args=[single.pk])), "data-download-choice")
        response = self.client.get(reverse("manufacturer-order-pdf", args=[single.pk]), {"format": "zip"})
        self.assertEqual(response["Content-Type"], "application/pdf")


class StampOnlyFooterTests(PurchaseOrderDocumentTests):
    def page_texts(self, order, **params):
        import io

        from pypdf import PdfReader

        response = self.client.get(reverse("manufacturer-order-pdf", args=[order.pk]), params)
        return [page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages]

    def test_footer_has_only_the_company_stamp(self):
        text = " ".join(self.page_texts(self.make_order()))
        self.assertIn("Company stamp", text)
        for gone in ("Authorised signature", "Name:", "Date:"):
            self.assertNotIn(gone, text)

    def test_long_order_stamps_only_the_last_page_and_repeats_our_header(self):
        from .services import create_manufacturer_order

        order = create_manufacturer_order(
            actor=self.owner, manufacturer=self.maker, brand=self.brand, advance_percent=0,
            items=[{"product": self.ashwa, "bottle_size": 30 + n, "quantity": "10", "unit_price": "100"}
                   for n in range(30)],
        )
        pages = self.page_texts(order)
        self.assertGreater(len(pages), 1)
        self.assertEqual([("Company stamp" in text) for text in pages], [False] * (len(pages) - 1) + [True])
        for text in pages[1:]:
            self.assertIn("Phone: +92 331 6504749", text)
            self.assertIn("Description", text)


class LastPageShowsProductsTests(StampOnlyFooterTests):
    def test_last_page_always_shows_products_above_the_totals(self):
        from .services import create_manufacturer_order

        for count in range(4, 16):
            order = create_manufacturer_order(
                actor=self.owner, manufacturer=self.maker, brand=self.brand,
                items=[{"product": self.ashwa, "bottle_size": 30 + n, "quantity": "10", "unit_price": "100"}
                       for n in range(count)],
            )
            for part in ("advance", "balance"):
                pages = self.page_texts(order, part=part)
                with self.subTest(products=count, part=part):
                    if len(pages) > 1:
                        self.assertIn("Description", pages[-1])
                    self.assertIn("Company stamp", pages[-1])
                    self.assertFalse(any("Company stamp" in text for text in pages[:-1]))


class PurchaseOrderNumberTests(PurchaseOrderDocumentTests):
    def test_numbers_use_the_manufacturer_prefix_and_count_per_manufacturer(self):
        from .services import manufacturer_code

        update_manufacturer(actor=self.owner, manufacturer_id=self.maker.pk, prefix="GP")
        self.maker.refresh_from_db()
        other = create_manufacturer(actor=self.owner, name="Sarkar Health Care")
        set_product_manufacturers(actor=self.owner, product=self.ashwa, manufacturers=[self.maker, other])

        first, second = self.make_order(), self.make_order()
        from .services import create_manufacturer_order

        theirs = create_manufacturer_order(
            actor=self.owner, manufacturer=other, brand=self.brand,
            items=[{"product": self.ashwa, "bottle_size": 30, "quantity": "1", "unit_price": "1"}],
        )
        self.assertEqual([first.po_number, second.po_number], ["PO-GP-001", "PO-GP-002"])
        self.assertEqual(theirs.po_number, "PO-SHC-001")
        self.assertEqual(manufacturer_code(other), "SHC")

        # A deleted order's number isn't reused while a later one exists.
        self.client.post(reverse("manufacturer-order-delete", args=[first.pk]))
        self.assertFalse(ManufacturerOrder.objects.filter(pk=first.pk).exists())
        self.assertEqual(self.make_order().po_number, "PO-GP-003")
