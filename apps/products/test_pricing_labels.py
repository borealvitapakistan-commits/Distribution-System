import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.manufacturers.services import create_manufacturer

from .models import Product, ProductPackagePrice
from .services import create_product, manufacturer_price_map, save_manufacturer_price

def png_bytes():
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, "PNG")
    return buffer.getvalue()


class RetailPricingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="retail@test.local", role="OWNER")
        self.maker = create_manufacturer(actor=self.owner, name="Alpha Labs")
        self.product = create_product(actor=self.owner, name="Moringa", sku="RET-MOR")
        self.client.force_login(self.owner)
        self.url = reverse("product-manufacturer-packages", args=[self.product.pk, self.maker.pk])

    def package(self, amount):
        return ProductPackagePrice.objects.get(product=self.product, manufacturer=self.maker, amount=amount)

    def test_linked_retail_follows_manufacturer_price_and_fixed_does_not(self):
        self.client.post(self.url, {
            "amount": ["30", "60", "90"], "price": ["1000", "1800", "2500"],
            "retail_mode": ["LINKED", "LINKED", "FIXED"], "markup_type": ["PERCENT", "AMOUNT", "PERCENT"],
            "markup_value": ["50", "700", "0"], "retail_price": ["999", "", "3999"],
        })
        self.assertEqual(self.package(30).retail_price, Decimal("1500.00"))  # typed 999 ignored: linked
        self.assertEqual(self.package(60).retail_price, Decimal("2500.00"))
        self.assertEqual(self.package(90).retail_price, Decimal("3999.00"))

        # The manufacturer raises prices (e.g. from a quote or purchase order).
        for amount, price in ((30, 1200), (90, 2700)):
            save_manufacturer_price(actor=self.owner, product=self.product, manufacturer=self.maker,
                                    amount=amount, price=Decimal(price))
        self.assertEqual(self.package(30).retail_price, Decimal("1800.00"))
        self.assertEqual(self.package(90).retail_price, Decimal("3999.00"))

        # Retail changed on its own, manufacturer price kept.
        self.client.post(self.url, {
            "amount": ["30"], "price": ["1200"], "retail_mode": ["FIXED"],
            "markup_type": ["PERCENT"], "markup_value": ["50"], "retail_price": ["2100"],
        })
        package = self.package(30)
        self.assertEqual((package.price, package.retail_price), (Decimal("1200"), Decimal("2100")))

    def test_bad_retail_rows_change_nothing(self):
        self.client.post(self.url, {"amount": ["30"], "price": ["100"]})
        for data in (
            {"amount": ["30"], "price": ["100"], "retail_mode": ["BOGUS"], "markup_type": ["PERCENT"],
             "markup_value": ["0"], "retail_price": [""]},
            {"amount": ["30"], "price": ["100"], "retail_mode": ["FIXED"], "markup_type": ["PERCENT"],
             "markup_value": ["0"], "retail_price": ["-5"]},
            {"amount": ["30", "60"], "price": ["100", "200"], "retail_mode": ["FIXED"]},
        ):
            self.client.post(self.url, data)
            self.assertEqual(self.package(30).retail_price, None)
            self.assertFalse(ProductPackagePrice.objects.filter(amount=60).exists())

    def test_price_map_gives_each_manufacturer_its_own_prices(self):
        other = create_manufacturer(actor=self.owner, name="Beta Pharma")
        save_manufacturer_price(actor=self.owner, product=self.product, manufacturer=self.maker, amount=40, price=Decimal("900"))
        save_manufacturer_price(actor=self.owner, product=self.product, manufacturer=other, amount=40, price=Decimal("950"))
        data = manufacturer_price_map()
        self.assertEqual(data["prices"][str(self.maker.pk)][str(self.product.pk)]["40"]["cost"], "900.00")
        self.assertEqual(data["prices"][str(other.pk)][str(self.product.pk)]["40"]["cost"], "950.00")
        self.assertEqual(data["allocated"][str(other.pk)], [str(self.product.pk)])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class LabelClaimTests(TestCase):
    def setUp(self):
        from .forms import ProductForm

        self.owner = User.objects.create_user(email="label@test.local", role="OWNER")
        self.product = create_product(actor=self.owner, name="Ashwagandha", sku="LBL-ASH")
        self.client.force_login(self.owner)
        self.url = reverse("product-detail", args=[self.product.pk])
        form = ProductForm(instance=self.product)
        self.data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields
                     if name not in ("image", "label_file", "bottle_image", "allow_fractional_quantity",
                                     "gallery_photos", "manufacturers")}
        self.data.update({"ingredients-TOTAL_FORMS": "0", "ingredients-INITIAL_FORMS": "0"})

    def test_label_claim_label_file_and_bottle_image_are_saved_and_shown(self):
        self.data.update({
            "label_product_name": "Ashwagandha KSM-66 600mg",
            "label_dose": "Take 1 capsule twice daily.",
            "label_cautions": "Not for pregnant women.",
            "label_file": SimpleUploadedFile("label.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
            "bottle_image": SimpleUploadedFile("bottle.png", png_bytes(), content_type="image/png"),
        })
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.label_cautions, "Not for pregnant women.")
        self.assertTrue(self.product.label_is_pdf)
        self.assertEqual(self.product.display_image_url, self.product.bottle_image.url)

        page = self.client.get(self.url)
        self.assertContains(page, "Label claim")
        self.assertContains(page, self.product.bottle_image.url)
        self.assertContains(page, self.product.label_file.url)

        # Removing the label keeps the rest of the label claim.
        self.data.pop("label_file"), self.data.pop("bottle_image")
        self.client.post(self.url, {**self.data, "label_file-clear": "on"})
        self.product.refresh_from_db()
        self.assertFalse(self.product.label_file)
        self.assertTrue(self.product.bottle_image)
        self.assertEqual(self.product.label_dose, "Take 1 capsule twice daily.")

    def test_label_file_must_be_an_image_or_pdf(self):
        self.data["label_file"] = SimpleUploadedFile("label.exe", b"MZ", content_type="application/octet-stream")
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.get(pk=self.product.pk).label_file)


class SimpleProductFormTests(TestCase):
    def test_powder_size_in_grams_and_hidden_fields_are_kept(self):
        from .forms import ProductForm
        from .models import ProductCategory

        owner = User.objects.create_user(email="simple@test.local", role="OWNER")
        powder, _ = ProductCategory.objects.get_or_create(name="Powder")
        product = create_product(actor=owner, name="Creatine", sku="SIM-CRE", generic_name="Creatine monohydrate",
                                 serving_size="5 g")
        form = ProductForm(instance=product)
        for hidden in ("generic_name", "brand_name", "indications", "form", "pack_size", "serving_size",
                       "strength_basis", "units_per_case", "allow_fractional_quantity"):
            self.assertNotIn(hidden, form.fields)
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields
                if name not in ("image", "label_file", "bottle_image", "gallery_photos", "manufacturers")}
        data.update({"category": powder.pk, "unit_of_measure": "GRAM", "size": "300",
                     "ingredients-TOTAL_FORMS": "0", "ingredients-INITIAL_FORMS": "0"})
        self.client.force_login(owner)
        page = self.client.get(reverse("product-detail", args=[product.pk]))
        self.assertContains(page, "Type &amp; size")
        self.assertContains(page, "Grams (powder)")
        self.client.post(reverse("product-detail", args=[product.pk]), data)
        product.refresh_from_db()
        self.assertEqual((product.unit_of_measure, product.size), ("GRAM", Decimal("300")))
        self.assertEqual((product.generic_name, product.serving_size), ("Creatine monohydrate", "5 g"))
