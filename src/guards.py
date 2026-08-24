"""The lock, not the note on the door.

The four intents are decided by a model listening to a stressed person on a
narrowband phone line. Sometimes it will get that wrong. So the guard is
deliberately narrow:

  - Looking things up is ALWAYS allowed, whatever the intent is believed to be.
    A misheard sentence must never stop somebody finding out about their unit.
  - Only actions with consequences are gated: reserving stock, promising a
    technician's time, logging a commercial offer.
  - A blocked call is not a silent failure. It returns an explanation, which
    the model reads and can act on by re-routing. Being wrong is recoverable;
    being wrong AND stuck is not.

This exists because everywhere else in this system deterministic code decides
whether something may happen. Routing was the one place that was still only a
prompt asking nicely.
"""

from __future__ import annotations

from typing import Any

# Tools that change the world, and which intent legitimately owns them.
GATED: dict[str, set[str]] = {
    "promise_slot": {"service"},
    "build_briefing": {"service"},
    "open_work_order": {"service", "order"},
    "log_supplier_offer": {"supplier"},
}

_HUMAN = {
    "service": "a broken machine",
    "order": "buying a part",
    "product": "a question about products",
    "supplier": "a vendor selling to us",
}


def guard_tool(tool: Any, args: dict, tool_context: Any) -> dict | None:
    """ADK before_tool_callback. Return None to allow, a dict to block.

    Returning a dict short-circuits the tool: the model receives that dict as
    the tool result, so the refusal is legible to it rather than mysterious.
    """
    name = getattr(tool, "name", "") or getattr(tool, "__name__", "")
    allowed = GATED.get(name)
    if allowed is None:
        return None  # lookups and everything else: always permitted

    intent = (tool_context.state.get("intent") or "").strip().lower()

    if not intent:
        return {
            "blocked": True,
            "why": f"{name} changes something, and this call has not been "
                   "classified yet.",
            "do_this": "Ask them one short question to establish whether this "
                       "is a breakdown, an order, a product question, or a "
                       "vendor call. Then call set_intent and try again.",
        }

    if intent not in allowed:
        return {
            "blocked": True,
            "why": f"{name} belongs to a {'/'.join(sorted(allowed))} call, "
                   f"but this call is currently marked as {intent} "
                   f"({_HUMAN.get(intent, intent)}).",
            "do_this": "If you have misread what they want, call set_intent "
                       "with the correct one and try again. If you have not, "
                       "do not attempt this action.",
        }

    return None
