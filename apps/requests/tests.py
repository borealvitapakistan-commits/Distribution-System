from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.distributors.models import DistributorProfile
from apps.distributors.services import approve_distributor, create_distributor
from apps.inventory.models import StockBalance
from apps.inventory.services import receive_stock
from apps.products.services import create_product
from apps.warehouse.models import Location
from apps.warehouse.services import create_location

from .models import StockRequest
from .services import (
    add_owner_comment,
    create_stock_request,
    decline_request,
    fulfill_request_item,
)


class StockRequestWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@requests.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product_x = create_product(
            actor=self.owner,
            sku="REQ-X",
            barcode="",
            name="Product X",
            base_retail_price=Decimal("100.00"),
            currency="pkr",
        )
        self.product_y = create_product(
            actor=self.owner,
            sku="REQ-Y",
            barcode="",
            name="Product Y",
            base_retail_price=Decimal("50.00"),
            currency="pkr",
        )
        self.warehouse = create_location(
            actor=self.owner,
            code="REQ-WH",
            name="Requests Warehouse",
            location_type=Location.LocationType.OWN,
        )
        receive_stock(
            actor=self.owner,
            product=self.product_x,
            quantity=Decimal("100"),
            to_location=self.warehouse,
        )
        receive_stock(
            actor=self.owner,
            product=self.product_y,
            quantity=Decimal("2"),
            to_location=self.warehouse,
        )

        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@requests.test",
            temporary_password="TemporaryPassword123!",
            name="Requests Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()
        self.distributor_profile = DistributorProfile.objects.get(
            user=self.distributor_user
        )

        self.other_distributor_user = create_distributor(
            actor=self.owner,
            email="other@requests.test",
            temporary_password="TemporaryPassword123!",
            name="Other Distributor",
        )
        approve_distributor(
            user=self.owner, distributor_id=self.other_distributor_user.pk
        )
        self.other_distributor_user.refresh_from_db()

    def make_request(self):
        return create_stock_request(
            actor=self.distributor_user,
            items=[
                {"product": self.product_x, "quantity_requested": Decimal("4")},
                {"product": self.product_y, "quantity_requested": Decimal("5")},
            ],
        )

    def test_create_stock_request_is_pending_and_scoped(self):
        stock_request = self.make_request()

        self.assertEqual(stock_request.status, StockRequest.Status.PENDING)
        self.assertEqual(stock_request.items.count(), 2)

        self.assertEqual(
            StockRequest.objects.for_user(self.distributor_user).count(), 1
        )
        self.assertEqual(
            StockRequest.objects.for_user(self.other_distributor_user).count(), 0
        )
        self.assertEqual(StockRequest.objects.for_user(self.owner).count(), 1)

    def test_only_approved_distributor_can_create(self):
        with self.assertRaises(PermissionDenied):
            create_stock_request(
                actor=self.owner,
                items=[
                    {"product": self.product_x, "quantity_requested": Decimal("1")}
                ],
            )

    def test_duplicate_product_rejected(self):
        with self.assertRaises(ValidationError):
            create_stock_request(
                actor=self.distributor_user,
                items=[
                    {"product": self.product_x, "quantity_requested": Decimal("1")},
                    {"product": self.product_x, "quantity_requested": Decimal("2")},
                ],
            )

    def test_full_fulfillment_moves_stock_and_updates_status(self):
        stock_request = self.make_request()
        item_x = stock_request.items.get(product=self.product_x)
        item_y = stock_request.items.get(product=self.product_y)

        fulfill_request_item(
            actor=self.owner,
            item_id=item_x.pk,
            quantity=Decimal("4"),
            from_location=self.warehouse,
        )
        stock_request.refresh_from_db()
        self.assertEqual(stock_request.status, StockRequest.Status.PARTIALLY_FULFILLED)

        fulfill_request_item(
            actor=self.owner,
            item_id=item_y.pk,
            quantity=Decimal("2"),
            from_location=self.warehouse,
        )
        stock_request.refresh_from_db()
        item_y.refresh_from_db()
        self.assertEqual(item_y.quantity_fulfilled, Decimal("2"))
        self.assertEqual(stock_request.status, StockRequest.Status.PARTIALLY_FULFILLED)

        distributor_location = Location.objects.get(
            location_type=Location.LocationType.DISTRIBUTOR,
            distributor_profile=self.distributor_profile,
        )
        balance_x = StockBalance.objects.get(
            product=self.product_x, location=distributor_location
        )
        self.assertEqual(balance_x.quantity, Decimal("4"))

    def test_fulfilling_more_than_remaining_is_rejected(self):
        stock_request = self.make_request()
        item_x = stock_request.items.get(product=self.product_x)

        with self.assertRaises(ValidationError):
            fulfill_request_item(
                actor=self.owner,
                item_id=item_x.pk,
                quantity=Decimal("999"),
                from_location=self.warehouse,
            )

    def test_decline_requires_comment_and_is_visible_to_distributor(self):
        stock_request = self.make_request()

        with self.assertRaises(ValidationError):
            decline_request(actor=self.owner, request_id=stock_request.pk, comment="")

        decline_request(
            actor=self.owner,
            request_id=stock_request.pk,
            comment="Sorry, out of stock right now.",
        )
        stock_request.refresh_from_db()

        self.assertEqual(stock_request.status, StockRequest.Status.DECLINED)
        self.assertIn("out of stock", stock_request.owner_comment)

        visible = StockRequest.objects.for_user(self.distributor_user).get(
            pk=stock_request.pk
        )
        self.assertEqual(visible.owner_comment, stock_request.owner_comment)

    def test_add_comment_does_not_change_status(self):
        stock_request = self.make_request()

        add_owner_comment(
            actor=self.owner,
            request_id=stock_request.pk,
            comment="Sending some now, rest later.",
        )
        stock_request.refresh_from_db()

        self.assertEqual(stock_request.status, StockRequest.Status.PENDING)
        self.assertIn("Sending some now", stock_request.owner_comment)


class StockRequestViewWiringTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@requestviews.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.product = create_product(
            actor=self.owner,
            sku="RVIEW-001",
            barcode="",
            name="View Product",
            base_retail_price=Decimal("20.00"),
            currency="pkr",
        )
        self.warehouse = create_location(
            actor=self.owner,
            code="RVIEW-WH",
            name="View Warehouse",
            location_type=Location.LocationType.OWN,
        )
        receive_stock(
            actor=self.owner,
            product=self.product,
            quantity=Decimal("10"),
            to_location=self.warehouse,
        )
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@requestviews.test",
            temporary_password="TemporaryPassword123!",
            name="View Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)
        self.distributor_user.refresh_from_db()

    def test_distributor_can_submit_request_via_form(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/distributor/requests/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-formset-add')
        self.assertContains(response, 'data-formset-empty-form')
        self.assertContains(response, 'id_items-TOTAL_FORMS')

        response = client.post(
            "/distributor/requests/new/",
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
        self.assertEqual(StockRequest.objects.count(), 1)

    def test_owner_can_view_and_fulfill_from_detail_page(self):
        stock_request = create_stock_request(
            actor=self.distributor_user,
            items=[{"product": self.product, "quantity_requested": Decimal("3")}],
        )
        item = stock_request.items.get()

        client = Client()
        client.force_login(self.owner)

        response = client.get(f"/owner/requests/{stock_request.pk}/")
        self.assertEqual(response.status_code, 200)

        response = client.post(
            f"/owner/requests/{stock_request.pk}/items/{item.pk}/fulfill/",
            {"quantity": "3", "from_location": str(self.warehouse.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        self.assertEqual(item.quantity_fulfilled, Decimal("3"))

    def test_sidebar_shows_requests_nav_for_both_roles(self):
        owner_client = Client()
        owner_client.force_login(self.owner)
        owner_response = owner_client.get("/owner/")
        self.assertEqual(owner_response.status_code, 200)
        self.assertContains(owner_response, "Requests")

        distributor_client = Client()
        distributor_client.force_login(self.distributor_user)
        distributor_response = distributor_client.get("/distributor/")
        self.assertEqual(distributor_response.status_code, 200)
        self.assertContains(distributor_response, "My Requests")
