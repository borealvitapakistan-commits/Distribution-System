from django.test import TestCase

# Create your tests here.


class NavigationSectionTests(TestCase):
    """Every page lights up the sidebar item it belongs to — including
    step pages that used to be missed (counter-offer, change, conversation)."""

    def test_owner_pages_map_to_their_section(self):
        from apps.core.navigation import section_for

        cases = {
            "manufacturer-order-counter": "purchase-orders",
            "manufacturer-order-change": "purchase-orders",
            "product-package-edit": "products",
            "inventory-allocate": "inventory",
            "stock-batch-list": "batches",
            "owner-finance-ledger": "finance",
            "owner-dashboard": "dashboard",
            "brand-list": "partners",
            "brand-edit": "partners",
            "manufacturer-list": "partners",
            "manufacturer-delete": "partners",
            "owner-list": "partners",
            "owner-create": "partners",
            "owner-profile": "",
        }
        for url_name, section in cases.items():
            self.assertEqual(section_for(url_name, is_owner=True), section, url_name)

    def test_only_owners_get_a_section(self):
        from apps.core.navigation import section_for

        self.assertEqual(section_for("owner-dashboard", is_owner=False), "")


class SpeedHelperTests(TestCase):
    def test_count_many_counts_in_one_round_trip(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.accounts.models import User
        from apps.core.querysets import count_many

        User.objects.create_user(email="a@speed.test", role="OWNER")
        User.objects.create_user(email="b@speed.test", role="OWNER", is_active=False)

        with CaptureQueriesContext(connection) as queries:
            counts = count_many(
                active=User.objects.filter(is_active=True),
                everyone=User.objects.all(),
                nobody=User.objects.none(),
            )

        self.assertEqual(counts, {"active": 1, "everyone": 2, "nobody": 0})
        self.assertEqual(len(queries), 1)

    def test_asset_urls_carry_a_content_version(self):
        from apps.core.templatetags.assets import asset

        self.assertRegex(asset("css/app.css"), r"^/static/css/app\.css\?v=[0-9a-f]{10}$")
        self.assertEqual(asset("css/does-not-exist.css"), "/static/css/does-not-exist.css")

    def test_signed_in_user_is_cached_and_refreshed_on_save(self):
        from django.core.cache import cache

        from apps.accounts.backends import CachedModelBackend, _key
        from apps.accounts.models import User

        user = User.objects.create_user(email="c@speed.test", role="OWNER")
        backend = CachedModelBackend()
        backend.get_user(user.pk)
        self.assertIsNotNone(cache.get(_key(user.pk)))

        user.is_active = False
        user.save()
        self.assertIsNone(cache.get(_key(user.pk)))
        self.assertIsNone(backend.get_user(user.pk))


class PageCacheTests(TestCase):
    """Repeat visits come from memory, but never once anything changed."""

    def setUp(self):
        from django.conf import settings
        from django.core.cache import cache
        from django.test import override_settings

        from apps.accounts.models import User

        cache.clear()
        self.addCleanup(cache.clear)
        middleware = override_settings(
            MIDDLEWARE=[*settings.MIDDLEWARE, "apps.core.page_cache.PageCacheMiddleware"]
        )
        middleware.enable()
        self.addCleanup(middleware.disable)
        self.owner = User.objects.create_user(email="owner@cache.test", role="OWNER", password="pw-12345678")
        self.client.force_login(self.owner)

    def test_second_visit_is_served_from_memory(self):
        first = self.client.get("/owner/manufacturers/")
        second = self.client.get("/owner/manufacturers/")

        self.assertFalse(first.has_header("X-Page-Cache"))
        self.assertEqual(second["X-Page-Cache"], "hit")
        self.assertEqual(first.content, second.content)

    def test_any_change_through_a_form_shows_fresh_pages(self):
        from django.urls import reverse

        self.client.get("/owner/manufacturers/")
        self.client.post(reverse("manufacturer-create"), {"name": "Fresh Labs", "prefix": "FL"})

        response = self.client.get("/owner/manufacturers/")
        self.assertFalse(response.has_header("X-Page-Cache"))
        self.assertContains(response, "Fresh Labs")

    def test_downloads_and_signed_out_visitors_are_not_cached(self):
        from django.test import Client

        for _ in range(2):
            download = self.client.get("/owner/manufacturers/?format=csv")
            visitor = Client().get("/owner/manufacturers/")
        self.assertFalse(download.has_header("X-Page-Cache"))
        self.assertFalse(visitor.has_header("X-Page-Cache"))

    def test_signing_in_again_never_reuses_another_sessions_page(self):
        self.client.get("/owner/manufacturers/")
        self.client.logout()
        self.client.force_login(self.owner)

        self.assertFalse(self.client.get("/owner/manufacturers/").has_header("X-Page-Cache"))
