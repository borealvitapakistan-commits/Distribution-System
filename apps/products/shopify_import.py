"""Turns Shopify products (as returned by shopify.fetch_products) into
this app's Products — matched to existing ones where possible, created
otherwise. Shopify is only ever read, never written to."""

import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify

from .catalog import default_unit
from .models import BottleSize, Product, ProductCategory
from .services import create_product, set_product_retail_prices, update_product
from .shopify import ShopifyError, download_image

SIZE_PATTERN = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:vegetarian\s+|veggie\s+|vegan\s+|veg\s+)?"
    r"(capsules?|caps|soft\s?gels?|tablets?|tabs|lozenges?|ml|grams?|g)\b",
    re.IGNORECASE,
)

# Checked in order: dosage forms before the looser words ("Tea Tree
# Essential Oil" is an essential oil; "Green Tea Extract … Capsules" a capsule).
CATEGORY_WORDS = [
    (r"essential oils?", "Essential Oil"),
    (r"carrier oils?", "Carrier Oil"),
    (r"soft\s?gels?", "Soft Gel"),
    (r"capsules?|caps", "Capsule"),
    (r"tablets?|tabs", "Tablet"),
    (r"syrups?", "Syrup"),
    (r"powders?", "Powder"),
    (r"herbal teas?|teas?", "Herbal Tea"),
    (r"liquids?|drops", "Liquid"),
    (r"oils?", "Oil"),
]


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _name_keys(title):
    """Ways a Shopify title might match one of our names:
    "Allergy Relief Capsule | 120 Vegetarian Capsules" ->
    {"allergy relief capsule | 120 …", "allergy relief capsule", "allergy relief"}."""
    full = " ".join(title.lower().split())
    base = full.split("|")[0].strip()
    bare = base
    for pattern, _name in CATEGORY_WORDS:
        bare = re.sub(rf"\b(?:{pattern})\b", " ", bare)
    bare = " ".join(re.sub(r"\b(?:vegetarian|veggie|vegan)\b", " ", bare).split())
    return [key for key in (full, base, bare) if key]


def guess_category(shopify_product, categories):
    for text in (shopify_product["product_type"], shopify_product["title"]):
        for pattern, name in CATEGORY_WORDS:
            if re.search(rf"\b(?:{pattern})\b", text, re.IGNORECASE):
                category = categories.get(name.lower())
                if category is not None:
                    return category
    return None


def guess_size(title):
    matches = SIZE_PATTERN.findall(title)
    return _decimal(matches[-1][0]) if matches else None


def _bottle_prices(shopify_product):
    """{30: price, 60: price, ...} for variants named after a bottle size."""
    prices = {}

    for variant in shopify_product["variants"]:
        match = SIZE_PATTERN.search(variant["title"]) or re.fullmatch(
            r"\s*(\d+)\s*", variant["title"]
        )
        if not match:
            continue

        size = int(Decimal(match.group(1)))
        if size in BottleSize.values:
            prices[size] = _decimal(variant["price"])

    return prices


def build_matcher(products):
    by_shopify_id, by_sku, by_handle, by_name = {}, {}, {}, {}

    for product in products:
        if product.shopify_product_id:
            by_shopify_id[product.shopify_product_id] = product
        by_sku[product.sku.upper()] = product
        by_handle[product.handle] = product
        by_name.setdefault(" ".join(product.name.lower().split()), []).append(product)

    def match(shopify_product):
        """(product or None, how it matched)."""
        if shopify_product["id"] in by_shopify_id:
            return by_shopify_id[shopify_product["id"]], "imported before"

        for variant in shopify_product["variants"]:
            if variant["sku"] and variant["sku"].upper() in by_sku:
                return by_sku[variant["sku"].upper()], f"SKU {variant['sku'].upper()}"

        if shopify_product["handle"] in by_handle:
            return by_handle[shopify_product["handle"]], "same handle"

        for key in _name_keys(shopify_product["title"]):
            if len(by_name.get(key, [])) == 1:
                return by_name[key][0], "same name"

        return None, ""

    return match


