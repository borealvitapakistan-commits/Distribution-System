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


class ProductListPageTests(TestCase):
    def setUp(self):
        from apps.products.models import Product, ProductCategory

        self.owner = User.objects.create_user(
            email="owner@list.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        self.capsule = ProductCategory.objects.get(name="Capsule")
        self.liquid = ProductCategory.objects.get(name="Liquid")
        self.ashwagandha = create_product(
            actor=self.owner,
            sku="SC-ASH",
            name="Ashwagandha",
            category=self.capsule,
            size=Decimal("60"),
        )
        self.syrup = create_product(
            actor=self.owner,
            sku="L-IMM",
            name="Immunity Liquid",
            category=self.liquid,
            unit_of_measure=Product.UnitOfMeasure.ML,
            size=Decimal("200"),
        )
        self.loose = create_product(actor=self.owner, sku="LZ-N40", name="Neo40")
        self.client = Client()
        self.client.force_login(self.owner)

    def names(self, response):
        return [product.name for product in response.context["products"]]

    def test_all_ten_categories_are_seeded(self):
        from apps.products.models import ProductCategory

        self.assertEqual(
            list(ProductCategory.objects.values_list("name", flat=True)),
            [
                "Capsule", "Tablet", "Soft Gel", "Liquid", "Syrup", "Oil",
                "Essential Oil", "Carrier Oil", "Powder", "Herbal Tea",
            ],
        )

    def test_list_shows_unit_and_size_but_no_bottle_prices(self):
        response = self.client.get("/owner/products/")

        self.assertContains(response, "<td>capsule</td>", html=True)
        self.assertContains(response, "<td>60 units</td>", html=True)
        self.assertContains(response, "<td>200 ml</td>", html=True)
        self.assertNotContains(response, "caps</th>")

    def test_category_tile_filters_and_counts(self):
        response = self.client.get(f"/owner/products/?category={self.capsule.pk}")
        self.assertEqual(self.names(response), ["Ashwagandha"])

        tiles = {tile["name"]: tile["count"] for tile in response.context["tiles"]}
        self.assertEqual(tiles["Capsule"], 1)
        self.assertEqual(tiles["Uncategorized"], 1)

        response = self.client.get("/owner/products/?category=none")
        self.assertEqual(self.names(response), ["Neo40"])

    def test_search_matches_name_sku_and_category(self):
        for query, expected in [("ashwa", ["Ashwagandha"]), ("l-imm", ["Immunity Liquid"]), ("liquid", ["Immunity Liquid"])]:
            response = self.client.get("/owner/products/", {"q": query})
            self.assertEqual(self.names(response), expected)

    def test_sort_by_sku_descending(self):
        response = self.client.get("/owner/products/?sort=-sku")
        self.assertEqual(self.names(response), ["Ashwagandha", "Neo40", "Immunity Liquid"])

    def test_rows_link_to_the_product(self):
        response = self.client.get("/owner/products/")
        self.assertContains(response, f'data-href="/owner/products/{self.ashwagandha.pk}/"')

    def test_product_form_only_offers_unit_ml_gram(self):
        response = self.client.get("/owner/products/new/")
        units = [value for value, _label in response.context["form"].fields["unit_of_measure"].choices]
        self.assertEqual(units, ["PIECE", "ML", "GRAM"])
        self.assertContains(response, 'name="category"')
        self.assertContains(response, 'name="size"')


def fake_shopify_product(**overrides):
    product = {
        "id": "8291596009661",
        "title": "Allergy Relief Capsule | 120 Vegetarian Capsules",
        "handle": "allergy-relief-capsule-120-vegetarian-capsules",
        "description_html": "<p>Natural allergy support.</p>",
        "vendor": "Boreal Vita",
        "product_type": "",
        "tags": ["allergy", "immune"],
        "active": True,
        "image_url": "https://cdn.shopify.com/s/files/1/mask.png?v=1",
        "variants": [
            {"sku": "", "barcode": "", "price": "3190.00", "compare_at_price": None, "title": "120 Capsules"},
            {"sku": "", "barcode": "", "price": "1790.00", "compare_at_price": None, "title": "60 Capsules"},
        ],
    }
    product.update(overrides)
    return product


def fake_image(url):
    import base64

    return "mask.png", base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


class ShopifyImportTests(TestCase):
    def setUp(self):
        import tempfile

        from django.test import override_settings

        media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        media.enable()
        self.addCleanup(media.disable)

        self.owner = User.objects.create_user(
            email="owner@shopify.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )

    def test_new_product_brings_photo_description_prices_category_and_size(self):
        from apps.products.shopify_import import import_product

        product = import_product(
            actor=self.owner,
            shopify_product=fake_shopify_product(),
            fetch_image=fake_image,
        )

        self.assertEqual(product.name, "Allergy Relief Capsule | 120 Vegetarian Capsules")
        self.assertEqual(product.sku, "SHOPIFY-8291596009661")
        self.assertEqual(product.body_html, "<p>Natural allergy support.</p>")
        self.assertEqual(product.tags, "allergy, immune")
        self.assertEqual(product.brand_name, "Boreal Vita")
        self.assertEqual(product.category.name, "Capsule")
        self.assertEqual(product.size, Decimal("120"))
        self.assertTrue(product.image.name.startswith("products/mask"))
        self.assertEqual(
            {row.bottle_size: row.price for row in product.retail_prices.all()},
            {120: Decimal("3190.00"), 60: Decimal("1790.00")},
        )

    def test_existing_product_is_matched_by_name_and_keeps_its_own_details(self):
        from apps.products.models import Product, ProductCategory
        from apps.products.shopify_import import import_product, preview

        tablet = ProductCategory.objects.get(name="Tablet")
        existing = create_product(
            actor=self.owner,
            sku="CC-ALR",
            name="Allergy Relief",
            category=tablet,
            enlistment_number="80134953",
        )

        row = preview([fake_shopify_product()], Product.objects.all())[0]
        self.assertEqual(row["match"], existing)

        import_product(
            actor=self.owner,
            shopify_product=fake_shopify_product(),
            target=existing,
            fetch_image=fake_image,
        )
        existing.refresh_from_db()

        self.assertEqual(existing.name, "Allergy Relief")
        self.assertEqual(existing.sku, "CC-ALR")
        self.assertEqual(existing.enlistment_number, "80134953")
        self.assertEqual(existing.category, tablet)
        self.assertEqual(existing.body_html, "<p>Natural allergy support.</p>")
        self.assertEqual(existing.shopify_product_id, "8291596009661")
        self.assertTrue(existing.image)

    def test_variant_sku_wins_and_ambiguous_names_are_not_guessed(self):
        from apps.products.models import Product
        from apps.products.shopify_import import preview

        create_product(actor=self.owner, sku="SC-MLT", name="Milk Thistle")
        create_product(actor=self.owner, sku="L-MLT", name="Milk Thistle", handle="milk-thistle-liquid")
        by_sku = fake_shopify_product(
            title="Milk Thistle",
            handle="milk-thistle-shop",
            variants=[{"sku": "l-mlt", "barcode": "", "price": "1", "compare_at_price": None, "title": "Default Title"}],
        )
        by_name = fake_shopify_product(id="2", title="Milk Thistle", handle="milk-thistle-2")

        rows = preview([by_sku, by_name], Product.objects.all())
        self.assertEqual(rows[0]["match"].sku, "L-MLT")
        self.assertIsNone(rows[1]["match"])

    def test_write_access_is_refused(self):
        from unittest import mock

        from apps.products import shopify

        scopes = {"currentAppInstallation": {"accessScopes": [{"handle": "read_products"}, {"handle": "write_products"}]}}
        with mock.patch.object(shopify, "graphql", return_value=scopes):
            with self.assertRaisesMessage(shopify.ShopifyError, "write access"):
                shopify.require_read_only_access()

    def test_import_page_explains_when_shopify_is_not_connected(self):
        from django.test import override_settings

        client = Client()
        client.force_login(self.owner)

        with override_settings(SHOPIFY_STORE_DOMAIN=""):
            response = client.get("/owner/products/import/shopify/")

        self.assertContains(response, "Shopify isn")

    def test_import_page_posts_selected_products(self):
        from unittest import mock

        from apps.products import shopify, shopify_import
        from apps.products.models import Product

        client = Client()
        client.force_login(self.owner)

        with mock.patch.object(shopify, "is_configured", return_value=True), \
                mock.patch.object(shopify, "fetch_products", return_value=[fake_shopify_product()]), \
                mock.patch.object(shopify_import, "download_image", fake_image):
            page = client.get("/owner/products/import/shopify/")
            self.assertContains(page, "Allergy Relief Capsule")

            response = client.post(
                "/owner/products/import/shopify/",
                {"import": ["8291596009661"], "target_8291596009661": "new"},
            )

        self.assertRedirects(response, "/owner/products/")
        self.assertTrue(Product.objects.filter(shopify_product_id="8291596009661").exists())


class ProductPageTests(TestCase):
    def setUp(self):
        import tempfile

        from django.test import override_settings

        from apps.manufacturers.services import create_manufacturer

        media = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        media.enable()
        self.addCleanup(media.disable)

        self.owner = User.objects.create_user(
            email="owner@page.test",
            password="OwnerPassword123!",
            role=User.Role.OWNER,
            is_active=True,
        )
        from apps.products.models import ProductCategory

        self.product = create_product(
            actor=self.owner,
            sku="SC-ASH",
            name="Ashwagandha",
            category=ProductCategory.objects.get(name="Capsule"),
            key_benefits="Helps reduce stress\n- Supports focus\n",
            allergen_info="Free from gluten.",
        )
        self.maker = create_manufacturer(actor=self.owner, name="Herbiotics")
        self.client = Client()
        self.client.force_login(self.owner)
        self.url = f"/owner/products/{self.product.pk}/"

    def test_add_edit_and_delete_a_package_size(self):
        response = self.client.post(
            f"{self.url}packages/new/",
            {"manufacturer": self.maker.pk, "amount": "60", "price": "1050"},
        )
        self.assertRedirects(response, self.url)
        package = self.product.package_prices.get()
        self.assertEqual((package.amount, package.price), (Decimal("60"), Decimal("1050")))

        page = self.client.get(self.url)
        self.assertContains(page, "Herbiotics")
        self.assertContains(page, "1,050.00")
        self.assertContains(page, "<td>capsules</td>", html=True)

        self.client.post(
            f"{self.url}packages/{package.pk}/edit/",
            {"manufacturer": self.maker.pk, "amount": "60", "price": "990"},
        )
        package.refresh_from_db()
        self.assertEqual(package.price, Decimal("990"))

        self.client.post(f"{self.url}packages/{package.pk}/delete/")
        self.assertFalse(self.product.package_prices.exists())

    def test_same_manufacturer_and_amount_twice_is_refused(self):
        data = {"manufacturer": self.maker.pk, "amount": "60", "price": "1050"}
        self.client.post(f"{self.url}packages/new/", data)
        response = self.client.post(f"{self.url}packages/new/", data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.product.package_prices.count(), 1)

    def test_page_shows_benefits_ingredients_and_no_npn(self):
        response = self.client.get(self.url)

        self.assertEqual(response.context["benefits"], ["Helps reduce stress", "Supports focus"])
        self.assertContains(response, "Free from gluten.")
        self.assertNotContains(response, "NPN")

    def test_photos_can_be_added_and_removed_from_the_edit_form(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.products.forms import ProductForm
        from apps.products.models import ProductImage

        def form_data():
            form = ProductForm(instance=self.product)
            data = {
                name: ("" if value is None else value)
                for name, value in form.initial.items()
                if name in form.fields and name not in ("image", "allow_fractional_quantity")
            }
            data.update(
                {
                    "category": self.product.category_id,
                    "active": "on",
                    "ingredients-TOTAL_FORMS": "0",
                    "ingredients-INITIAL_FORMS": "0",
                    "ingredients-MIN_NUM_FORMS": "0",
                    "ingredients-MAX_NUM_FORMS": "1000",
                }
            )
            return data

        _name, png = fake_image("")
        data = form_data()
        data["gallery_photos"] = [
            SimpleUploadedFile("a.png", png, content_type="image/png"),
            SimpleUploadedFile("b.png", png, content_type="image/png"),
        ]
        response = self.client.post(f"{self.url}edit/", data)
        self.assertRedirects(response, self.url)
        self.assertEqual(self.product.gallery.count(), 2)

        data = form_data()
        data["remove_photos"] = [self.product.gallery.first().pk]
        self.client.post(f"{self.url}edit/", data)
        self.assertEqual(ProductImage.objects.filter(product=self.product).count(), 1)
