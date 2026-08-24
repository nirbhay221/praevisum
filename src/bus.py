"""Work that must outlive the phone call, handed to Pub/Sub.

WHY THIS EXISTS, AND IT IS NOT THE HACKATHON RULE

The briefing is the thing this product is named for: a message to the
technician before they leave, saying what to put in the van. It was computed
correctly, in full, with the reasoning and the money, and then returned as a
dictionary that nothing ever sent. The feature everyone talks about stopped at
"calculated".

Fixing that by calling Twilio inline would have been wrong, and the reason is
the same one the hold music exists for: **the caller is still on the line**
when the briefing is built. An SMS API call on that path is dead air, and if
Twilio is slow or down the customer hears it. So the briefing is published and
the turn continues. A worker delivers it, retries if Twilio is unreachable,
and the conversation never knows.

The outreach queue has the opposite shape and the same answer. The sweep
decides at midnight who is worth ringing; the calls happen during business
hours inside each customer's quiet window. Producer and consumer are separated
by hours, which is a queue whether or not you call it one.

OFF BY DEFAULT

`PRAEVISUM_BUS=1` turns publishing on. Without it every publish is a no-op that
returns immediately, so tests, local development and the live phone line run
exactly as before and nothing bills. Pub/Sub charges on message volume with the
first 10 GiB a month free, and an idle topic costs nothing at all, so this is
off to keep behaviour predictable rather than to save money.

Publishing never raises. A message bus that can break a phone call is worse
than no message bus.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

# What we publish. Two topics because they are consumed by different things at
# different times, not because two sounded better than one.
BRIEFINGS = "praevisum-briefings"
OUTREACH = "praevisum-outreach"

_publisher = None
_warned = False


def enabled() -> bool:
    return os.getenv("PRAEVISUM_BUS") == "1"


def _client():
    global _publisher
    if _publisher is None:
        from google.cloud import pubsub_v1
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def _project() -> str:
    return os.getenv("GOOGLE_CLOUD_PROJECT", "")


def publish(topic: str, payload: dict, **attributes: str) -> dict:
    """Put a message on a topic. Never raises, never blocks a call.

    Returns what happened rather than nothing, so a caller that wants to know
    can check, and one that does not can ignore it.
    """
    global _warned

    if not enabled():
        return {"published": False, "why": "bus disabled"}

    project = _project()
    if not project:
        if not _warned:
            print("[bus] PRAEVISUM_BUS is on but GOOGLE_CLOUD_PROJECT is unset",
                  flush=True)
            _warned = True
        return {"published": False, "why": "no project configured"}

    body = dict(payload)
    body.setdefault("published_at", datetime.now().isoformat(timespec="seconds"))

    try:
        client = _client()
        path = client.topic_path(project, topic)
        future = client.publish(
            path,
            json.dumps(body, default=str).encode("utf-8"),
            **{k: str(v) for k, v in attributes.items() if v is not None},
        )
        # Waiting here is deliberate and bounded. Publishing is milliseconds,
        # and a fire-and-forget publish that silently fails would put us back
        # where we started: a briefing nobody receives and nobody notices.
        message_id = future.result(timeout=5)
        return {"published": True, "topic": topic, "message_id": message_id}
    except Exception as e:
        # The phone call matters more than the message. Say so and carry on.
        print(f"[bus] publish to {topic} failed: {type(e).__name__}: {e}",
              flush=True)
        return {"published": False, "why": f"{type(e).__name__}"}


def send_briefing(brief: dict, dealer_id: str = "D-REF") -> dict:
    """Hand a finished briefing to whatever delivers messages.

    Carries the technician's number and the work order as attributes so a
    subscriber can filter without parsing the body.
    """
    return publish(
        BRIEFINGS,
        {
            "kind": "briefing",
            "dealer_id": dealer_id,
            "work_order_id": brief.get("work_order_id"),
            "visit_id": brief.get("visit_id"),
            "technician": brief.get("technician"),
            "window": brief.get("window"),
            "customer": brief.get("customer"),
            "site": brief.get("site"),
            "address": brief.get("address"),
            "machine": brief.get("machine"),
            "reported": brief.get("reported"),
            "safety": brief.get("safety"),
            "load_these": brief.get("load_these"),
            "left_behind": brief.get("left_behind"),
            "reasoning": brief.get("reasoning"),
            "text": render_briefing(brief),
        },
        dealer_id=dealer_id,
        work_order_id=brief.get("work_order_id"),
    )


def render_briefing(brief: dict) -> str:
    """The briefing as a technician would actually read it on a phone.

    Written here rather than by the model. This message is the product, it goes
    out unattended, and nobody reads it before it is sent, so it is assembled
    from the computed facts instead of being narrated. A hallucinated part
    number in an unattended SMS is a wasted trip nobody chose.
    """
    lines = [
        f"{brief.get('window') or 'Scheduled'} - {brief.get('customer') or ''}",
        f"{brief.get('site') or ''}, {brief.get('address') or ''}".strip(", "),
        f"Machine: {brief.get('machine') or 'unknown'}",
        f"Reported: {brief.get('reported') or 'not stated'}",
    ]
    if brief.get("where_on_site"):
        lines.append(f"Where: {brief['where_on_site']}")
    if brief.get("access_note"):
        lines.append(f"Access: {brief['access_note']}")
    if brief.get("safety"):
        lines.append(f"SAFETY: {brief['safety']}")

    load = brief.get("load_these") or []
    if load:
        lines.append("")
        lines.append("TAKE:")
        for p in load:
            pct = p.get("likelihood")
            pct = f" ({int(pct * 100)}%)" if isinstance(pct, (int, float)) else ""
            lines.append(f"  - {p.get('name')}{pct}")
    else:
        lines.append("")
        lines.append("TAKE: nothing specific. Our history has no match for this.")

    left = brief.get("left_behind") or []
    if left:
        lines.append("")
        lines.append("LEFT BEHIND:")
        for p in left[:3]:
            lines.append(f"  - {p.get('name')}: {p.get('why','')}"[:90])

    if brief.get("reasoning"):
        lines.append("")
        lines.append(brief["reasoning"])

    return "\n".join(lines)


def send_outreach(item: dict, dealer_id: str = "D-REF") -> dict:
    """Hand one queued call to whatever places calls.

    `kind` rides as an attribute so a subscriber can route a safety recall
    differently from a sales call without opening the message.
    """
    return publish(
        OUTREACH,
        {
            "kind": item.get("kind"),
            "dealer_id": dealer_id,
            "outreach_id": item.get("outreach_id") or item.get("id"),
            "account_id": item.get("account_id"),
            "account": item.get("account_name") or item.get("account"),
            "asset_id": item.get("asset_id"),
            "reason": item.get("reason"),
            "evidence": item.get("evidence"),
            "say": item.get("say"),
            "priority": item.get("priority"),
        },
        dealer_id=dealer_id,
        outreach_kind=item.get("kind"),
    )
