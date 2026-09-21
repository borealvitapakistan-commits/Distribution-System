import csv
import io

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import (
    Ingredient,
    Product,
    ProductCategory,
    ProductIngredient,
)


SHOPIFY_CSV_HEADER = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Variant SKU",
    "Variant Barcode",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Variant Inventory Qty",
    "Image Src",
    "Gift Card",
    "Status",
]



def clean_text(text):
    return text.strip() if isinstance(text, str) else text


def product_data(product):
    return {
        "sku": product.sku,
        "name": product.name,
        "active": product.active,
        "category_id":(
            str(product.category_id)
            if product.category_id
            else None
        ),
        "base_retail_price": str(product.base_retail_price),
        "currency": product.currency
    }



def category_data(category):
    return {
        "name": category.name,
        "parent_id":(
            str(category.parent_id)
            if category.parent_id
            else None
        ),
        "active": category.active
    }



def ingredient_data(ingredient):
    return {
        "name": ingredient.name,
        "botanical_name": ingredient.botanical_name,
        "active": ingredient.active
    }


@transaction.atomic
def create_category(*, actor, **data):
    require_owner(actor)

    category = ProductCategory(
        created_by=actor,
        updated_by=actor,
        **data,
    )

    category.full_clean()
    category.save()

    record_audit_event(
        user=actor,
        action="products.category_created",
        instance=category,
        after_data=category_data(category),
    )

    return category


@transaction.atomic
def create_ingredient(*, actor, **data):
    """Create an ingredient through the service layer."""
    require_owner(actor)

    ingredient = Ingredient(
        created_by=actor,
        updated_by=actor,
        **data,
    )
    ingredient.full_clean()
    ingredient.save()

    record_audit_event(
        user=actor,
        action="products.ingredient_created",
        instance=ingredient,
        after_data=ingredient_data(ingredient),
    )

    return ingredient



@transaction.atomic
def update_category(*, actor, category_id, **data):
    require_owner(actor)

    category = (
        ProductCategory.objects
        .select_for_update()
        .filter(pk=category_id)
        .first()
    )

    if category is None:
        raise ValidationError(
            "Category was not found."
        )

    before = category_data(category)

    for field, value in data.items():
        setattr(category, field, value)

    category.updated_by = actor
    category.full_clean()
    category.save()

    record_audit_event(
        user=actor,
        action="products.category_updated",
        instance=category,
        before_data=before,
        after_data=category_data(category),
    )

    return category



def validate_ingredient_rows(actor, product, ingredients_rows):
    links = []
    seen = set()

    for index, row in enumerate(ingredients_rows or []):
        ingredient = row.get("ingredient")

        if not ingredient:
            raise ValidationError(
                "Every ingredient row needs an ingredient."
            )

        if ingredient.pk in seen:
            raise ValidationError(
                "An ingredient can appear only once "
                "on a product."
            )

        seen.add(ingredient.pk)

        links.append(
            ProductIngredient(
                product=product,
                ingredient=ingredient,
                strength=clean_text(
                    row.get("strength", "")
                ),
                unit=clean_text(
                    row.get("unit", "")
                ),
                is_medicinal=bool(
                    row.get("is_medicinal", False)
                ),
                extract_ratio=clean_text(
                    row.get("extract_ratio", "")
                ),
                equivalent_to=clean_text(
                    row.get("equivalent_to", "")
                ),
                sort_order=row.get(
                    "sort_order",
                    index,
                ),
                created_by=actor,
                updated_by=actor,
            )
        )

    for link in links:
        link.full_clean()

    return links



@transaction.atomic
def create_product(*, actor, ingredients=None, **data):
    require_owner(actor)

    product = Product(
        created_by=actor,
        updated_by=actor,
        **data
    )

    product.full_clean()
    product.save()


    links = validate_ingredient_rows(
        actor,
        product,
        ingredients
    )

    ProductIngredient.objects.bulk_create(links)

    record_audit_event(
        user=actor,
        action="products.product_created",
        instance=product,
        after_data=product_data(product),
    )

    return product




def _has_posted_movements(product):
    return product.stock_movements.exists()


@transaction.atomic
def update_product(
    *,
    actor,
    product_id,
    ingredients=None,
    **data,
):
    require_owner(actor)

    product = (
        Product.objects
        .select_for_update()
        .filter(pk=product_id)
        .first()
    )

    if product is None:
        raise ValidationError(
            "Product was not found."
        )

    before = product_data(product)

    new_sku = data.get("sku", product.sku)

    if (
        _has_posted_movements(product)
        and new_sku.strip().upper() != product.sku
    ):
        raise ValidationError(
            "SKU cannot be changed after posted "
            "stock movements exist."
        )

    for field, value in data.items():
        setattr(product, field, value)

    product.updated_by = actor
    product.full_clean()
    product.save()

    if ingredients is not None:
        links = validate_ingredient_rows(
            actor,
            product,
            ingredients,
        )

        ProductIngredient.objects.filter(
            product=product
        ).delete()

        ProductIngredient.objects.bulk_create(links)

    record_audit_event(
        user=actor,
        action="products.product_updated",
        instance=product,
        before_data=before,
        after_data=product_data(product),
    )

    return product



def export_products_csv(queryset):
    """Build a Shopify-import-compatible product CSV in memory."""
    from apps.owner_inventory.models import StockBalance
    from apps.owner_warehouse.models import Location

    shopify_balances = dict(
        StockBalance.objects
        .filter(location__location_type=Location.LocationType.SHOPIFY)
        .values_list("product_id", "quantity")
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(SHOPIFY_CSV_HEADER)

    for product in queryset.select_related("category", "manufacturer"):
        writer.writerow([
            product.handle,
            product.name,
            product.body_html,
            product.manufacturer.name if product.manufacturer_id else "",
            product.category.name if product.category_id else "",
            product.tags,
            "TRUE" if product.active else "FALSE",
            "Title",
            "Default Title",
            product.sku,
            product.barcode,
            product.weight_grams,
            "shopify",
            "deny",
            "manual",
            product.base_retail_price,
            product.compare_at_price if product.compare_at_price is not None else "",
            "TRUE",
            "TRUE",
            shopify_balances.get(product.pk, 0),
            product.image.url if product.image else "",
            "FALSE",
            "active" if product.active else "draft",
        ])

    return buffer.getvalue()