def preview(shopify_products, products):
    match = build_matcher(products)
    rows = []

    for shopify_product in shopify_products:
        product, reason = match(shopify_product)
        image = shopify_product["image_url"]
        rows.append(
            {
                "shopify": shopify_product,
                "thumb": f"{image}{'&' if '?' in image else '?'}width=120" if image else "",
                "match": product,
                "reason": reason,
                "skus": ", ".join(v["sku"] for v in shopify_product["variants"] if v["sku"]),
                "price": shopify_product["variants"][0]["price"] if shopify_product["variants"] else "",
            }
        )

    return rows


def _unique(value, field, exclude):
    taken = Product.objects.filter(**{field: value})
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    return not taken.exists()


@transaction.atomic
def import_product(*, actor, shopify_product, target=None, fetch_image=download_image):
    """Create (target=None) or update `target` from one Shopify product.
    Returns the saved Product."""
    categories = {
        category.name.lower(): category for category in ProductCategory.objects.all()
    }
    variants = shopify_product["variants"]
    first = variants[0] if variants else {}

    data = {
        "body_html": shopify_product["description_html"],
        "tags": ", ".join(shopify_product["tags"])[:255],
        "brand_name": shopify_product["vendor"][:200],
        "shopify_product_id": shopify_product["id"],
    }

    price = _decimal(first.get("price"))
    if price is not None:
        data["base_retail_price"] = price
    data["compare_at_price"] = _decimal(first.get("compare_at_price"))

    barcode = next((v["barcode"] for v in variants if v["barcode"]), "")
    if barcode and _unique(barcode, "barcode", target):
        data["barcode"] = barcode

    if _unique(shopify_product["handle"], "handle", target):
        data["handle"] = shopify_product["handle"]

    category = guess_category(shopify_product, categories)
    if category is not None and (target is None or target.category_id is None):
        data["category"] = category
        data["unit_of_measure"] = default_unit(category) or Product.UnitOfMeasure.PIECE

    size = guess_size(shopify_product["title"])
    if size is not None and (target is None or target.size is None):
        data["size"] = size

    image_url = shopify_product["image_url"]
    if image_url and (
        target is None or not target.image or target.shopify_image_url != image_url
    ):
        try:
            filename, content = fetch_image(image_url)
        except ShopifyError:
            pass
        else:
            data["image"] = ContentFile(content, name=filename)
            data["shopify_image_url"] = image_url

    if target is None:
        sku = next((v["sku"].upper() for v in variants if v["sku"]), "")
        if not sku or not _unique(sku, "sku", None):
            sku = f"SHOPIFY-{shopify_product['id']}"

        if "handle" not in data:
            data["handle"] = slugify(f"{shopify_product['title']}-{sku}")[:200]

        product = create_product(
            actor=actor,
            sku=sku,
            name=shopify_product["title"][:200],
            active=shopify_product["active"],
            currency="PKR",
            **data,
        )
    else:
        product = update_product(actor=actor, product_id=target.pk, **data)

    bottle_prices = _bottle_prices(shopify_product)
    if bottle_prices:
        set_product_retail_prices(actor=actor, product=product, prices=bottle_prices)

    return product


def import_selected(*, actor, shopify_products, choices, fetch_image=download_image):
    """choices: {shopify id: "new" | product pk}. Each product imports on
    its own, so one bad product doesn't stop the rest.
    Returns (created, updated, [(title, error), ...])."""
    created = updated = 0
    errors = []

    for shopify_product in shopify_products:
        choice = choices.get(shopify_product["id"])
        if not choice:
            continue

        target = None
        if choice != "new":
            target = Product.objects.filter(pk=choice).first()
            if target is None:
                errors.append((shopify_product["title"], "The chosen product no longer exists."))
                continue

        try:
            import_product(
                actor=actor,
                shopify_product=shopify_product,
                target=target,
                fetch_image=fetch_image,
            )
        except ValidationError as exc:
            errors.append((shopify_product["title"], " ".join(exc.messages)))
            continue

        if target is None:
            created += 1
        else:
            updated += 1

    return created, updated, errors
