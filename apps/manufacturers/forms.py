from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from apps.core.models import Brand
from apps.products.models import Product

from .models import (
    Manufacturer,
    ManufacturerOrder,
    ManufacturerOrderItem,
    ManufacturerOrderPayment,
)


class ManufacturerForm(forms.ModelForm):
    class Meta:
        model = Manufacturer
        fields = [
            "name", "phone", "email", "address", "notes",
            "upfront_payment_percentage", "active",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "upfront_payment_percentage": "Payment terms: % due before shipping",
        }


class ManufacturerOrderForm(forms.Form):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.filter(active=True),
    )
    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(active=True),
    )


class ManufacturerOrderItemForm(forms.ModelForm):
    class Meta:
        model = ManufacturerOrderItem
        fields = ["product", "quantity", "unit_price", "source_purchase_order_item"]
        widgets = {
            "source_purchase_order_item": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["product"].queryset = Product.objects.filter(active=True)
        self.fields["source_purchase_order_item"].required = False


ManufacturerOrderItemFormSet = inlineformset_factory(
    ManufacturerOrder,
    ManufacturerOrderItem,
    form=ManufacturerOrderItemForm,
    extra=1,
    can_delete=True,
)


class ManufacturerOrderInvoiceForm(forms.Form):
    """One quantity/price pair per existing line item, plus the invoice
    document itself — recording and approving the invoice is one action."""

    invoice_number = forms.CharField(max_length=100, required=False)
    invoice_file = forms.FileField(required=False)

    ITEM_PRICE_PREFIX = "item_price_"
    ITEM_QTY_PREFIX = "item_qty_"
    ITEM_EXPIRY_PREFIX = "item_expiry_"

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.items = (
            list(order.items.select_related("product", "batch").all())
            if order else []
        )

        if order is not None and not args:
            self.initial["invoice_number"] = order.invoice_number

        for item in self.items:
            self.fields[f"{self.ITEM_QTY_PREFIX}{item.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=4,
                min_value=Decimal("0.0001"),
                initial=item.quantity,
                label=f"{item.product.name} — quantity",
            )
            self.fields[f"{self.ITEM_PRICE_PREFIX}{item.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=2,
                min_value=Decimal("0"),
                initial=item.unit_price,
                label=f"{item.product.name} — unit price",
            )
            self.fields[f"{self.ITEM_EXPIRY_PREFIX}{item.pk}"] = forms.DateField(
                required=False,
                widget=forms.DateInput(attrs={"type": "date"}),
                initial=getattr(item.batch, "expiry_date", None),
                label=f"{item.product.name} — expiry date",
            )

    def item_rows(self):
        """[(item, quantity_bound_field, unit_price_bound_field,
        expiry_date_bound_field), ...] — so the template never needs to
        construct dynamic field names."""
        return [
            (
                item,
                self[f"{self.ITEM_QTY_PREFIX}{item.pk}"],
                self[f"{self.ITEM_PRICE_PREFIX}{item.pk}"],
                self[f"{self.ITEM_EXPIRY_PREFIX}{item.pk}"],
            )
            for item in self.items
        ]

    def get_item_prices(self):
        """{item_pk_as_str: {"quantity": Decimal, "unit_price": Decimal,
        "expiry_date": date | None}}"""
        updates = {}

        for item in self.items:
            updates[str(item.pk)] = {
                "quantity": self.cleaned_data.get(f"{self.ITEM_QTY_PREFIX}{item.pk}"),
                "unit_price": self.cleaned_data.get(f"{self.ITEM_PRICE_PREFIX}{item.pk}"),
                "expiry_date": self.cleaned_data.get(f"{self.ITEM_EXPIRY_PREFIX}{item.pk}"),
            }

        return updates


class ManufacturerOrderOutcomeForm(forms.Form):
    outcome = forms.ChoiceField(
        choices=[
            (ManufacturerOrder.Status.GOOD, ManufacturerOrder.Status.GOOD.label),
            (ManufacturerOrder.Status.DISPUTED, ManufacturerOrder.Status.DISPUTED.label),
            (ManufacturerOrder.Status.REFUNDED, ManufacturerOrder.Status.REFUNDED.label),
        ],
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Note (why disputed/refunded)",
    )


class ManufacturerOrderPaymentForm(forms.ModelForm):
    class Meta:
        model = ManufacturerOrderPayment
        fields = ["amount", "paid_at", "note"]
        widgets = {
            "paid_at": forms.DateInput(attrs={"type": "date"}),
        }
