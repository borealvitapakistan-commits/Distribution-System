import json
from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from django.db.models import Q

from apps.core.models import Brand
from apps.manufacturers.models import Manufacturer

from .catalog import PRODUCT_UNITS, default_unit
from .description import sanitize_description
from .models import (
    BottleSize,
    Ingredient,
    Product,
    ProductCategory,
    ProductImage,
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


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            return []
        uploads = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultipleImageField, self).clean(upload, initial) for upload in uploads]


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "brand",
            "sku",
            "barcode",
            "handle",
            "name",
            "category",
            "body_html",
            "tags",
            "enlistment_number",
            "unit_of_measure",
            "size",
            "base_retail_price",
            "compare_at_price",
            "weight_grams",
            "image",
            "currency",
            "shelf_life_months",
            "hs_code",
            "default_reorder_point",
            "default_reorder_quantity",
            "active",
            "key_benefits",
            "other_ingredients",
            "allergen_info",
            "label_product_name",
            "label_dose",
            "label_cautions",
            "label_other",
            "label_file",
            "bottle_image",
        ]
        labels = {
            "body_html": "Description",
            "name": "Title",
            "size": "Size",
            "unit_of_measure": "Measured in",
            "base_retail_price": "Price",
            "weight_grams": "Weight (g)",
        }
        widgets = {
            "active": forms.Select(choices=[("True", "Active"), ("False", "On hold")]),
            "body_html": forms.Textarea(
                attrs={"rows": 4}
            ),
            "key_benefits": forms.Textarea(attrs={"rows": 4}),
            "other_ingredients": forms.Textarea(attrs={"rows": 2}),
            "allergen_info": forms.Textarea(attrs={"rows": 2}),
            "label_dose": forms.Textarea(attrs={"rows": 3}),
            "label_cautions": forms.Textarea(attrs={"rows": 3}),
            "label_other": forms.Textarea(attrs={"rows": 3}),
            "label_file": forms.FileInput(attrs={"accept": ".png,.jpg,.jpeg,.webp,.pdf,image/*,application/pdf"}),
            "bottle_image": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        }

    # Not Product fields — the views hand these to update_product_photos.
    PHOTO_FIELDS = ("gallery_photos", "remove_photos")

    manufacturers = forms.ModelMultipleChoiceField(
        label="Allocate to manufacturers",
        queryset=Manufacturer.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Tick every manufacturer that makes this product. Each one gets its own sizes and prices.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["brand"].queryset = (
            Brand.objects
            .filter(active=True)
        )
        self.fields["handle"].required = False
        allocated = (
            list(self.instance.allocations.values_list("manufacturer_id", flat=True))
            if self.instance.pk else []
        )
        self.fields["manufacturers"].queryset = Manufacturer.objects.filter(
            Q(active=True) | Q(pk__in=allocated), archived_at__isnull=True,
        ).order_by("name")
        if self.instance.pk and "manufacturers" not in self.initial:
            self.initial["manufacturers"] = allocated

        self.fields["gallery_photos"] = MultipleImageField(
            label="Add more photos",
            required=False,
            help_text="Shown in the product page gallery after the main image.",
        )
        self.fields["remove_photos"] = forms.ModelMultipleChoiceField(
            label="Remove photos",
            queryset=(
                self.instance.gallery.all()
                if self.instance.pk
                else ProductImage.objects.none()
            ),
            required=False,
            widget=forms.CheckboxSelectMultiple,
        )
        if not self.instance.pk or not self.instance.gallery.exists():
            del self.fields["remove_photos"]

        self.fields["category"].queryset = (
            ProductCategory.objects
            .filter(active=True)
        )

        units = list(PRODUCT_UNITS)
        if self.instance.unit_of_measure not in units:
            units.append(self.instance.unit_of_measure)
        plain = {
            Product.UnitOfMeasure.PIECE: "Capsules / tablets (count)",
            Product.UnitOfMeasure.GRAM: "Grams (powder)",
            Product.UnitOfMeasure.ML: "Millilitres (liquid)",
        }
        self.fields["unit_of_measure"].choices = [
            (unit, plain.get(unit, Product.UnitOfMeasure(unit).label)) for unit in units
        ]
        self.fields["category"].label = "Product type"
        self.fields["size"].widget.attrs.update({"step": "any", "min": "0", "data-size-input": ""})

        # Picking a category pre-selects its unit (see app.js).
        self.fields["category"].widget.attrs["data-unit-by-category"] = json.dumps(
            {
                str(category.pk): default_unit(category)
                for category in self.fields["category"].queryset
                if default_unit(category)
            }
        )

    def split_photo_data(self):
        """(product fields, photos to add, photos to remove)."""
        data = dict(self.cleaned_data)
        # Django's clear checkbox returns False; FileField expects an empty name.
        if data.get("image") is False:
            data["image"] = ""
            data["shopify_image_url"] = ""
        data.pop("manufacturers", None)
        # The label file and bottle image use their own "Remove" boxes.
        for name in ("label_file", "bottle_image"):
            if self.data.get(f"{name}-clear") and not self.files.get(name):
                data[name] = ""
        add = data.pop("gallery_photos", None) or []
        remove = data.pop("remove_photos", None) or []
        return data, add, remove

    def clean_body_html(self):
        return sanitize_description(self.cleaned_data.get("body_html", ""))


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


class ProductRetailPricesForm(forms.Form):
    """What we sell one bottle for at each size. Leave a size blank if
    the product isn't sold in that bottle."""

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)

        saved = {}
        if product is not None:
            saved = {row.bottle_size: row.price for row in product.retail_prices.all()}

        for size, label in BottleSize.choices:
            self.fields[f"retail_{size}"] = forms.DecimalField(
                max_digits=18,
                decimal_places=2,
                min_value=Decimal("0"),
                required=False,
                label=label,
                initial=saved.get(size),
                widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "Not sold"}),
            )

    def prices(self):
        return {
            size: self.cleaned_data.get(f"retail_{size}")
            for size in BottleSize.values
        }


class ProductPackagePriceForm(forms.Form):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.filter(active=True).order_by("name"),
    )
    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="How much is in the package, e.g. 60 (capsules) or 100 (ml).",
    )
    price = forms.DecimalField(
        label="Price (PKR)",
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0"),
    )
