"""Posts a Request to Quote / Purchase Order to Slack so the team can
discuss changes. Uses a Slack incoming webhook (SLACK_WEBHOOK_URL)."""

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.accounts.services import require_owner
from apps.audit.services import record_audit_event


def slack_enabled():
    return bool(settings.SLACK_WEBHOOK_URL)


def _money(value):
    return f"Rs {value:,.2f}"


def order_message(order, *, link, note="", sender=""):
    """The Slack message (Block Kit) for one order."""
    lines = []
    for item in order.items.select_related("product"):
        size = f" ({item.get_bottle_size_display()})" if item.bottle_size else ""
        price = "to quote" if item.unit_price is None else f"{_money(item.unit_price)} each = {_money(item.line_total)}"
        lines.append(f"• *{item.product.name}*{size} × {item.quantity.normalize():f} — {price}")

    title = f"{order.get_status_display()} {order.po_number} · {order.manufacturer.name}"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
    ]
    if note:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Note:* {note}"[:3000]}})
    blocks += [
        {"type": "section", "text": {"type": "mrkdwn", "text": ("\n".join(lines) or "No products yet.")[:3000]}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Subtotal*\n{_money(order.subtotal)}"},
            {"type": "mrkdwn", "text": f"*Total*\n{_money(order.grand_total)}"},
        ]},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open order"}, "url": link},
        ]},
    ]
    if sender:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Shared by {sender}"}]})
    return {"text": f"{title} — {_money(order.grand_total)}", "blocks": blocks}


def share_order_to_slack(*, actor, order, link, note=""):
    require_owner(actor)
    if not slack_enabled():
        raise ValidationError("Slack isn't connected yet. Add SLACK_WEBHOOK_URL to the .env file.")

    note = (note or "").strip()
    payload = order_message(order, link=link, note=note, sender=actor.get_full_name() or actor.email)
    request = urllib.request.Request(
        settings.SLACK_WEBHOOK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValidationError("Couldn't reach Slack. Check the webhook URL and try again.") from exc

    record_audit_event(
        user=actor, action="manufacturer_order.shared_to_slack", instance=order,
        after_data={"po_number": order.po_number, "note": note},
    )
