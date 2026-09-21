from .models import Brand


def brand(request):
    """Makes the single Brand record available to every template as
    {{ brand }}, since no User points at it anymore."""
    return {"brand": Brand.objects.first()}


def owner_notifications(request):
    """New (never-opened) Distributor purchase orders, for the sidebar
    badge and the Dashboard notification card. Owner-only, computed once
    per request."""
    user = getattr(request, "user", None)

    if not (user and user.is_authenticated and getattr(user, "is_owner", False)):
        return {"new_purchase_orders": None, "new_purchase_orders_count": 0}

    from apps.requests.models import PurchaseOrder

    new_purchase_orders = (
        PurchaseOrder.objects
        .unseen_by_owner()
        .select_related("distributor_profile")
        .order_by("-created_at")[:5]
    )

    return {
        "new_purchase_orders": new_purchase_orders,
        "new_purchase_orders_count": PurchaseOrder.objects.unseen_by_owner().count(),
    }
