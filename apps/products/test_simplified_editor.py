from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.manufacturers.services import create_manufacturer

from .forms import ProductForm, ProductIngredientForm
from .models import Ingredient, Product, ProductCategory, ProductIngredient
from .services import create_product, save_manufacturer_price


class SimplifiedProductEditorTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="simple-editor@test.local", role="OWNER")
        self.client.force_login(self.owner)

    def data(self, **extra):
        return {"name": "New supplement", "active": "True",
                "ingredients-TOTAL_FORMS": "0", "ingredients-INITIAL_FORMS": "0", **extra}

    def test_new_products_need_neither_sku_nor_barcode(self):
        for name in ("First supplement", "Second supplement"):
            response = self.client.post(reverse("product-create"), self.data(name=name))
            self.assertEqual(response.status_code, 302)
        products = Product.objects.order_by("name")
        self.assertEqual(products.count(), 2)
        self.assertNotEqual(products[0].sku, products[1].sku)
        self.assertTrue(all(product.sku and not product.barcode for product in products))
        self.assertNotIn("sku", ProductForm().fields)

    def test_types_determine_measurement_on_the_server(self):
        expected = {"Capsule": "PIECE", "Tablet": "PIECE", "Liquid": "ML",
                    "Essential Oil": "ML", "Carrier Oil": "ML", "Powder": "GRAM", "Herbal Tea": "PIECE"}
        for name, unit in expected.items():
            with self.subTest(name=name):
                category = ProductCategory.objects.get(name=name)
                form = ProductForm(self.data(name=name, category=category.pk, unit_of_measure="BOTTLE"))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["unit_of_measure"], unit)
        self.assertEqual(set(ProductForm().fields["category"].queryset.values_list("name", flat=True)), set(expected))

    def test_removed_fields_preserve_existing_values(self):
        product = create_product(actor=self.owner, name="Original", sku="KEEP", size=60,
                                 base_retail_price=1400, weight_grams=150, key_benefits="Saved benefit")
        response = self.client.post(reverse("product-detail", args=[product.pk]),
                                    self.data(name="Updated", size="999", base_retail_price="1", sku="REPLACE"))
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual((product.sku, product.size, product.base_retail_price, product.weight_grams),
                         ("KEEP", Decimal("60"), Decimal("1400"), Decimal("150")))
        self.assertEqual(product.key_benefits, "Saved benefit")

    def test_optional_ingredients_support_multiple_rows_and_removal(self):
        product = create_product(actor=self.owner, name="Ingredient test")
        first = Ingredient.objects.create(name="First")
        second = Ingredient.objects.create(name="Second")
        url = reverse("product-detail", args=[product.pk])
        response = self.client.post(url, self.data(**{
            "ingredients-TOTAL_FORMS": "2",
            "ingredients-0-ingredient": first.pk, "ingredients-0-strength": "500 mg", "ingredients-0-is_medicinal": "on",
            "ingredients-1-ingredient": second.pk, "ingredients-1-strength": "20 mg",
        }))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(product.ingredients.count(), 2)
        link = product.ingredients.get(ingredient=first)
        self.assertEqual((link.strength, link.is_medicinal), ("500 mg", True))
        self.assertFalse(product.ingredients.get(ingredient=second).is_medicinal)
        self.assertEqual(set(ProductIngredientForm().fields), {"ingredient", "strength", "is_medicinal"})

        link.unit, link.extract_ratio, link.equivalent_to = "mg", "10:1", "5 g"
        link.save()
        other = product.ingredients.get(ingredient=second)
        response = self.client.post(url, self.data(**{
            "ingredients-TOTAL_FORMS": "2", "ingredients-INITIAL_FORMS": "2",
            "ingredients-0-id": link.pk, "ingredients-0-ingredient": first.pk,
            "ingredients-0-strength": "600 mg", "ingredients-0-is_medicinal": "on",
            "ingredients-1-id": other.pk, "ingredients-1-ingredient": second.pk, "ingredients-1-DELETE": "on",
        }))
        self.assertEqual(response.status_code, 302)
        saved = product.ingredients.get()
        self.assertEqual((saved.strength, saved.unit, saved.extract_ratio, saved.equivalent_to),
                         ("600 mg", "mg", "10:1", "5 g"))

    def test_saved_fixed_retail_does_not_follow_manufacturer_cost(self):
        maker = create_manufacturer(actor=self.owner, name="Pricing lab")
        product = create_product(actor=self.owner, name="Pricing test")
        url = reverse("product-manufacturer-packages", args=[product.pk, maker.pk])
        response = self.client.post(url, {"amount": ["60"], "price": ["100"],
                                         "retail_mode": ["FIXED"], "retail_price": ["250"]})
        self.assertEqual(response.status_code, 302)
        save_manufacturer_price(actor=self.owner, product=product, manufacturer=maker, amount=60, price=Decimal("150"))
        package = product.package_prices.get()
        self.assertEqual((package.price, package.retail_price), (Decimal("150"), Decimal("250")))
        page = self.client.get(reverse("product-detail", args=[product.pk]))
        self.assertNotContains(page, "data-retail-mode-select")
        self.assertContains(page, "Our retail price")
