import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.products.services import create_product

from .services import create_manufacturer, create_request_to_quote

WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


class ShareToSlackTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="slack@test.local", role="OWNER", first_name="Sam")
        maker = create_manufacturer(actor=self.owner, name="Alpha Labs")
        product = create_product(actor=self.owner, name="Moringa", sku="SLK-MOR")
        self.order = create_request_to_quote(
            actor=self.owner, manufacturer=maker,
            items=[{"product": product, "bottle_size": 40, "quantity": "100", "unit_price": "9.5"}],
        )
        self.client.force_login(self.owner)
        self.url = reverse("manufacturer-order-share-slack", args=[self.order.pk])

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK)
    @mock.patch("apps.manufacturers.slack.urllib.request.urlopen")
    def test_posts_order_lines_note_and_link(self, urlopen):
        response = self.client.post(self.url, {"note": "Can we get 5% off?"})
        self.assertRedirects(response, reverse("manufacturer-order-detail", args=[self.order.pk]))
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, WEBHOOK)
        payload = json.dumps(json.loads(request.data))
        for text in ("Moringa", "40 capsules", "Rs 950.00", "Can we get 5% off?", self.order.po_number,
                     f"/owner/manufacturer-orders/{self.order.pk}/", "Sam"):
            self.assertIn(text, payload)

    @override_settings(SLACK_WEBHOOK_URL="")
    @mock.patch("apps.manufacturers.slack.urllib.request.urlopen")
    def test_not_configured_shows_setup_help_and_posts_nothing(self, urlopen):
        page = self.client.get(reverse("manufacturer-order-detail", args=[self.order.pk]))
        self.assertContains(page, "SLACK_WEBHOOK_URL")
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, "Slack isn&#x27;t connected yet")
        urlopen.assert_not_called()

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK)
    def test_distributors_cannot_share(self):
        self.client.force_login(User.objects.create_user(email="d@slack.test", role="DISTRIBUTOR"))
        self.assertEqual(self.client.post(self.url).status_code, 403)


class OrderFromProductPageTests(TestCase):
    def test_order_link_prefills_manufacturer_and_product(self):
        owner = User.objects.create_user(email="link@test.local", role="OWNER")
        maker = create_manufacturer(actor=owner, name="Alpha Labs")
        product = create_product(actor=owner, name="Moringa", sku="LNK-MOR")
        self.client.force_login(owner)
        response = self.client.get(reverse("manufacturer-order-create"),
                                   {"product": product.pk, "manufacturer": maker.pk})
        self.assertEqual(response.context["form"]["manufacturer"].value(), maker.pk)
        self.assertEqual(str(response.context["formset"].forms[0]["product"].value()), str(product.pk))
        bad = self.client.get(reverse("manufacturer-order-create"), {"manufacturer": "not-a-uuid"})
        self.assertIsNone(bad.context["form"]["manufacturer"].value())
