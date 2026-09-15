from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.core.models import Brand
from apps.distributors.services import approve_distributor, create_distributor
from apps.products.serializers import DistributorProductSerializer
from apps.products.services import create_ingredient, create_product


class ProductRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@products.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def product_data(self, **overrides):
        data = {
            "sku": "sku-001",
            "barcode": "890000000001",
            "name": "Example Product",
            "base_retail_price": Decimal("100.00"),
            "currency": "pkr",
        }
        data.update(overrides)
        return data

    def test_product_can_be_linked_to_a_brand(self):
        brand = Brand.objects.create(name="My Brand")
        product = create_product(
            actor=self.owner,
            **self.product_data(brand=brand),
        )

        self.assertEqual(product.brand, brand)

    def test_sku_and_barcode_are_globally_unique(self):
        create_product(actor=self.owner, **self.product_data())

        with self.assertRaises(ValidationError):
            create_product(actor=self.owner, **self.product_data(name="Duplicate"))

    def test_ingredient_rows_are_trimmed(self):
        ingredient = create_ingredient(
            actor=self.owner,
            name="  Ashwagandha  ",
            botanical_name="Withania somnifera",
        )
        product = create_product(
            actor=self.owner,
            **self.product_data(
                sku="FORMULA-001",
                barcode="",
                ingredients=[
                    {
                        "ingredient": ingredient,
                        "strength": " 10 ",
                        "unit": " mg ",
                    }
                ],
            ),
        )

        link = product.ingredients.get()
        self.assertEqual(ingredient.name, "Ashwagandha")
        self.assertEqual(link.strength, "10")
        self.assertEqual(link.unit, "mg")

    def test_distributor_product_serializer_shows_price_but_no_owner_fields(self):
        product = create_product(actor=self.owner, **self.product_data())
        data = DistributorProductSerializer(product).data

        self.assertIn("base_retail_price", data)
        self.assertNotIn("default_reorder_point", data)
        self.assertNotIn("default_reorder_quantity", data)
        self.assertNotIn("cost", data)
        self.assertNotIn("manufacturer", data)
        self.assertNotIn("locations", data)


class ProductURLRenameTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@rename.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.distributor_user = create_distributor(
            actor=self.owner,
            email="dist@rename.test",
            temporary_password="TemporaryPassword123!",
            name="Rename Distributor",
        )
        approve_distributor(user=self.owner, distributor_id=self.distributor_user.pk)

    def test_old_catalog_url_is_gone(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/catalog/products/")
        self.assertEqual(response.status_code, 404)

    def test_new_products_url_works(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/products/")
        self.assertEqual(response.status_code, 200)

    def test_product_form_includes_brand_field(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/products/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="brand"')

    def test_sidebar_says_products_not_catalog(self):
        client = Client()
        client.force_login(self.owner)

        response = client.get("/owner/")
        self.assertContains(response, "Products")
        self.assertNotContains(response, "Catalog")

    def test_distributor_product_list_url_works(self):
        client = Client()
        client.force_login(self.distributor_user)

        response = client.get("/distributor/products/")
        self.assertEqual(response.status_code, 200)
