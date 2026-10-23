from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.owner_inventory.models import StockBalance
from apps.owner_warehouse.models import Location

from .lifecycle import apply_product_action, deletion_review
from .models import Product
from .services import create_product
from .workspace import inventory_summary, with_inventory


class ProductWorkspaceRulesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="workspace@test.local", role="OWNER")
        self.product = create_product(actor=self.owner, name="Test supplement", sku="WORKSPACE")

    def balance(self, code, quantity, kind="OWN", sellable=True, active=True):
        location = Location.objects.create(
            code=code, name=code, location_type=kind, on_book=True,
            is_sellable=sellable, active=active,
        )
        StockBalance.objects.create(product=self.product, location=location, quantity=quantity)
        return location

    def test_inventory_separates_sellable_unavailable_and_transit(self):
        self.balance("Sellable", 12)
        self.balance("Held", 3, sellable=False)
        self.balance("Transit", 4, kind="TRANSIT", sellable=False)
        self.balance("Inactive", 2, active=False)
        summary = inventory_summary(self.product)
        self.assertEqual(summary["totals"], {
            "available":Decimal(12), "unavailable":Decimal(5),
            "on_hand":Decimal(17), "in_transit":Decimal(4),
        })
        self.assertEqual(with_inventory(Product.objects.all()).get(pk=self.product.pk).inventory_available, 12)

    def test_product_without_balances_has_zero_inventory(self):
        self.assertEqual(with_inventory(Product.objects.all()).get(pk=self.product.pk).inventory_available, 0)

    def test_delete_unused_product_and_audit(self):
        product_id = self.product.pk
        token = deletion_review([self.product])["token"]
        result = apply_product_action(actor=self.owner, product_ids=[product_id], action="delete", token=token)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(Product.objects.filter(pk=product_id).exists())
        self.assertTrue(AuditEvent.objects.filter(action="product.deleted", object_id=product_id).exists())

    def test_delete_archives_stock_history_instead_of_removing_it(self):
        self.balance("Keep", 8)
        token = deletion_review([self.product])["token"]
        result = apply_product_action(actor=self.owner, product_ids=[self.product.pk], action="delete", token=token)
        self.product.refresh_from_db()
        self.assertEqual(result["archived"], 1)
        self.assertFalse(self.product.active)
        self.assertIsNotNone(self.product.archived_at)
        self.assertEqual(self.product.stock_balances.get().quantity, 8)

    def test_stale_review_and_missing_confirmation_token_cannot_delete(self):
        token = deletion_review([self.product])["token"]
        self.balance("New history", 1)
        for candidate in (token, None, "tampered"):
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                apply_product_action(actor=self.owner, product_ids=[self.product.pk], action="delete", token=candidate)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_hold_and_reactivate(self):
        for action, expected in (("hold", False), ("active", True)):
            apply_product_action(actor=self.owner, product_ids=[self.product.pk], action=action)
            self.product.refresh_from_db()
            self.assertEqual(self.product.active, expected)

    def test_nonowner_and_unknown_selections_cannot_modify_products(self):
        user = User.objects.create_user(email="buyer@workspace.test", role="DISTRIBUTOR")
        with self.assertRaises(PermissionDenied):
            apply_product_action(actor=user, product_ids=[self.product.pk], action="hold")
        with self.assertRaises(ValidationError):
            apply_product_action(actor=self.owner, product_ids=[self.product.pk, "bad"], action="hold")
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)

    def test_formatted_description_is_sanitized_and_safe_to_edit(self):
        from .description import sanitize_description

        dirty = '<p onclick="bad()">Good <strong>text</strong><script>alert(1)</script><img src=x onerror=bad()><a href="java&#115;cript:bad()">link</a></p>'
        cleaned = sanitize_description(dirty)
        self.assertIn('<strong>text</strong>', cleaned)
        for text in ('onclick', 'script', '<img', 'alert(', 'javascript:'):
            self.assertNotIn(text, cleaned)
        self.assertEqual(sanitize_description(cleaned), cleaned)
        product = create_product(actor=self.owner, sku="HTML-SAFE", name="Safe", body_html=dirty)
        self.assertEqual(product.body_html, cleaned)

    def test_list_inventory_sort_and_pagination(self):
        self.balance("Stock", 5)
        create_product(actor=self.owner, sku="SECOND", name="Another product")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("product-list"), {"sort":"-inventory"})
        self.assertEqual(list(response.context["products"])[0].pk, self.product.pk)
        self.assertEqual(response.context["page_obj"].paginator.count, 2)
        self.assertContains(response, "5 in stock")

    def test_delete_page_and_bulk_action_require_review_before_mutation(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("product-delete", args=[self.product.pk]))
        token = response.context["review"]["token"]
        payload = {"products":[self.product.pk], "action":"delete", "review_token":token}
        response = self.client.post(reverse("product-actions"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        payload["confirmed"] = "yes"
        self.assertRedirects(self.client.post(reverse("product-actions"), payload), reverse("product-list"))
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_product_actions_require_owner_and_csrf(self):
        from django.test import Client

        self.assertEqual(self.client.post(reverse("product-actions")).status_code, 302)
        self.client.force_login(User.objects.create_user(email="other@workspace.test", role="DISTRIBUTOR"))
        self.assertEqual(self.client.get(reverse("product-delete", args=[self.product.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("product-actions")).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(reverse("product-actions"), {"products":[self.product.pk], "action":"hold"}).status_code, 403)

    def test_product_page_saves_details_without_leaving_the_page(self):
        from .forms import ProductForm

        self.client.force_login(self.owner)
        form = ProductForm(instance=self.product)
        data = {name: value if value is not None else "" for name, value in form.initial.items()
                if name in form.fields and name not in ("image", "allow_fractional_quantity")}
        data.update({"name":"Updated supplement", "base_retail_price":"125.50", "active":"False",
                     "ingredients-TOTAL_FORMS":"0", "ingredients-INITIAL_FORMS":"0",
                     "ingredients-MIN_NUM_FORMS":"0", "ingredients-MAX_NUM_FORMS":"1000"})
        response = self.client.post(reverse("product-detail", args=[self.product.pk]), data)
        self.assertRedirects(response, reverse("product-detail", args=[self.product.pk]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated supplement")
        self.assertEqual(self.product.base_retail_price, Decimal("125.50"))
        self.assertFalse(self.product.active)

    def test_invalid_editor_submission_keeps_values_and_ingredient_errors(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("product-detail", args=[self.product.pk]), {
            "name":"Keep this title", "sku":"WORKSPACE", "base_retail_price":"-1",
            "ingredients-TOTAL_FORMS":"1", "ingredients-INITIAL_FORMS":"0",
            "ingredients-0-strength":"orphaned strength", "ingredients-0-sort_order":"0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["name"].value(), "Keep this title")
        self.assertContains(response, "Please check the highlighted fields.")

    def test_main_image_can_be_cleared_from_product_editor(self):
        import tempfile
        from django.core.files.base import ContentFile
        from django.test import override_settings
        from .forms import ProductForm
        from .tests import fake_image

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            _, png = fake_image("")
            self.product.image.save("main.png", ContentFile(png))
            self.product.shopify_image_url = "https://example.com/original.jpg"
            self.product.save(update_fields=["shopify_image_url"])
            form = ProductForm(instance=self.product)
            data = {name:value if value is not None else "" for name,value in form.initial.items()
                    if name in form.fields and name not in ("image", "allow_fractional_quantity")}
            data.update({"image-clear":"on", "active":"True",
                         "ingredients-TOTAL_FORMS":"0", "ingredients-INITIAL_FORMS":"0",
                         "ingredients-MIN_NUM_FORMS":"0", "ingredients-MAX_NUM_FORMS":"1000"})
            self.client.force_login(self.owner)
            response = self.client.post(reverse("product-detail", args=[self.product.pk]), data)
            self.assertRedirects(response, reverse("product-detail", args=[self.product.pk]))
            self.product.refresh_from_db()
            self.assertFalse(self.product.image)
            self.assertEqual(self.product.shopify_image_url, "")
