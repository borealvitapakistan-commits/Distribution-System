from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.customers.services import create_customer
from apps.distributors.services import create_distributor
from apps.manufacturers.services import create_manufacturer
from apps.products.services import create_product
from apps.owner_warehouse.models import Location
from apps.owner_warehouse.services import create_inventory, create_location

from .models import OwnerProfile
from .services import create_owner, deactivate_owner, update_owner_profile


class OwnerRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@owners.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def test_create_owner_is_active_and_admin_capable_immediately(self):
        new_owner = create_owner(
            actor=self.owner,
            email="second@owners.test",
            temporary_password="TemporaryPassword123!",
            name="Second Owner",
        )

        self.assertTrue(new_owner.is_active)
        self.assertTrue(new_owner.is_staff)
        self.assertTrue(new_owner.is_superuser)

        profile = OwnerProfile.objects.get(user=new_owner)
        self.assertTrue(profile.active)

    def test_owner_self_service_profile_update(self):
        # The root Owner (created directly, e.g. via createsuperuser) has
        # no OwnerProfile row — self-service editing must still work.
        update_owner_profile(
            user=self.owner,
            first_name="Updated",
        )
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "Updated")

    def test_owner_cannot_deactivate_self(self):
        with self.assertRaises(ValidationError):
            deactivate_owner(actor=self.owner, target_user_id=self.owner.pk)

    def test_deactivate_owner_removes_access(self):
        new_owner = create_owner(
            actor=self.owner,
            email="deactivate-me@owners.test",
            temporary_password="TemporaryPassword123!",
            name="Deactivate Me",
        )

        deactivate_owner(actor=self.owner, target_user_id=new_owner.pk)
        new_owner.refresh_from_db()
        self.assertFalse(new_owner.is_active)
        self.assertFalse(OwnerProfile.objects.get(user=new_owner).active)


class OwnerPagesTests(TestCase):
    """Smoke-tests every Owner-facing page across all apps."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@pages.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.manufacturer = create_manufacturer(actor=self.owner, name="Page Manufacturer")
        self.customer = create_customer(actor=self.owner, name="Page Customer")
        self.product = create_product(
            actor=self.owner,
            sku="PAGE-001",
            name="Page Product",
        )
        self.inventory = create_inventory(
            actor=self.owner, code="PAGE-INV", name="Page Region"
        )
        self.location = create_location(
            actor=self.owner,
            code="PAGE-LOC",
            name="Page Location",
            location_type=Location.LocationType.OWN,
            inventory=self.inventory,
        )
        self.distributor = create_distributor(
            actor=self.owner,
            email="distributor@pages.test",
            temporary_password="TemporaryPassword123!",
            name="Page Distributor",
        )

    def test_all_owner_pages_render(self):
        self.client.force_login(self.owner)
        routes = [
            reverse("owner-list"),
            reverse("owner-create"),
            reverse("owner-profile"),
            reverse("distributor-list"),
            reverse("distributor-invite"),
            reverse("manufacturer-list"),
            reverse("manufacturer-create"),
            reverse("manufacturer-detail", kwargs={"pk": self.manufacturer.pk}),
            reverse("manufacturer-edit", kwargs={"pk": self.manufacturer.pk}),
            reverse("customer-list"),
            reverse("customer-create"),
            reverse("customer-detail", kwargs={"pk": self.customer.pk}),
            reverse("customer-edit", kwargs={"pk": self.customer.pk}),
            reverse("category-list"),
            reverse("category-create"),
            reverse("ingredient-create"),
            reverse("product-list"),
            reverse("product-create"),
            reverse("product-detail", kwargs={"pk": self.product.pk}),
            reverse("product-edit", kwargs={"pk": self.product.pk}),
            reverse("location-list"),
            reverse("location-create"),
            reverse("location-detail", kwargs={"pk": self.location.pk}),
            reverse("location-edit", kwargs={"pk": self.location.pk}),
            reverse("inventory-list"),
            reverse("inventory-create"),
            reverse("inventory-detail", kwargs={"pk": self.inventory.pk}),
            reverse("inventory-edit", kwargs={"pk": self.inventory.pk}),
            reverse("stock-balance-list"),
            reverse("owner-finance"),
            reverse("brand-list"),
            reverse("owner-purchase-orders-hub"),
            reverse("owner-purchase-order-list"),
            reverse("manufacturer-order-list"),
        ]

        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)


class ApiWiringTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@api.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def test_owner_api_list_routes_are_connected(self):
        routes = [
            reverse("api-owner-list"),
            reverse("api-owner-profile"),
            reverse("api-manufacturer-list"),
            reverse("api-customer-list"),
            reverse("api-category-list"),
            reverse("api-ingredient-list"),
            reverse("api-product-list"),
            reverse("api-location-list"),
            reverse("api-brand"),
        ]

        for route in routes:
            with self.subTest(route=route):
                response = self.api.get(route)
                self.assertEqual(response.status_code, 200)
