from .models import Brand


def brand(request):
    """Makes the single Brand record available to every template as
    {{ brand }}, since no User points at it anymore."""
    return {"brand": Brand.objects.first()}


def _latest_steps(purchase_orders):
    """Attaches latest_step — the last thing said on each order — for the
    notification lists."""
    from apps.requests.models import PurchaseOrderRevision

    purchase_orders = list(purchase_orders)
    latest = {}
    for revision in (
        PurchaseOrderRevision.objects
        .filter(purchase_order__in=purchase_orders)
        .order_by("purchase_order_id", "-number")
    ):
        latest.setdefault(revision.purchase_order_id, revision)
    for purchase_order in purchase_orders:
        purchase_order.latest_step = latest.get(purchase_order.pk)
    return purchase_orders


def owner_notifications(request):
    """New (never-opened) Distributor purchase orders, and orders where
    the Distributor has said something since the Owner last looked — a
    counter-offer or an accepted quote — for the sidebar badge and the
    Dashboard notification cards. Owner-only, computed once per request."""
    user = getattr(request, "user", None)

    if not (user and user.is_authenticated and getattr(user, "is_owner", False)):
        return {
            "new_purchase_orders": None,
            "new_purchase_orders_count": 0,
            "purchase_order_updates": None,
            "purchase_order_updates_count": 0,
            "purchase_orders_attention_count": 0,
        }

    from apps.requests.models import PurchaseOrder

    new_purchase_orders = (
        PurchaseOrder.objects
        .unseen_by_owner()
        .select_related("distributor_profile")
        .order_by("-created_at")[:5]
    )
    new_count = PurchaseOrder.objects.unseen_by_owner().count()

    updates = PurchaseOrder.objects.with_updates_for_owner().select_related(
        "distributor_profile"
    ).order_by("-updated_at")
    updates_count = updates.count()

    return {
        "new_purchase_orders": new_purchase_orders,
        "new_purchase_orders_count": new_count,
        "purchase_order_updates": _latest_steps(updates[:5]) if updates_count else None,
        "purchase_order_updates_count": updates_count,
        "purchase_orders_attention_count": new_count + updates_count,
    }


def distributor_notifications(request):
    """Agreements the Owner has sent that this Distributor still has to
    sign, and orders the Owner has answered (a quote, a changed Purchase
    Order, the invoice) since the Distributor last looked — for the
    sidebar badges and the Dashboard notification cards."""
    user = getattr(request, "user", None)

    if not (user and user.is_authenticated and getattr(user, "is_distributor", False)):
        return {
            "agreements_to_sign": None,
            "agreements_to_sign_count": 0,
            "order_updates": None,
            "order_updates_count": 0,
        }

    from apps.agreements.models import Agreement
    from apps.requests.models import PurchaseOrder

    agreements_to_sign = list(
        Agreement.objects
        .for_user(user)
        .awaiting_signature()
        .order_by("-created_at")
    )

    updates = (
        PurchaseOrder.objects
        .for_user(user)
        .with_updates_for_distributor()
        .order_by("-updated_at")
    )
    updates = _latest_steps(updates)

    return {
        "agreements_to_sign": agreements_to_sign,
        "agreements_to_sign_count": len(agreements_to_sign),
        "order_updates": updates[:5] or None,
        "order_updates_count": len(updates),
    }
