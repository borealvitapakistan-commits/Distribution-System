"""Which sidebar item a page belongs to, worked out from its URL name —
so a new page in a section lights up the right item without anyone
having to remember to list it in the template."""

# (section, URL-name prefixes or exact names), checked in order — the
# first match wins, so more specific entries come first.
OWNER_SECTIONS = [
    ("dashboard", ("owner-dashboard", "dashboard")),
    ("purchase-orders", (
        "owner-purchase-order", "purchase-order", "manufacturer-order",
    )),
    ("agreements", ("owner-agreement",)),
    ("partners", (
        "manufacturer-", "brand-", "owner-list", "owner-create",
        "distributor-list", "distributor-invite", "approve-distributor",
        "reject-distributor", "suspend-distributor", "deactivate-owner",
    )),
    ("products", ("product-", "category-", "ingredient-")),
    ("batches", ("stock-batch-list", "batch-trace")),
    ("inventory", (
        "inventory-", "location-", "stock-balance-list", "shopify-sync",
    )),
    ("finance", ("owner-finance",)),
]

DISTRIBUTOR_SECTIONS = [
    ("dashboard", ("distributor-dashboard",)),
    ("orders", (
        "distributor-orders-hub", "distributor-purchase-order", "purchase-order",
    )),
    ("agreements", ("distributor-agreement",)),
    ("inventory", ("distributor-region", "distributor-warehouse")),
    ("stock", ("distributor-stock",)),
    ("sales", ("distributor-sale",)),
    ("profile", ("distributor-profile",)),
]


def section_for(url_name, is_owner):
    if not url_name:
        return ""
    for section, prefixes in OWNER_SECTIONS if is_owner else DISTRIBUTOR_SECTIONS:
        if any(url_name == prefix or url_name.startswith(prefix) for prefix in prefixes):
            return section
    return ""


def nav_section(request):
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    if not (user and user.is_authenticated and match):
        return {"nav_section": ""}
    return {"nav_section": section_for(match.url_name, getattr(user, "is_owner", False))}
