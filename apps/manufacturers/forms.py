from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from apps.core.forms import PaidQuestionForm
from apps.core.models import Brand
from apps.owner_warehouse.models import Location
from apps.products.models import BottleSize, Product

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
        label="Vendor",
        help_text="The Manufacturer this goes to — the manufacturer is the vendor.",
    )
    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(active=True),
        help_text="The Request to Quote and Purchase Order use this brand's logo and colour.",
    )
    terms = forms.CharField(
        required=False,
        label="Terms and conditions",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Printed at the bottom of the document.",
    )


class ManufacturerOrderItemForm(forms.ModelForm):
    class Meta:
        model = ManufacturerOrderItem
        fields = [
            "product", "bottle_size", "quantity", "unit_price",
            "source_purchase_order_item",
        ]
        widgets = {
            "source_purchase_order_item": forms.HiddenInput(),
            "unit_price": forms.NumberInput(
                attrs={"step": "0.001", "placeholder": "Optional", "class": "doc-line-input"}
            ),
            "quantity": forms.NumberInput(
                attrs={"step": "1", "min": "1", "placeholder": "0", "class": "doc-line-input"}
            ),
        }
        labels = {
            "bottle_size": "Bottle",
            "quantity": "Qty (bottles)",
            "unit_price": "Unit price",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["product"].queryset = Product.objects.filter(active=True)
        self.fields["product"].empty_label = "Select product…"
        self.fields["bottle_size"].choices = [("", "Bottle size — none")] + list(BottleSize.choices)
        self.fields["unit_price"].required = False
        self.fields["source_purchase_order_item"].required = False

    def validate_unique(self):
        # The order isn't saved through this form; the service checks
        # product + bottle size uniqueness across the whole order.
        pass


ManufacturerOrderItemFormSet = inlineformset_factory(
    ManufacturerOrder,
    ManufacturerOrderItem,
    form=ManufacturerOrderItemForm,
    extra=1,
    can_delete=True,
)


class ManufacturerQuoteForm(forms.Form):
    """The Manufacturer's reply to a Request to Quote: their document plus
    the price (and quantity, if they changed it) per line. A line can be
    dropped if they can't supply it, and its quoted price can be saved
    as our new price for that product and bottle size."""

    PRICE_PREFIX = "quote_price_"
    QTY_PREFIX = "quote_qty_"
    REMOVE_PREFIX = "quote_remove_"
    SAVE_PREFIX = "quote_save_"

    quote_file = forms.FileField(
        required=False,
        label="Quote document (real prices)",
        help_text="The document or picture with the Manufacturer's real prices, as the manufacturer sent it.",
    )

    def __init__(self, *args, order=None, saved_prices=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.order = order
        self.saved_prices = saved_prices or {}
        self.items = (
            list(order.items.select_related("product").all()) if order else []
        )
        self.fields["quote_file"].required = not (order and order.quote_file)

        for item in self.items:
            self.fields[f"{self.PRICE_PREFIX}{item.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=3,
                min_value=Decimal("0"),
                required=False,
                initial=item.unit_price,
                widget=forms.NumberInput(attrs={"step": "0.001", "class": "doc-line-input"}),
                label=f"{item.product.name} — quoted price",
            )
            self.fields[f"{self.QTY_PREFIX}{item.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=4,
                min_value=Decimal("0.0001"),
                initial=f"{item.quantity.normalize():f}",
                widget=forms.NumberInput(attrs={"step": "1", "min": "1", "class": "doc-line-input"}),
                label=f"{item.product.name} — quantity",
            )
            self.fields[f"{self.REMOVE_PREFIX}{item.pk}"] = forms.BooleanField(
                required=False, label="Drop"
            )
            self.fields[f"{self.SAVE_PREFIX}{item.pk}"] = forms.BooleanField(
                required=False, label="Save as our new price"
            )

    def saved_price_for(self, item):
        if not item.bottle_size:
            return None
        value = self.saved_prices.get(str(item.product_id), {}).get(str(item.bottle_size))
        return None if value is None else Decimal(value)

    def item_rows(self):
        return [
            {
                "item": item,
                "saved_price": self.saved_price_for(item),
                "price": self[f"{self.PRICE_PREFIX}{item.pk}"],
                "quantity": self[f"{self.QTY_PREFIX}{item.pk}"],
                "remove": self[f"{self.REMOVE_PREFIX}{item.pk}"],
                "save": self[f"{self.SAVE_PREFIX}{item.pk}"],
            }
            for item in self.items
        ]

    def clean(self):
        cleaned_data = super().clean()

        for item in self.items:
            if cleaned_data.get(f"{self.REMOVE_PREFIX}{item.pk}"):
                continue
            if cleaned_data.get(f"{self.PRICE_PREFIX}{item.pk}") is None:
                self.add_error(
                    f"{self.PRICE_PREFIX}{item.pk}",
                    "Enter the price the Manufacturer quoted.",
                )

        return cleaned_data

    def item_updates(self):
        return {
            str(item.pk): {
                "unit_price": self.cleaned_data.get(f"{self.PRICE_PREFIX}{item.pk}"),
                "quantity": self.cleaned_data.get(f"{self.QTY_PREFIX}{item.pk}"),
            }
            for item in self.items
        }

    def removed_item_ids(self):
        return [
            item.pk for item in self.items
            if self.cleaned_data.get(f"{self.REMOVE_PREFIX}{item.pk}")
        ]

    def save_price_item_ids(self):
        return [
            item.pk for item in self.items
            if self.cleaned_data.get(f"{self.SAVE_PREFIX}{item.pk}")
        ]


class ManufacturerOrderInvoiceForm(forms.Form):
    """The Manufacturer's invoice: their reference and document, shipping
    and tax, the warehouse the goods go into, plus one
    quantity/price/expiry row per line item — what actually arrived. Our
    own invoice number is generated, not typed."""

    ITEM_PRICE_PREFIX = "item_price_"
    ITEM_QTY_PREFIX = "item_qty_"
    ITEM_EXPIRY_PREFIX = "item_expiry_"

    warehouse = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        label="Receive into warehouse",
        empty_label="Select warehouse…",
        help_text="The stock goes straight into this warehouse.",
    )
    supplier_invoice_ref = forms.CharField(
        max_length=100,
        required=False,
        label="Manufacturer's invoice no.",
        help_text="Optional — the number printed on their invoice.",
    )
    invoice_file = forms.FileField(
        required=False,
        label="Invoice document",
        help_text="The Manufacturer's invoice, as they gave it to us.",
    )
    shipping_amount = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        label="Shipping",
    )
    tax_percentage = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        label="Tax %",
        help_text="The tax charged on the invoice.",
    )

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["warehouse"].queryset = (
            Location.objects.filter(location_type=Location.LocationType.OWN, active=True)
            .select_related("inventory")
            .order_by("inventory__name", "name")
        )
        self.fields["warehouse"].label_from_instance = lambda location: (
            f"{location.inventory.name} — {location.name}"
            if location.inventory_id else location.name
        )

        if order is not None:
            self.fields["shipping_amount"].initial = order.shipping_amount
            self.fields["tax_percentage"].initial = order.tax_percentage

        self.items = (
            list(order.items.select_related("product", "batch").all())
            if order else []
        )

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
                decimal_places=3,
                min_value=Decimal("0"),
                initial=item.unit_price,
                label=f"{item.product.name} — unit price",
            )
            self.fields[f"{self.ITEM_EXPIRY_PREFIX}{item.pk}"] = forms.DateField(
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

    def projected_grand_total(self, order):
        """What the order will cost once this invoice is saved — worked
        out the same way as ManufacturerOrder.grand_total, from the
        invoiced quantity and price per line."""
        cent = Decimal("0.01")
        subtotal = sum(
            (
                (row["quantity"] * row["unit_price"]).quantize(cent)
                for row in self.get_item_prices().values()
            ),
            Decimal("0.00"),
        ).quantize(cent)
        tax_percentage, shipping_amount = self.charges(order)
        tax = (subtotal * tax_percentage / Decimal("100")).quantize(cent)
        return subtotal + tax + shipping_amount

    def charges(self, order):
        """(tax_percentage, shipping_amount) — as invoiced, or the
        order's current values where left blank."""
        tax = self.cleaned_data.get("tax_percentage")
        shipping = self.cleaned_data.get("shipping_amount")
        return (
            order.tax_percentage if tax is None else tax,
            order.shipping_amount if shipping is None else shipping,
        )


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
        fields = ["amount", "paid_at", "proof", "note"]
        widgets = {
            "paid_at": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "paid_at": "Date paid",
            "proof": "Payment proof",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proof"].required = True


class ManufacturerAdvancePaymentForm(PaidQuestionForm):
    QUESTION = "Are you paying in advance?"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["answer"].choices = [("yes", "Advance"), ("no", "No advance")]


class ManufacturerOrderReceiveForm(forms.Form):
    """The final payment for whatever is still owed on the invoiced
    total. An order can't be received until it's paid in full, so these
    are required whenever anything is owed — the view decides that,
    since it depends on the invoice."""

    amount = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        label="Amount paid",
    )
    paid_at = forms.DateField(
        label="Date paid",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    proof = forms.FileField(
        label="Payment proof",
        required=False,
        help_text="Bank slip, screenshot, etc.",
    )
    note = forms.CharField(max_length=255, required=False)

    def require_full_payment(self, remaining):
        """Adds errors unless the full `remaining` amount is paid, with
        its date and proof."""
        for name in ("amount", "paid_at", "proof"):
            if not self.cleaned_data.get(name):
                self.add_error(name, "Required — the order must be paid in full.")

        amount = self.cleaned_data.get("amount")
        if amount and amount != remaining:
            self.add_error(
                "amount", f"Pay the full remaining amount: {remaining}."
            )
