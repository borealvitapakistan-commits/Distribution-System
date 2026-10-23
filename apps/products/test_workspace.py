from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

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
