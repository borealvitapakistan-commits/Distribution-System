from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.manufacturers.services import create_manufacturer

from .models import Product
from .services import create_product, save_manufacturer_price


class UnifiedProductSaveTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="save-all@test.local", role="OWNER")
        self.client.force_login(self.owner)
        self.product = create_product(actor=self.owner, name="Original")
        self.makers = [create_manufacturer(actor=self.owner, name=name) for name in ("First lab", "Second lab")]
        for maker in self.makers:
            save_manufacturer_price(actor=self.owner, product=self.product, manufacturer=maker, amount=30, price=Decimal("100"))
        self.url = reverse("product-detail", args=[self.product.pk])

    def data(self):
        data = {"name": "Updated", "active": "True", "manufacturers": [str(m.pk) for m in self.makers],
                "ingredients-TOTAL_FORMS": "0", "ingredients-INITIAL_FORMS": "0",
                "pricing_manufacturers": [str(m.pk) for m in self.makers]}
        for maker, cost in zip(self.makers, ("150", "160")):
            prefix = f"pricing-{maker.pk}-"
            data.update({f"{prefix}amount": ["60"], f"{prefix}price": [cost],
                         f"{prefix}retail_mode": ["FIXED"], f"{prefix}retail_price": ["300"]})
        return data

    def test_save_updates_product_and_every_manufacturer_together(self):
        response = self.client.post(self.url, self.data(), follow=True)
        self.assertRedirects(response, self.url)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated")
        for maker, cost in zip(self.makers, ("150", "160")):
            package = self.product.package_prices.get(manufacturer=maker)
            self.assertEqual((package.amount, package.price, package.retail_price),
                             (Decimal("60"), Decimal(cost), Decimal("300")))
        self.assertContains(response, "Product has been saved.", count=1)
        self.assertNotContains(response, "Save First lab prices")
        self.assertContains(response, 'type="submit" class="button button-primary', count=1)

    def test_bad_price_rolls_back_product_and_all_manufacturer_prices(self):
        data = self.data()
        maker = self.makers[-1]
        data[f"pricing-{maker.pk}-amount"] = ["60", "60"]
        data[f"pricing-{maker.pk}-price"] = ["160", "170"]
        data[f"pricing-{maker.pk}-retail_mode"] = ["FIXED", "FIXED"]
        data[f"pricing-{maker.pk}-retail_price"] = ["300", "310"]
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Original")
        self.assertEqual(list(self.product.package_prices.values_list("price", flat=True)), [Decimal("100"), Decimal("100")])
        self.assertContains(response, "Each size can only be listed once")
        cards = response.context["manufacturer_pricing"]
        self.assertTrue(all(card["open"] for card in cards))
        self.assertEqual(cards[0]["packages"][0]["price"], "150")

    def test_invalid_product_preserves_typed_prices(self):
        data = self.data()
        data["name"] = ""
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["manufacturer_pricing"][0]["packages"][0]["retail_price"], "300")

    def test_removed_manufacturer_is_not_reallocated_by_its_price_card(self):
        data = self.data()
        data["manufacturers"] = [str(self.makers[0].pk)]
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.product.package_prices.filter(manufacturer=self.makers[1]).exists())
        self.assertFalse(self.product.allocations.filter(manufacturer=self.makers[1]).exists())

    def test_empty_price_card_removes_all_its_sizes(self):
        data = self.data()
        for key in list(data):
            if key.startswith(f"pricing-{self.makers[0].pk}-"):
                del data[key]
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertFalse(self.product.package_prices.filter(manufacturer=self.makers[0]).exists())
