from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from apps.core.forms import PaidQuestionForm
from apps.core.models import Brand
from apps.products.models import BottleSize, Product

from .models import (
    Manufacturer,
    ManufacturerOrder,
    ManufacturerOrderItem,
    ManufacturerOrderPayment,
)


def validate_whole_bottles(value):
    if value is not None and value != value.to_integral_value():
        raise forms.ValidationError("Enter a whole number of bottles.")


def bottle_quantity_field(*, initial=None, label="Quantity", css_class=None):
    """Number of bottles: a whole number, at least 1. min_value has to be
    1 too — Django writes it into the input's min, and the browser counts
    each step from there, so a min of 0.0001 makes 40 "invalid"."""
    attrs = {"step": "1"}
    if css_class:
        attrs["class"] = css_class
    return forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("1"),
        initial=initial,
        validators=[validate_whole_bottles],
        widget=forms.NumberInput(attrs=attrs),
        label=label,
    )


class ManufacturerForm(forms.ModelForm):
    class Meta:
        model = Manufacturer
        fields = [
            "name", "prefix", "logo", "phone", "email", "address", "notes", "active",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class ManufacturerDeletionForm(forms.Form):
    review_token = forms.CharField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(
        label="I have reviewed these choices and want to delete this manufacturer.",
        error_messages={"required": "Confirm your choices before deleting the manufacturer."},
    )

    def __init__(self, *args, manufacturer, review, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["review_token"].initial = review["token"]
        self.rows = []
        targets = Manufacturer.objects.filter(
            active=True, archived_at__isnull=True,
        ).exclude(pk=manufacturer.pk)
        for row in review["products"]:
            product = row["product"]
            action_name = f"action_{product.pk.hex}"
            target_name = f"target_{product.pk.hex}"
            self.fields[action_name] = forms.ChoiceField(
                label=f"What should happen to {product.name}?",
                choices=[("", "Choose an action…"), ("reassign", "Reassign to another manufacturer"),
                         ("hold", "Put on hold"), ("delete", "Delete product")],
                widget=forms.Select(attrs={"data-deletion-action": ""}),
            )
            self.fields[target_name] = forms.ModelChoiceField(
                label="Replacement manufacturer", queryset=targets, required=False,
                empty_label="Choose a manufacturer…",
            )
            self.rows.append({**row, "action": self[action_name], "target": self[target_name]})

    def clean(self):
        cleaned = super().clean()
        for row in self.rows:
            if cleaned.get(row["action"].name) == "reassign" and not cleaned.get(row["target"].name):
                self.add_error(row["target"].name, "Choose a replacement manufacturer for this product.")
        return cleaned

    def product_actions(self):
        return {
            str(row["product"].pk): {
                "action": self.cleaned_data[row["action"].name],
                "target_id": str(self.cleaned_data[row["target"].name].pk)
                if self.cleaned_data.get(row["target"].name) else None,
            }
            for row in self.rows
        }


class ManufacturerOrderForm(forms.Form):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.filter(active=True),
        label="Manufacturer",
        empty_label="Choose a manufacturer…",
        help_text="Who you're asking for prices.",
    )
    brand = forms.ModelChoiceField(
        queryset=Brand.objects.filter(active=True),
        label="Brand",
        empty_label="Choose a brand…",
        help_text="The document is printed with this brand's logo and colour.",
    )
    terms = forms.CharField(
        required=False,
        label="Terms and conditions",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Printed at the bottom of the document.",
    )
    message = forms.CharField(
        required=False,
        label="Message to the manufacturer",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Printed on the Request to Quote, and kept in the order's conversation history.",
    )

    # Who the document is addressed to and from. Filled in from the
    # Manufacturer and Brand chosen above; changes apply to this order only.
    vendor_name = forms.CharField(required=False, max_length=200, label="Name")
    vendor_address = forms.CharField(required=False, label="Address", widget=forms.Textarea(attrs={"rows": 2}))
    vendor_email = forms.CharField(required=False, max_length=254, label="Email")
    vendor_phone = forms.CharField(required=False, max_length=50, label="Phone")
    ship_to_name = forms.CharField(required=False, max_length=200, label="Name")
    ship_to_address = forms.CharField(required=False, label="Address", widget=forms.Textarea(attrs={"rows": 2}))
    ship_to_email = forms.CharField(required=False, max_length=254, label="Email")
    ship_to_phone = forms.CharField(required=False, max_length=50, label="Phone")

    PARTY_FIELDS = (
        "vendor_name", "vendor_address", "vendor_email", "vendor_phone",
        "ship_to_name", "ship_to_address", "ship_to_email", "ship_to_phone",
    )

    def party_details(self):
        """The Vendor / Ship to details as typed, for this order only.
        Fields the page didn't send at all are left out, so they fall back
        to the Manufacturer's / Brand's own details."""
        return {
            name: (self.cleaned_data.get(name) or "").strip()
            for name in self.PARTY_FIELDS
            if name in self.data
        }


class ManufacturerOrderItemForm(forms.ModelForm):
    save_price = forms.BooleanField(
        required=False,
        label="Remember this price",
        help_text="Keep this price for this product and bottle size, so it's filled in next time.",
    )

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
        self.fields["product"].empty_label = "Choose a product…"
        self.fields["bottle_size"].choices = [("", "Bottle size (optional)")] + list(BottleSize.choices)
        self.fields["unit_price"].required = False
        self.fields["source_purchase_order_item"].required = False
        self.fields["quantity"] = bottle_quantity_field(
            label="Qty (bottles)", css_class="doc-line-input"
        )
        self.fields["quantity"].widget.attrs["placeholder"] = "e.g. 100"

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get("save_price") and not cleaned_data.get("DELETE"):
            if not cleaned_data.get("bottle_size"):
                self.add_error("save_price", "Pick a bottle size to save the price for.")
            elif not cleaned_data.get("unit_price"):
                self.add_error("save_price", "Enter a price to save.")

        return cleaned_data

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
    message = forms.CharField(
        required=False,
        label="What the manufacturer said",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Their reply in their words — kept in the order's conversation history.",
    )

    def __init__(self, *args, order=None, saved_prices=None, latest_lines=None, **kwargs):
        """latest_lines: {item_pk_as_str: OrderRevisionLine} — the last
        prices on the table (e.g. our counter-offer), so a reply that just
        accepts them needs no retyping."""
        super().__init__(*args, **kwargs)

        self.order = order
        self.saved_prices = saved_prices or {}
        latest_lines = latest_lines or {}
        self.items = (
            list(order.items.select_related("product").all()) if order else []
        )
        self.fields["quote_file"].required = not (order and order.quote_file)

        for item in self.items:
            latest = latest_lines.get(str(item.pk)) or item
            self.fields[f"{self.PRICE_PREFIX}{item.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=3,
                min_value=Decimal("0"),
                required=False,
                initial=latest.unit_price,
                widget=forms.NumberInput(attrs={"step": "0.001", "class": "doc-line-input"}),
                label=f"{item.product.name} — quoted price",
            )
            self.fields[f"{self.QTY_PREFIX}{item.pk}"] = bottle_quantity_field(
                initial=f"{latest.quantity.normalize():f}",
                label=f"{item.product.name} — quantity",
                css_class="doc-line-input",
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


class OrderLinesForm(forms.Form):
    """A price and quantity per order line plus a message — shared by
    our counter-offer and by changing a sent Purchase Order."""

    PRICE_PREFIX = "line_price_"
    QTY_PREFIX = "line_qty_"
    REMOVE_PREFIX = "line_remove_"

    allow_remove = False
    price_label = "Price"

    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, order=None, initial_lines=None, **kwargs):
        """initial_lines: {item_pk_as_str: OrderRevisionLine} to start
        from instead of the order's own prices."""
        super().__init__(*args, **kwargs)

        self.items = list(order.items.select_related("product")) if order else []
        initial_lines = initial_lines or {}

        for item in self.items:
            start = initial_lines.get(str(item.pk)) or item
            self.fields[f"{self.PRICE_PREFIX}{item.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=3,
                min_value=Decimal("0"),
                required=False,
                initial=start.unit_price,
                widget=forms.NumberInput(attrs={"step": "0.001"}),
                label=f"{item.product.name} — {self.price_label.lower()}",
            )
            self.fields[f"{self.QTY_PREFIX}{item.pk}"] = bottle_quantity_field(
                initial=f"{start.quantity.normalize():f}",
                label=f"{item.product.name} — quantity",
            )
            if self.allow_remove:
                self.fields[f"{self.REMOVE_PREFIX}{item.pk}"] = forms.BooleanField(
                    required=False, label="Drop"
                )

    def item_rows(self):
        return [
            {
                "item": item,
                "price": self[f"{self.PRICE_PREFIX}{item.pk}"],
                "quantity": self[f"{self.QTY_PREFIX}{item.pk}"],
                "remove": self[f"{self.REMOVE_PREFIX}{item.pk}"] if self.allow_remove else None,
            }
            for item in self.items
        ]

    def clean(self):
        cleaned_data = super().clean()

        for item in self.items:
            if cleaned_data.get(f"{self.REMOVE_PREFIX}{item.pk}"):
                continue
            if cleaned_data.get(f"{self.PRICE_PREFIX}{item.pk}") is None:
                self.add_error(f"{self.PRICE_PREFIX}{item.pk}", "Enter a price.")

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


class CounterOfferForm(OrderLinesForm):
    price_label = "Our price"

    attachment = forms.FileField(
        required=False,
        label="Attachment",
        help_text="Optional — e.g. a screenshot of the message you sent.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].label = "Message to the manufacturer"
        self.fields["message"].help_text = "Why you're asking for these prices."


class PurchaseOrderEditForm(OrderLinesForm):
    allow_remove = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].label = "Reason for the change"
        self.fields["message"].help_text = "Saved with this version of the Purchase Order."


class ManufacturerOrderInvoiceForm(forms.Form):
    """The Manufacturer's invoice: their reference and document, shipping
    and tax, plus one quantity/price/expiry row per line item — what
    actually arrived. Our own invoice number is generated, not typed.
    The stock lands unallocated; the Owner splits each batch across
    regions and warehouses on the Inventory page afterwards."""

    ITEM_PRICE_PREFIX = "item_price_"
    ITEM_QTY_PREFIX = "item_qty_"
    ITEM_EXPIRY_PREFIX = "item_expiry_"

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
        label="Shipping charged (Rs)",
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
                # 50, not 50.0000 — bottles are counted whole.
                initial=(
                    item.quantity.to_integral_value()
                    if item.quantity == item.quantity.to_integral_value()
                    else item.quantity
                ),
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
                initial=getattr(getattr(item, "batch", None), "expiry_date", None),
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
        label="Amount you're paying now (Rs)",
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
