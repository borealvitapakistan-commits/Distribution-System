from django import template

register = template.Library()


@register.inclusion_tag("manufacturers/_stage_tracker.html")
def order_stage_bar(order, current=None):
    """The slim four-stage bar at the top of an order's step pages.
    current overrides which stage is highlighted, for a page that
    belongs to a stage the order hasn't formally reached yet (recording
    the goods is stage 4 while the order is still a sent Purchase
    Order)."""
    from apps.manufacturers.views import order_steps

    steps = order_steps(order)
    if current is not None:
        for step in steps:
            step["state"] = (
                "done" if step["number"] < current
                else "current" if step["number"] == current
                else "upcoming"
            )
    return {"steps": steps, "compact": True}


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
