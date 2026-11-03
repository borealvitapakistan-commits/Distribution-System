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
