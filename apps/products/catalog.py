"""The product categories Boreal Vita sells, and how each one is shown.

Matched to ProductCategory rows by name (case-insensitive), so a
category added later through the Categories page still works — it just
gets the neutral look and no default unit.
"""

from .models import Product

Unit = Product.UnitOfMeasure

# name, default unit of measure, colour tone, icon
CATEGORIES = [
    ("Capsule", Unit.PIECE, "blue", "capsule"),
    ("Tablet", Unit.PIECE, "green", "tablet"),
    ("Soft Gel", Unit.PIECE, "amber", "softgel"),
    ("Liquid", Unit.ML, "red", "bottle"),
    ("Syrup", Unit.ML, "purple", "bottle"),
    ("Oil", Unit.ML, "orange", "drop"),
    ("Essential Oil", Unit.ML, "violet", "dropper"),
    ("Carrier Oil", Unit.ML, "teal", "bottle"),
    ("Powder", Unit.GRAM, "rose", "powder"),
    ("Herbal Tea", Unit.GRAM, "lime", "leaf"),
]

# The only units a product can be measured in.
PRODUCT_UNITS = [Unit.PIECE, Unit.ML, Unit.GRAM]

_BY_NAME = {name.lower(): (unit, tone, icon) for name, unit, tone, icon in CATEGORIES}


def category_style(category):
    """(tone, icon) for a ProductCategory, or the neutral look."""
    if category is None:
        return "slate", "box"

    _unit, tone, icon = _BY_NAME.get(category.name.lower(), (None, "slate", "box"))
    return tone, icon


def default_unit(category):
    if category is None:
        return None

    return _BY_NAME.get(category.name.lower(), (None,))[0]


def unit_label(product):
    """What the Unit column says: the dosage form for counted products
    (capsule, tablet, soft gel), otherwise ml or gram."""
    if product.unit_of_measure == Unit.PIECE:
        if product.category and default_unit(product.category) == Unit.PIECE:
            return product.category.name.lower()
        return "unit"

    if product.unit_of_measure == Unit.ML:
        return "ml"

    if product.unit_of_measure == Unit.GRAM:
        return "gram"

    return product.get_unit_of_measure_display().lower()


def size_label(product):
    """e.g. "60 units", "100 ml", "300 g" — blank when no size is set."""
    if product.size is None:
        return ""

    amount = f"{product.size.normalize():f}"

    suffix = {
        Unit.PIECE: "units",
        Unit.ML: "ml",
        Unit.GRAM: "g",
    }.get(product.unit_of_measure, product.get_unit_of_measure_display().lower())

    return f"{amount} {suffix}"


def unit_plural(product):
    """The unit as it reads after an amount: "capsules", "ml", "g"."""
    if product.unit_of_measure == Unit.PIECE:
        label = unit_label(product)
        return "units" if label == "unit" else f"{label}s"

    if product.unit_of_measure == Unit.GRAM:
        return "g"

    return unit_label(product)
