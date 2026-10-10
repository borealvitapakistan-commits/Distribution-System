from decimal import Decimal, InvalidOperation
from django import forms
from django.forms import inlineformset_factory
from apps.core.forms import PaidQuestionForm
from apps.owner_inventory.services import shippable_batches_fefo
from apps.products.models import Product
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderPayment


class ProductPriceSelect(forms.Select):
    """A <select> whose <option>s carry data-price (list price) and
    data-discount (this Distributor's agreement discount for it), so the
    create-PO page can show live prices in JS without a round trip."""

    agreement = None

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        product = getattr(value, "instance", None)

        if product is not None:
            discount = self.agreement.discount_for(product) if self.agreement else Decimal("0")
            option["attrs"]["data-price"] = str(product.base_retail_price)
            option["attrs"]["data-discount"] = str(discount)

        return option


class PurchaseOrderItemForm(forms.ModelForm):
    """One line of a Request to Quote: product, quantity and the price the
    Distributor asks for — pre-filled from their agreement as they pick
    the product, and optional (blank = "please quote")."""

    class Meta:
        model = PurchaseOrderItem
        fields = ["product", "quantity_requested", "requested_unit_price"]
        widgets = {
            "product": ProductPriceSelect,
            "requested_unit_price": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "placeholder": "Optional", "class": "doc-line-input"}
            ),
            "quantity_requested": forms.NumberInput(
                attrs={"step": "1", "min": "1", "placeholder": "0", "class": "doc-line-input"}
            ),
        }
        labels = {
            "quantity_requested": "Quantity",
            "requested_unit_price": "Your price",
        }

    def __init__(self, *args, agreement=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["product"].queryset = Product.objects.filter(active=True)
        self.fields["product"].widget.agreement = agreement
        self.fields["product"].empty_label = "Select product…"
        self.fields["requested_unit_price"].required = False

    def validate_unique(self):
        # The order isn't saved through this form; the service checks a
        # product appears only once across the whole order.
        pass


PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderItem,
    form=PurchaseOrderItemForm,
    extra=1,
    can_delete=True,
)


class RequestToQuoteForm(forms.Form):
    message = forms.CharField(
        required=False,
        label="Message to the Owner",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Kept in the order's conversation — the Owner sees it with your request.",
    )


def _price_field(initial, label):
    return forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        initial=initial,
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "doc-line-input"}),
        label=label,
    )


def _quantity_field(initial, label):
    return forms.DecimalField(
        max_digits=18,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        initial=None if initial is None else f"{initial.normalize():f}",
        widget=forms.NumberInput(attrs={"step": "1", "class": "doc-line-input"}),
        label=label,
    )


class OrderLinesForm(forms.Form):
    """A price and quantity per order line plus a message — shared by the
    Owner's quote, the Distributor's counter-offer and the Owner changing
    a placed Purchase Order."""

    PRICE_PREFIX = "line_price_"
    QTY_PREFIX = "line_qty_"
    REMOVE_PREFIX = "line_remove_"

    allow_remove = False
    allow_attachment = False
    price_label = "Price"
    price_required_error = "Enter a price."

    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    attachment = forms.FileField(
        required=False,
        label="Attachment",
        help_text="Optional — e.g. a price list or a note.",
    )

    def __init__(self, *args, purchase_order=None, initial_lines=None, **kwargs):
        """initial_lines: {item_pk_as_str: PurchaseOrderRevisionLine} to
        start from instead of the order's own prices — e.g. the
        Distributor's counter-offer, so a quote that accepts it needs no
        retyping."""
        super().__init__(*args, **kwargs)

        if not self.allow_attachment:
            del self.fields["attachment"]

        self.items = (
            list(purchase_order.items.select_related("product")) if purchase_order else []
        )
        initial_lines = initial_lines or {}

        for item in self.items:
            start = initial_lines.get(str(item.pk))
            price = start.unit_price if start else item.unit_price
            quantity = start.quantity if start else item.quantity_requested
            self.fields[f"{self.PRICE_PREFIX}{item.pk}"] = _price_field(
                price, f"{item.product.name} — {self.price_label.lower()}"
            )
            self.fields[f"{self.QTY_PREFIX}{item.pk}"] = _quantity_field(
                quantity, f"{item.product.name} — quantity"
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
                self.add_error(f"{self.PRICE_PREFIX}{item.pk}", self.price_required_error)

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


class OwnerQuoteForm(OrderLinesForm):
    """The Owner's answer to a Request to Quote, from the Owner's panel."""

    allow_remove = True
    allow_attachment = True
    price_label = "Quoted price"
    price_required_error = "Enter the price you're quoting."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].label = "Message to the Distributor"
        self.fields["message"].help_text = (
            "Sent with your quote — the Distributor sees it on their order."
        )


