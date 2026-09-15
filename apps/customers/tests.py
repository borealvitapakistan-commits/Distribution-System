from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Customer
from .services import create_customer


class CustomerRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@customer.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def test_customer_name_is_globally_unique(self):
        create_customer(actor=self.owner, name="Same Customer")

        with self.assertRaises(ValidationError):
            create_customer(actor=self.owner, name="Same Customer")

    def test_owner_customer_pages_are_connected(self):
        customer = create_customer(actor=self.owner, name="Page Customer")
        self.client.force_login(self.owner)

        for route in [
            reverse("customer-list"),
            reverse("customer-create"),
            reverse("customer-detail", kwargs={"pk": customer.pk}),
            reverse("customer-edit", kwargs={"pk": customer.pk}),
        ]:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)


class CustomerApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@customer-api.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def test_customer_api_creates_customer(self):
        response = self.api.post(
            reverse("api-customer-list"),
            {"name": "API Customer"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(
            Customer.objects.filter(name="API Customer").exists()
        )
