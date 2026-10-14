from django import forms

from apps.products.models import Product

from .models import Expense, MarketResearch


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["date", "name", "category", "amount", "comments"]
        labels = {
            "name": "Expense name",
            "amount": "Amount (Rs)",
        }
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "comments": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].widget.attrs.update({"min": "0.01", "step": "0.01"})


class MarketResearchForm(forms.ModelForm):
    class Meta:
        model = MarketResearch
        fields = [
            "product",
            "similar_product",
            "store",
            "pack_size",
            "unit",
            "market_price",
            "use_for_market_price",
            "checked_on",
            "note",
            "link",
        ]
        labels = {
            "product": "Our product",
            "similar_product": "Similar product on market",
            "market_price": "Market price (Rs)",
            "use_for_market_price": "Use for market price",
        }
        widgets = {
            "checked_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True).order_by("name")
        self.fields["pack_size"].widget.attrs.update({"min": "0.01", "step": "0.01"})
        self.fields["market_price"].widget.attrs.update({"min": "0", "step": "0.01"})
