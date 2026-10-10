import csv
import io
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event

from .models import (
    BottleSize,
    Ingredient,
    Product,
    ProductBottlePrice,
    ProductCategory,
    ProductImage,
    ProductIngredient,
    ProductPackagePrice,
    ProductRetailPrice,
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


@transaction.atomic
def save_bottle_price(*, actor, product, bottle_size, price):
    """Creates or replaces our saved price for one bottle of a product
    at one bottle size. price=None removes the saved price."""
    require_owner(actor)

    if bottle_size not in BottleSize.values:
        raise ValidationError("Not a valid bottle size.")

    saved = ProductBottlePrice.objects.filter(
        product=product, bottle_size=bottle_size
    ).first()
    before = None if saved is None else str(saved.price)

    if price in (None, ""):
        if saved is not None:
            saved.delete()
            record_audit_event(
                user=actor,
                action="products.bottle_price_removed",
                instance=product,
                before_data={"bottle_size": bottle_size, "price": before},
            )
        return None

    try:
        price = Decimal(str(price))
    except (InvalidOperation, TypeError):
        raise ValidationError("Price must be a number.")

    if price < 0:
        raise ValidationError("Price cannot be negative.")

    if saved is not None and saved.price == price:
        return saved

    if saved is None:
        saved = ProductBottlePrice(
            product=product,
            bottle_size=bottle_size,
            created_by=actor,
        )

    saved.price = price
    saved.updated_by = actor
    saved.full_clean()
    saved.save()

    record_audit_event(
        user=actor,
        action="products.bottle_price_saved",
        instance=saved,
        before_data={"price": before},
        after_data={
            "product_id": str(product.pk),
            "bottle_size": bottle_size,
            "price": str(price),
        },
    )

    return saved


@transaction.atomic
def set_product_bottle_prices(*, actor, product, prices):
    """prices: {bottle_size: Decimal | None} — None clears that size."""
    require_owner(actor)

    for bottle_size, price in prices.items():
        save_bottle_price(
            actor=actor,
            product=product,
            bottle_size=int(bottle_size),
            price=price,
        )


def bottle_price_map(products=None):
    """{product_id_str: {bottle_size_str: "price"}} — handed to the
    Request to Quote form so picking a product and bottle size
    pre-fills the price."""
    queryset = ProductBottlePrice.objects.all()

    if products is not None:
        queryset = queryset.filter(product__in=products)

    prices = {}
    for row in queryset.values("product_id", "bottle_size", "price"):
        prices.setdefault(str(row["product_id"]), {})[str(row["bottle_size"])] = str(
            row["price"]
        )

    return prices


def product_price_map():
    """{product_id_str: "price"} — each product's own price, offered on
    the Request to Quote as the system price when no price is saved for
    the chosen bottle size. Products without a price are left out."""
    return {
        str(pk): str(price)
        for pk, price in Product.objects.filter(base_retail_price__gt=0)
        .values_list("pk", "base_retail_price")
    }


@transaction.atomic
def save_retail_price(*, actor, product, bottle_size, price):
    """Creates or replaces what we sell one bottle of a product for at
    one bottle size. price=None removes the retail price."""
    require_owner(actor)

    if bottle_size not in BottleSize.values:
        raise ValidationError("Not a valid bottle size.")

    saved = ProductRetailPrice.objects.filter(
        product=product, bottle_size=bottle_size
    ).first()
    before = None if saved is None else str(saved.price)

    if price in (None, ""):
        if saved is not None:
            saved.delete()
            record_audit_event(
                user=actor,
                action="products.retail_price_removed",
                instance=product,
                before_data={"bottle_size": bottle_size, "price": before},
            )
        return None

    try:
        price = Decimal(str(price))
    except (InvalidOperation, TypeError):
        raise ValidationError("Price must be a number.")

    if price < 0:
        raise ValidationError("Price cannot be negative.")

    if saved is not None and saved.price == price:
        return saved

    if saved is None:
        saved = ProductRetailPrice(
            product=product,
            bottle_size=bottle_size,
            created_by=actor,
        )

    saved.price = price
    saved.updated_by = actor
    saved.full_clean()
    saved.save()

    record_audit_event(
        user=actor,
        action="products.retail_price_saved",
        instance=saved,
        before_data={"price": before},
        after_data={
            "product_id": str(product.pk),
            "bottle_size": bottle_size,
            "price": str(price),
        },
    )

    return saved


@transaction.atomic
def set_product_retail_prices(*, actor, product, prices):
    """prices: {bottle_size: Decimal | None} — None clears that size."""
    require_owner(actor)

    for bottle_size, price in prices.items():
        save_retail_price(
            actor=actor,
            product=product,
            bottle_size=int(bottle_size),
            price=price,
        )


RETAIL_PRICE_SHEET_HEADER = ["SKU", "Product"] + [
    f"{size} capsules" for size in BottleSize.values
]


def export_retail_price_sheet(queryset):
    """One row per product, one price column per bottle size — open it
    in Excel, edit the prices, then load it back with
    `manage.py import_retail_prices`."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(RETAIL_PRICE_SHEET_HEADER)

    for product in queryset.prefetch_related("retail_prices"):
        prices = {row.bottle_size: row.price for row in product.retail_prices.all()}
        writer.writerow(
            [product.sku, product.name]
            + [prices.get(size, "") for size in BottleSize.values]
        )

    return buffer.getvalue()


@transaction.atomic
def import_retail_price_sheet(*, actor, file):
    """Reads a sheet in the export_retail_price_sheet layout. A blank
    cell clears that size's price. Returns the number of products
    updated; raises ValidationError naming the bad row otherwise."""
    require_owner(actor)

    reader = csv.DictReader(file)
    missing = set(RETAIL_PRICE_SHEET_HEADER) - set(reader.fieldnames or [])
    missing.discard("Product")
    if missing:
        raise ValidationError(
            f"Price sheet is missing column(s): {', '.join(sorted(missing))}."
        )

    updated = 0
    for line, row in enumerate(reader, start=2):
        sku = (row.get("SKU") or "").strip().upper()
        if not sku:
            continue

        product = Product.objects.filter(sku=sku).first()
        if product is None:
            raise ValidationError(f"Row {line}: no product with SKU {sku}.")

        prices = {}
        for size in BottleSize.values:
            cell = (row.get(f"{size} capsules") or "").strip().replace(",", "")
            try:
                prices[size] = Decimal(cell) if cell else None
            except InvalidOperation:
                raise ValidationError(
                    f"Row {line}: {size} capsules price '{cell}' is not a number."
                )

        set_product_retail_prices(actor=actor, product=product, prices=prices)
        updated += 1

    return updated


@transaction.atomic
def save_package_price(*, actor, product, manufacturer, amount, price, package=None):
    """Creates (package=None) or edits one manufacturer's package of a
    product: how much is in it and what that manufacturer charges."""
    require_owner(actor)

    before = None
    if package is None:
        package = ProductPackagePrice(product=product, created_by=actor)
    else:
        before = {
            "manufacturer_id": str(package.manufacturer_id),
            "amount": str(package.amount),
            "price": str(package.price),
        }

    package.manufacturer = manufacturer
    package.amount = amount
    package.price = price
    package.updated_by = actor
    package.full_clean()
    package.save()

    record_audit_event(
        user=actor,
        action="products.package_price_saved",
        instance=package,
        before_data=before,
        after_data={
            "product_id": str(product.pk),
            "manufacturer_id": str(manufacturer.pk),
            "amount": str(package.amount),
            "price": str(package.price),
        },
    )

    return package


@transaction.atomic
def delete_package_price(*, actor, package):
    require_owner(actor)

    record_audit_event(
        user=actor,
        action="products.package_price_deleted",
        instance=package.product,
        before_data={
            "manufacturer_id": str(package.manufacturer_id),
            "amount": str(package.amount),
            "price": str(package.price),
        },
    )
    package.delete()


@transaction.atomic
def update_product_photos(*, actor, product, add=(), remove=()):
    """add: uploaded image files; remove: ProductImage rows to delete."""
    require_owner(actor)

    next_order = product.gallery.count()

    for photo in remove:
        photo.image.delete(save=False)
        photo.delete()

    for index, upload in enumerate(add):
        photo = ProductImage(
            product=product,
            image=upload,
            sort_order=next_order + index,
            created_by=actor,
            updated_by=actor,
        )
        photo.full_clean()
        photo.save()

    if add or remove:
        record_audit_event(
            user=actor,
            action="products.photos_updated",
            instance=product,
            after_data={"added": len(add), "removed": len(remove)},
        )
