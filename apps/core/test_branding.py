from base64 import b64decode

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User

from .context_processors import BRAND_CACHE_KEY
from .models import Brand, BrandLogo


LOGO_BYTES = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class BrandLogoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            email="owner@branding.test", role=User.Role.OWNER,
        )

    def setUp(self):
        cache.delete(BRAND_CACHE_KEY)
        self.addCleanup(cache.delete, BRAND_CACHE_KEY)

    def make_brand(self):
        return Brand.objects.create(
            name="Boreal Vita",
            logo=SimpleUploadedFile("logo.png", LOGO_BYTES, content_type="image/png"),
        )

    def brand_data(self):
        return {
            "name": "Boreal Vita", "base_currency": "PKR", "timezone": "Asia/Karachi",
            "fiscal_year_start_month": "1", "default_country": "PK",
            "primary_color": "#1f7a4d", "active": "on",
        }

    def test_default_logo_is_used_without_a_brand(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, '<link rel="icon" type="image/svg+xml" sizes="any" href="/static/images/boreal-favicon.svg">')
        self.assertNotContains(response, "boreal-mark.svg")

    def test_public_pages_use_the_saved_logo_and_compact_favicon(self):
        brand = self.make_brand()
        logo_url = reverse("brand-logo", args=[brand.pk])

        for route in ("login", "password_reset", "password_reset_done", "password_reset_complete"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, '<link rel="icon" type="image/svg+xml" sizes="any" href="/static/images/boreal-favicon.svg">')
                self.assertContains(response, f'<img src="{logo_url}"')
                self.assertNotContains(response, "boreal-mark.svg")

    def test_sidebar_and_brand_details_show_saved_logo(self):
        brand = self.make_brand()
        self.client.force_login(self.owner)

        response = self.client.get(reverse("brand-detail", args=[brand.pk]))

        logo_url = reverse("brand-logo", args=[brand.pk])
        self.assertContains(response, f'<img class="brand-wordmark" src="{logo_url}"')
        self.assertContains(response, f'<img class="brand-detail-logo" src="{logo_url}"')

    def test_upload_is_persisted_and_refreshes_cached_brand(self):
        brand = self.make_brand()
        original_name = brand.logo.name
        original_page = self.client.get(reverse("login"))  # Prime the cached brand.
        original_version = original_page.context["brand"].updated_at
        self.client.force_login(self.owner)
        data = self.brand_data()
        data["logo"] = SimpleUploadedFile("replacement.png", LOGO_BYTES, content_type="image/png")

        response = self.client.post(reverse("brand-edit", args=[brand.pk]), data)

        self.assertRedirects(response, reverse("brand-detail", args=[brand.pk]))
        brand.refresh_from_db()
        self.assertNotEqual(brand.logo.name, original_name)
        self.assertEqual(bytes(BrandLogo.objects.get(brand=brand).data), LOGO_BYTES)
        response = self.client.get(reverse("login"))
        self.assertContains(response, f'<img src="{reverse("brand-logo", args=[brand.pk])}"')
        self.assertNotEqual(response.context["brand"].updated_at, original_version)
        self.assertEqual(response.context["brand"].logo.name, brand.logo.name)

        cache.delete(BRAND_CACHE_KEY)  # Reload from the database, as after a server restart.
        response = self.client.get(reverse("login"))
        self.assertContains(response, f'<img src="{reverse("brand-logo", args=[brand.pk])}"')
        self.assertEqual(response.context["brand"].logo.name, brand.logo.name)

    @override_settings(DEBUG=False)
    def test_saved_logo_is_publicly_served_with_debug_disabled(self):
        brand = self.make_brand()

        response = self.client.get(reverse("brand-logo", args=[brand.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content, LOGO_BYTES)

    def test_logo_is_kept_in_the_database_not_file_storage(self):
        from django.core.files.storage import default_storage

        brand = self.make_brand()

        logo = BrandLogo.objects.get(brand=brand)
        self.assertEqual(bytes(logo.data), LOGO_BYTES)
        self.assertEqual(logo.content_type, "image/png")
        self.assertFalse(default_storage.exists(brand.logo.name))

    def test_pdfs_can_draw_the_logo_from_the_database(self):
        from .utils import _pdf_link_callback

        brand = self.make_brand()
        path = _pdf_link_callback(brand.logo_url, None)

        with open(path, "rb") as drawn:
            self.assertEqual(drawn.read(), LOGO_BYTES)

    def test_missing_logo_endpoint_returns_not_found(self):
        brand = Brand.objects.create(name="No logo")
        self.assertEqual(self.client.get(reverse("brand-logo", args=[brand.pk])).status_code, 404)

    def test_clearing_logo_restores_default(self):
        brand = self.make_brand()
        self.client.get(reverse("login"))
        self.client.force_login(self.owner)
        data = {**self.brand_data(), "logo-clear": "on"}

        response = self.client.post(reverse("brand-edit", args=[brand.pk]), data)

        self.assertRedirects(response, reverse("brand-detail", args=[brand.pk]))
        brand.refresh_from_db()
        self.assertFalse(brand.logo)
        self.assertFalse(BrandLogo.objects.filter(brand=brand).exists())
        response = self.client.get(reverse("login"))
        self.assertContains(response, '<img src="/static/images/boreal-vita-logo.png"')
