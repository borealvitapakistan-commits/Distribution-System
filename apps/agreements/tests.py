from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from apps.requests import tests as po_tests
from apps.requests.models import PurchaseOrder
from apps.requests.services import (
    create_purchase_order,
    receive_purchase_order_item,
    ship_purchase_order_item,
    update_line_discounts,
    update_line_status,
)

from .models import Agreement, discounted_price
from .services import (
    agreement_in_force,
    cancel_agreement,
    create_agreement,
    decline_agreement,
    sign_agreement,
)


class AgreementPricingTests(TestCase):
    """Owner sends an agreement → Distributor signs → their orders inside
    its dates get its discounts, which the Owner can still adjust per
    line before shipping."""

    # Same Owner, products (X = 100, Y = 50), stock and two approved
    # Distributors as the purchase order tests.
    setUp = po_tests.PurchaseOrderWorkflowTests.setUp
    make_po = po_tests.PurchaseOrderWorkflowTests.make_po

    def send_agreement(self, discount="18", profile=None, start=None, end=None, product_rates=()):
        today = timezone.localdate()
        return create_agreement(
            actor=self.owner,
            distributor_profile=profile or self.distributor_profile,
            discount_percentage=Decimal(discount),
            start_date=start or today - timedelta(days=1),
            end_date=end or today + timedelta(days=30),
            product_rates=product_rates,
        )

    def test_discount_math_matches_the_agreement_examples(self):
        self.assertEqual(discounted_price(Decimal("1000"), Decimal("18")), Decimal("820.00"))
        self.assertEqual(discounted_price(Decimal("2000"), Decimal("18")), Decimal("1640.00"))
        self.assertEqual(discounted_price(Decimal("3000"), Decimal("15")), Decimal("2550.00"))
        self.assertEqual(discounted_price(Decimal("1000"), Decimal("15")), Decimal("850.00"))

    def test_signed_agreement_prices_new_orders_with_product_override(self):
        agreement = self.send_agreement(
            discount="18", product_rates=[(self.product_y, Decimal("10"))]
        )
        sign_agreement(actor=self.distributor_user, agreement_id=agreement.pk, signature="PO Dist")

        po = self.make_po()
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)

        self.assertEqual(po.agreement, agreement)
        self.assertEqual(item_x.list_price, Decimal("100.00"))
        self.assertEqual(item_x.discount_percentage, Decimal("18"))
        self.assertEqual(item_x.unit_price, Decimal("82.00"))
        self.assertEqual(item_y.discount_percentage, Decimal("10"))
        self.assertEqual(item_y.unit_price, Decimal("45.00"))
        # 4 × 82 + 5 × 45
        self.assertEqual(po.subtotal, Decimal("553.00"))

    def test_unsigned_declined_or_cancelled_agreements_do_not_apply(self):
        agreement = self.send_agreement()
        self.assertIsNone(self.make_po().agreement)

        decline_agreement(actor=self.distributor_user, agreement_id=agreement.pk, reason="Too low")
        self.assertIsNone(agreement_in_force(self.distributor_profile))

        second = self.send_agreement()
        sign_agreement(actor=self.distributor_user, agreement_id=second.pk, signature="PO Dist")
        cancel_agreement(actor=self.owner, agreement_id=second.pk, reason="Renegotiating")
        self.assertIsNone(agreement_in_force(self.distributor_profile))

    def test_agreement_only_applies_inside_its_dates(self):
        today = timezone.localdate()
        agreement = self.send_agreement(
            start=today + timedelta(days=5), end=today + timedelta(days=10)
        )
        sign_agreement(actor=self.distributor_user, agreement_id=agreement.pk, signature="PO Dist")

        self.assertIsNone(agreement_in_force(self.distributor_profile))
        self.assertEqual(agreement_in_force(self.distributor_profile, today + timedelta(days=7)), agreement)
        self.assertIsNone(agreement_in_force(self.distributor_profile, today + timedelta(days=11)))

    def test_each_distributor_gets_its_own_percentage(self):
        mine = self.send_agreement(discount="12")
        sign_agreement(actor=self.distributor_user, agreement_id=mine.pk, signature="A")

        other_profile = self.other_distributor_user.distributor_profile
        theirs = self.send_agreement(discount="18", profile=other_profile)
        sign_agreement(actor=self.other_distributor_user, agreement_id=theirs.pk, signature="B")

        my_po = self.make_po()
        their_po = create_purchase_order(
            actor=self.other_distributor_user,
            items=[{"product": self.product_x, "quantity_requested": Decimal("1")}],
        )

        self.assertEqual(my_po.items.get(product=self.product_x).unit_price, Decimal("88.00"))
        self.assertEqual(their_po.items.get(product=self.product_x).unit_price, Decimal("82.00"))

    def test_overlapping_agreements_for_one_distributor_are_refused(self):
        self.send_agreement()

        with self.assertRaises(ValidationError):
            self.send_agreement(discount="20")

    def test_only_the_distributor_it_was_sent_to_can_sign(self):
        agreement = self.send_agreement()

        with self.assertRaises(ValidationError):
            sign_agreement(actor=self.other_distributor_user, agreement_id=agreement.pk, signature="X")

        with self.assertRaises(ValidationError):
            sign_agreement(actor=self.distributor_user, agreement_id=agreement.pk, signature="  ")

    def test_owner_can_change_a_lines_discount_until_it_ships(self):
        agreement = self.send_agreement(discount="18")
        sign_agreement(actor=self.distributor_user, agreement_id=agreement.pk, signature="PO Dist")
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)

        update_line_discounts(
            actor=self.owner, purchase_order_id=po.pk, discounts={str(item_x.pk): "15"}
        )
        item_x.refresh_from_db()
        self.assertEqual(item_x.unit_price, Decimal("85.00"))

        ship_purchase_order_item(
            actor=self.owner, item_id=item_x.pk, allocations=[(self.batch_x, Decimal("1"))]
        )

        with self.assertRaises(ValidationError):
            update_line_discounts(
                actor=self.owner, purchase_order_id=po.pk, discounts={str(item_x.pk): "10"}
            )

    def test_unavailable_line_is_dropped_and_the_order_completes_without_it(self):
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)

        # Marking unavailable needs a comment for the Distributor.
        with self.assertRaises(ValidationError):
            update_line_status(actor=self.owner, item_id=item_y.pk, note="", unavailable=True)

        ship_purchase_order_item(
            actor=self.owner, item_id=item_x.pk, allocations=[(self.batch_x, Decimal("4"))]
        )
        receive_purchase_order_item(actor=self.distributor_user, item_id=item_x.pk, quantity=Decimal("4"))

        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.PARTIALLY_SHIPPED)

        update_line_status(
            actor=self.owner, item_id=item_y.pk, note="Out of stock this season", unavailable=True
        )

        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.RECEIVED)
        # Only Product X (4 × 100) is billed.
        self.assertEqual(po.subtotal, Decimal("400.00"))

    def test_line_closed_partway_bills_only_what_shipped(self):
        po = self.make_po()
        item_y = po.items.get(product=self.product_y)

        ship_purchase_order_item(
            actor=self.owner, item_id=item_y.pk, allocations=[(self.batch_y, Decimal("2"))]
        )
        update_line_status(actor=self.owner, item_id=item_y.pk, note="Only 2 left", unavailable=True)

        item_y.refresh_from_db()
        self.assertEqual(item_y.quantity_ordered, Decimal("2"))
        self.assertEqual(item_y.quantity_to_ship_remaining, Decimal("0"))
        self.assertEqual(item_y.line_total, Decimal("100.00"))

    def test_distributor_is_notified_and_signs_through_the_pages(self):
        agreement = self.send_agreement()
        client = Client()
        client.force_login(self.distributor_user)

        dashboard = client.get("/distributor/")
        self.assertContains(dashboard, agreement.agreement_number)
        self.assertEqual(dashboard.context["agreements_to_sign_count"], 1)

        detail = client.get(f"/distributor/agreements/{agreement.pk}/")
        self.assertContains(detail, "Sign Agreement")

        client.post(f"/distributor/agreements/{agreement.pk}/sign/", {"signature": "PO Distributor"})
        agreement.refresh_from_db()
        self.assertEqual(agreement.status, Agreement.Status.ACCEPTED)
        self.assertEqual(agreement.distributor_signature, "PO Distributor")
        self.assertEqual(client.get("/distributor/").context["agreements_to_sign_count"], 0)

    def test_owner_creates_an_agreement_through_the_form(self):
        client = Client()
        client.force_login(self.owner)
        today = timezone.localdate()

        response = client.post(
            "/owner/agreements/new/",
            {
                "distributor_profile": self.distributor_profile.pk,
                "discount_percentage": "18",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=365)).isoformat(),
                "terms": "",
                "rates-TOTAL_FORMS": "1",
                "rates-INITIAL_FORMS": "0",
                "rates-MIN_NUM_FORMS": "0",
                "rates-MAX_NUM_FORMS": "1000",
                "rates-0-product": self.product_x.pk,
                "rates-0-discount_percentage": "15",
            },
        )

        agreement = Agreement.objects.get()
        self.assertRedirects(response, f"/owner/agreements/{agreement.pk}/")
        self.assertEqual(agreement.status, Agreement.Status.SENT)
        self.assertEqual(agreement.discount_for(self.product_x), Decimal("15"))
        self.assertEqual(agreement.discount_for(self.product_y), Decimal("18"))

        detail = client.get(f"/owner/agreements/{agreement.pk}/")
        self.assertContains(detail, "Signature of Owner")
        self.assertContains(detail, "1,000 becomes 820.00")
        self.assertContains(detail, "Cancel Agreement")
        self.assertContains(client.get("/owner/agreements/"), agreement.agreement_number)

        distributor = Client()
        distributor.force_login(self.distributor_user)
        self.assertContains(distributor.get("/distributor/agreements/"), "Review &amp; sign")
        # The order form shows this Distributor's discount on each product.
        sign_agreement(actor=self.distributor_user, agreement_id=agreement.pk, signature="PO Dist")
        order_form = distributor.get("/distributor/purchase-orders/new/")
        self.assertContains(order_form, 'data-discount="15.00"')
        self.assertContains(order_form, agreement.agreement_number)

    def test_owner_order_page_saves_discounts_and_line_comments(self):
        po = self.make_po()
        item_x = po.items.get(product=self.product_x)
        item_y = po.items.get(product=self.product_y)
        client = Client()
        client.force_login(self.owner)

        page = client.get(f"/owner/purchase-orders/{po.pk}/")
        self.assertContains(page, f'name="discount_{item_x.pk}"')

        client.post(f"/owner/purchase-orders/{po.pk}/discounts/", {f"discount_{item_x.pk}": "20"})
        item_x.refresh_from_db()
        self.assertEqual(item_x.unit_price, Decimal("80.00"))

        client.post(
            f"/owner/purchase-orders/{po.pk}/items/{item_y.pk}/status/",
            {f"line-{item_y.pk}-note": "Discontinued", f"line-{item_y.pk}-unavailable": "on"},
        )
        item_y.refresh_from_db()
        self.assertTrue(item_y.unavailable)

        distributor = Client()
        distributor.force_login(self.distributor_user)
        self.assertContains(
            distributor.get(f"/distributor/purchase-orders/{po.pk}/"), "Discontinued"
        )
