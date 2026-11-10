from django import template

register = template.Library()


@register.inclusion_tag("manufacturers/_stage_tracker.html")
def order_stage_bar(order, current=None):
    """The slim stage bar at the top of an order's step pages. current is
    the step the page belongs to ("quote", "stamped", "advance",
    "invoice"…), for a page the order hasn't formally reached yet."""
    from apps.manufacturers.views import order_steps

    return {"steps": order_steps(order, current=current), "compact": True}

