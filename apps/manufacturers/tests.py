from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Manufacturer
from .services import create_manufacturer


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
