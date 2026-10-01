from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from apps.core.models import Brand

from .models import (
    BottleSize,
    Ingredient,
    Product,
    ProductCategory,
    ProductIngredient,
)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = [
            "name",
            "parent",
            "sort_order",
            "active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["parent"].queryset = (
            ProductCategory.objects
            .exclude(pk=self.instance.pk)
        )


class IngredientForm(forms.ModelForm):
    class Meta:
        model = Ingredient
        fields = [
            "name",
            "botanical_name",
            "part_used",
            "default_unit",
            "active",
        ]


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "brand",
            "sku",
            "barcode",
            "handle",
            "name",
            "generic_name",
            "brand_name",
            "indications",
            "body_html",
            "tags",
            "enlistment_number",
            "form",
            "pack_size",
            "unit_of_measure",
            "units_per_case",
            "serving_size",
            "strength_basis",
            "base_retail_price",
            "compare_at_price",
            "weight_grams",
            "image",
            "currency",
            "shelf_life_months",
            "hs_code",
            "default_reorder_point",
            "default_reorder_quantity",
            "allow_fractional_quantity",
            "active",
        ]
        widgets = {
            "indications": forms.Textarea(
                attrs={"rows": 3}
            ),
            "body_html": forms.Textarea(
                attrs={"rows": 4}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["brand"].queryset = (
            Brand.objects
            .filter(active=True)
        )
        self.fields["handle"].required = False


class ProductIngredientForm(forms.ModelForm):
    class Meta:
        model = ProductIngredient
        fields = [
            "ingredient",
            "strength",
            "unit",
            "is_medicinal",
            "extract_ratio",
            "equivalent_to",
            "sort_order",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["ingredient"].queryset = (
            Ingredient.objects
            .filter(active=True)
        )


ProductIngredientFormSet = inlineformset_factory(
    Product,
    ProductIngredient,
    form=ProductIngredientForm,
    extra=1,
    can_delete=True,
)


class ProductBottlePricesForm(forms.Form):
    """Our saved price per bottle size — what a Request to Quote line is
    pre-filled with. Leave a size blank to have no saved price."""

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)

        saved = {}
        if product is not None:
            saved = {row.bottle_size: row.price for row in product.bottle_prices.all()}

        for size, label in BottleSize.choices:
            self.fields[f"price_{size}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=3,
                min_value=Decimal("0"),
                required=False,
                label=label,
                initial=saved.get(size),
                widget=forms.NumberInput(attrs={"step": "0.001", "placeholder": "Not set"}),
            )

    def prices(self):
        return {
            size: self.cleaned_data.get(f"price_{size}")
            for size in BottleSize.values
        }