class CounterOfferForm(OrderLinesForm):
    """The Distributor answering the Owner's quote with their own prices."""

    allow_attachment = True
    price_label = "Your price"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].label = "Message to the Owner"
        self.fields["message"].help_text = "Why you're asking for these prices."


class PurchaseOrderChangeForm(OrderLinesForm):
    """The Owner changing a placed Purchase Order — saved as a new version."""

    allow_remove = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].label = "Reason for the change"
        self.fields["message"].help_text = (
            "Saved with this version of the Purchase Order — the Distributor sees it."
        )


class IssueInvoiceForm(forms.Form):
    tax_percentage = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        label="Tax %",
    )
    shipping_amount = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
        label="Shipping",
    )
    message = forms.CharField(
        required=False,
        label="Note on the invoice",
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Optional — shown to the Distributor with the invoice.",
    )


class ShipPurchaseOrderItemForm(forms.Form):
    """One quantity field per available batch (warehouse) for this
    product — the Owner can split a single shipment across as many of
    them as they want in one submission, not just pick one."""

    BATCH_FIELD_PREFIX = "batch_qty_"

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.batches = list(shippable_batches_fefo(product=product)) if product else []

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


class LineStatusForm(forms.Form):
    note = forms.CharField(
        required=False,
        label="Comment to Distributor",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    unavailable = forms.BooleanField(
        required=False,
        label="Not available — close this product on the order",
    )


class PricingForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["tax_percentage", "shipping_amount"]


class DistributorAdvancePaymentForm(PaidQuestionForm):
    QUESTION = "Are you paying in advance?"


class PurchaseOrderReceiveForm(PaidQuestionForm):
    """The Distributor's Order Received page: how much arrived on each
    line still waiting to be received, plus "Have you paid the remaining
    amount?" — which only needs an answer when something is still owed,
    so the view decides that."""

    QUESTION = "Have you paid the remaining amount?"
    QTY_PREFIX = "qty_"

    def __init__(self, *args, purchase_order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["answer"].required = False
        self.fields["answer"].empty_value = None

        self.items = [
            item
            for item in (purchase_order.items.select_related("product") if purchase_order else [])
            if item.quantity_to_receive_remaining > 0
        ]

        for item in self.items:
            self.fields[f"{self.QTY_PREFIX}{item.pk}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=4,
                min_value=Decimal("0"),
                max_value=item.quantity_to_receive_remaining,
                required=False,
                initial=item.quantity_to_receive_remaining,
                label=f"{item.product.name} — quantity received",
            )

    def item_rows(self):
        return [(item, self[f"{self.QTY_PREFIX}{item.pk}"]) for item in self.items]

    def quantities(self):
        """{item_pk_as_str: Decimal} for every line with something received."""
        return {
            str(item.pk): self.cleaned_data[f"{self.QTY_PREFIX}{item.pk}"]
            for item in self.items
            if self.cleaned_data.get(f"{self.QTY_PREFIX}{item.pk}")
        }


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
        labels = {
            "paid_at": "Date paid",
            "proof": "Payment proof",
        }


class RejectPaymentForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
