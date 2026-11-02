from django import template

register = template.Library()


@register.inclusion_tag("manufacturers/_stage_tracker.html")
def order_stage_bar(order, current=None):
    """The slim stage bar at the top of an order's step pages. current is
    the step the page belongs to ("quote", "stamped", "advance",
    "invoice"…), for a page the order hasn't formally reached yet."""
    from apps.manufacturers.views import order_steps

    return {"steps": order_steps(order, current=current), "compact": True}


@register.inclusion_tag("manufacturers/_stage_tracker.html")
def stage_tracker(steps, viewer="owner", compact=False):
    """The four-stage tracker for steps that carry wording for each side
    (plain_owner / plain_distributor, help_owner / help_distributor) —
    shown with the words for whoever is looking."""
    return {
        "steps": [
            {
                **step,
                "plain": step.get(f"plain_{viewer}", step.get("plain", "")),
                "help": step.get(f"help_{viewer}", step.get("help", "")),
            }
            for step in steps
        ],
        "compact": compact,
    }
