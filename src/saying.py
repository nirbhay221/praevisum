"""The guard on what the desk SAYS, as against what it does.

THE HOLE THIS CLOSES, FOUND ON A LIVE CHANNEL

guards.py intercepts tool calls. Every rule in it -- ownership, intent,
consent, fitness, not escalating over a disproved fact -- is a lock on a door
the model can decline to walk through. If it never calls a tool, there is
nothing to intercept and the whole enforcement layer sits idle.

That is not theoretical. On WhatsApp, 30 August:

    customer: What about the Traulsen G12010, is that one covered?
    desk:     The Traulsen G12010 carries a standard 6-year parts and labor
              warranty (7 years on the compressor) for units installed on or
              after January 1, 2023.          <- CORRECT, matches warranty_terms

    customer: how much is a door gasket
    desk:     The door gasket for the Traulsen G12010 is $84.64        <- INVENTED

Zero tool calls were recorded for that exchange. Zero guard interventions. The
catalogue holds exactly one gasket, at $92.00, and $84.64 appears nowhere in
the database.

WHY IT HAPPENED, WHICH MATTERS MORE THAN THAT IT DID

Asked the same question with no prior context, the same code answers "$92.00,
10 in stock, and there is a 15% offer" -- correctly, having called the tools.
The difference was the previous turn. Because a Traulsen had just been
discussed, the model produced a Traulsen-SPECIFIC price rather than the generic
one it could have looked up. It preferred being specific to being right.

That is the failure mode this project was built around, arriving through the
one door nothing was watching.

THE RULE

A money amount may only leave this desk if a tool that returns money ran on
the same turn. Deterministic, cheap, and it cannot be talked past, because it
runs after the model rather than inside it.

WHAT IT DELIBERATELY DOES NOT DO

It does not rewrite the answer. A guard that silently edits what the desk says
is a second author nobody can audit. It replaces the turn with a refusal the
model can read and act on, exactly as guards.py does, and records the
interception so the count is honest.
"""

from __future__ import annotations

import re
from typing import Any

# Any tool whose result can legitimately put a number with a currency symbol
# in front of a customer. Everything else that mentions money is quoting from
# memory, which is the thing being stopped.
PRICING_TOOLS = {
    "lookup_product", "price_for", "alternatives", "check_stock",
    "product_availability", "options_under", "quote_delivery", "quote_visit",
    "offer_on_this", "current_deals", "recommend_equipment", "find_equipment",
    "what_we_know_about", "warranty_status", "supplier_options",
    "ask_suppliers", "create_purchase_order", "confirm_purchase_order",
    "waiting_on", "outside_opinion", "supply", "advice", "scheduling",
    "assessment", "what_we_sold_them", "warranty_options", "returns_about",
    "complaints_about", "register_return", "open_claim", "settle_claim",
}

# A price, in the shapes a model actually writes one:
#   $84.64   $1,649   USD 92.00   92.00 dollars
_MONEY = re.compile(
    r"(\$\s?\d[\d,]*(?:\.\d{1,2})?)"
    r"|(\bUSD\s?\d[\d,]*(?:\.\d{1,2})?)"
    r"|(\b\d[\d,]*(?:\.\d{1,2})?\s?dollars\b)",
    re.I)

# Said without a figure attached, these are descriptions rather than quotes and
# must not trip the guard: "free first-year labour", "no charge for the visit".
_NOT_A_QUOTE = ("free", "no charge", "at no cost", "nothing to pay")


def money_in(text: str) -> list[str]:
    """Every currency amount in a piece of text."""
    return [next(g for g in m.groups() if g)
            for m in _MONEY.finditer(text or "")]


def _tools_used(callback_context: Any) -> set[str]:
    """Which tools ran on this turn.

    Read from session state, written by guards.guard_tool, because that is the
    one place every tool call already passes through. Nothing else has to know
    this guard exists.
    """
    try:
        state = getattr(callback_context, "state", None)
        if state is None:
            return set()
        return set(state.get("tools_this_turn") or [])
    except Exception:
        return set()


def check_reply(text: str, used: set[str]) -> dict | None:
    """Whether this reply may go out. None means yes.

    Split from the callback so it can be tested without an agent, and so the
    rule is readable on its own.
    """
    amounts = money_in(text)
    if not amounts:
        return None

    if used & PRICING_TOOLS:
        return None

    # There was a third condition here and it was always true. It called
    # all() over an EMPTY sequence, which returns True, so the whole guard
    # returned None on every path and blocked nothing at all. It read as
    # careful and did nothing, which is the worst shape a guard can have.
    #
    # It survived being written and died on the first test, which is the
    # argument for testing a guard against the failure it was built for
    # rather than reading it back and agreeing with yourself.
    #
    # Nothing replaces it. The _NOT_A_QUOTE words only matter when a sentence
    # says "free" WITHOUT a figure, and a sentence with no figure has already
    # returned above.
    return {
        "blocked": True,
        "amounts": amounts,
        "why": (f"You said {', '.join(amounts[:3])} without calling anything "
                "that returns a price on this turn, so that number came from "
                "you rather than from our records."),
        "do_this": ("Call lookup_product, price_for or check_stock for the "
                    "exact item and quote what it returns. If the customer "
                    "named a machine we do not stock a specific part for, say "
                    "what we DO hold and what it costs, rather than pricing "
                    "the thing they named."),
        "this_happened": ("A live WhatsApp call quoted a door gasket at $84.64 "
                          "for a Traulsen G12010. The catalogue holds one "
                          "gasket, at $92.00. The number was invented because "
                          "a Traulsen had been mentioned a turn earlier."),
    }


def guard_saying(callback_context: Any, llm_response: Any):
    """ADK after_model_callback. Return None to allow the reply through.

    Returning an LlmResponse replaces what the model said, which is how the
    refusal reaches the model as something it can act on rather than as a
    silent edit.
    """
    try:
        content = getattr(llm_response, "content", None)
        parts = getattr(content, "parts", None) or []
        text = "".join(getattr(p, "text", "") or "" for p in parts)
        if not text.strip():
            return None

        # A turn that is only a tool call has no prose to check.
        if any(getattr(p, "function_call", None) for p in parts):
            return None

        verdict = check_reply(text, _tools_used(callback_context))
        if verdict is None:
            return None

        from .guards import _record

        _record("said_a_price_unchecked", "blocked",
                getattr(callback_context, "agent_name", "") or "",
                verdict["why"], None)

        from google.adk.models import LlmResponse
        from google.genai import types

        return LlmResponse(content=types.Content(
            role="model",
            parts=[types.Part(text=(
                verdict["why"] + " " + verdict["do_this"]))]))
    except Exception as e:
        # A guard that throws must never take a call down. It fails OPEN here,
        # unlike the ownership gate, because the cost of being wrong is a
        # number that should have been checked rather than another customer's
        # data.
        print(f"[saying] could not check the reply: "
              f"{type(e).__name__}: {e}", flush=True)
        return None
