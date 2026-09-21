from decimal import Decimal, InvalidOperation
from django import forms
from django.forms import inlineformset_factory
from apps.owner_inventory.services import available_batches_fefo
from apps.products.models import Product
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderPayment


class ProductPriceSelect(forms.Select):
    """A <select> whose <option>s carry data-price, so the create-PO page
    can show a live running total in JS without a server round trip."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        raw_value = getattr(value, "value", value)

        if raw_value:
            price = (
                Product.objects
                .filter(pk=raw_value)
                .values_list("base_retail_price", flat=True)
                .first()
            )

            if price is not None:
                option["attrs"]["data-price"] = str(price)

        return option


class PurchaseOrderItemForm(forms.ModelForm):
    requested_price = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        label="Requested price (optional)",
        help_text="Leave blank if you don't know the price.",
    )

    class Meta:
        model = PurchaseOrderItem
        fields = ["product", "quantity_requested"]
        widgets = {"product": ProductPriceSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["product"].queryset = Product.objects.filter(active=True)


PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderItem,
    form=PurchaseOrderItemForm,
    extra=1,
    can_delete=True,
)


class ShipPurchaseOrderItemForm(forms.Form):
    """One quantity field per available batch (warehouse) for this
    product — the Owner can split a single shipment across as many of
    them as they want in one submission, not just pick one."""

    BATCH_FIELD_PREFIX = "batch_qty_"

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.batches = list(available_batches_fefo(product=product)) if product else []

        for batch in self.batches:
            expiry = f"expires {batch.expiry_date}" if batch.expiry_date else "no expiry set"
            number = f"{batch.batch_number} — " if batch.batch_number else ""

            self.fields[f"{self.BATCH_FIELD_PREFIX}{batch.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=4,
                min_value=Decimal("0"),
                required=False,
                initial=Decimal("0"),
                label=f"{number}{batch.location.name} — {expiry} — {batch.quantity_remaining} available",
            )

    def get_allocations(self):
        """[(batch, quantity), ...] for every batch given a quantity > 0.
        Call only after is_valid() has returned True."""
        allocations = []

        for batch in self.batches:
            raw = self.cleaned_data.get(f"{self.BATCH_FIELD_PREFIX}{batch.pk}")

            try:
                quantity = Decimal(raw) if raw not in (None, "") else Decimal("0")
            except InvalidOperation:
                quantity = Decimal("0")

            if quantity > 0:
                allocations.append((batch, quantity))

        return allocations


class PricingForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["tax_percentage", "shipping_amount"]


class ReceivePurchaseOrderItemForm(forms.Form):
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0001"),
    )


class DeclinePurchaseOrderForm(forms.Form):
    comment = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class OwnerCommentForm(forms.Form):
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class RecordPaymentForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderPayment
        fields = ["amount", "paid_at", "proof", "note"]
        widgets = {
            "paid_at": forms.DateInput(attrs={"type": "date"}),
        }


class RejectPaymentForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
