"""The owner talks to their own catalogue.

    "put the fan motors on buy three pay for two until the end of the month"
    "defrost thermostats are 68 now"
    "we have 12 door gaskets in"

Same pattern as the phone desk, pointed at the person who runs the business
rather than the person who rang it. A form has eleven fields and gets used
once; a sentence gets used.

It runs on the cheapest model that can do the job, because this is structured
extraction into four known functions rather than judgment, and it is a person
sitting at a screen who can see immediately if it got something wrong.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.tools import ToolContext

from . import console
from .config import settings


def _dealer(tool_context: ToolContext) -> str:
    return str(tool_context.state.get("dealer_id") or "D-REF")


def add_or_update_part(name: str, unit_cost: float, tool_context: ToolContext,
                       sku: str = "", lead_time_days: int = 0,
                       on_hand: int = 0) -> dict:
    """Add a part to this dealer's catalogue, or change one already there.

    Args:
        name: what it is called, e.g. "Defrost termination thermostat".
        unit_cost: what the dealer charges.
        sku: optional part number. One is generated if left blank.
        lead_time_days: days from the supplier when it is not in stock.
        on_hand: quantity at the main warehouse.
    """
    return console.upsert_part(_dealer(tool_context), name, unit_cost,
                               sku, lead_time_days, on_hand)


def change_price(part: str, unit_cost: float, tool_context: ToolContext) -> dict:
    """Change the price of a part that already exists.

    Args:
        part: the part number or its name.
        unit_cost: the new price.
    """
    return console.set_price(_dealer(tool_context), part, unit_cost)


def change_stock(part: str, on_hand: int, tool_context: ToolContext,
                 location: str = "") -> dict:
    """Set how many of a part we physically have.

    Args:
        part: the part number or its name.
        on_hand: the count.
        location: the warehouse by default, or a van's label.
    """
    return console.set_stock(_dealer(tool_context), part, on_hand, location)


def start_offer(headline: str, ends: str, tool_context: ToolContext,
                detail: str = "", terms: str = "",
                applies_to: list[str] | None = None) -> dict:
    """Put a real promotion on the record so the phone agent can mention it.

    This is the only way a discount can come into existence. Always confirm the
    end date with the owner: an offer without one cannot be created, and the
    date is what stops it running forever.

    Args:
        headline: what a customer is told, e.g. "10% off defrost components".
        ends: last valid day as YYYY-MM-DD. Required.
        detail: the fuller sentence.
        terms: conditions, e.g. "trade accounts, while stock lasts".
        applies_to: parts it covers, by name or SKU.
    """
    return console.create_promotion(_dealer(tool_context), headline, ends,
                                    detail, terms, applies_to)


def stop_offer(promotion_id: str, tool_context: ToolContext) -> dict:
    """End a promotion today rather than waiting for its date.

    Args:
        promotion_id: the offer's id, as shown in the console.
    """
    return console.end_promotion(_dealer(tool_context), promotion_id)


def what_to_reorder(tool_context: ToolContext) -> dict:
    """What is running short, how many to order, and what a stockout costs.

    Worked out from the parts this dealer has actually consumed over the last
    year, against the lead time to replace them and how long until anybody
    looks at the shelf again.

    This lives on the owner's console and not on the phone line on purpose.
    Deciding what to buy is the owner's job; the phone agent's job is to be
    honest about what is on the shelf right now.
    """
    from .ops import restock_advice
    return restock_advice(_dealer(tool_context))


def show_catalogue(tool_context: ToolContext) -> dict:
    """What this dealer currently sells, stocks and is running as offers."""
    s = console.snapshot(_dealer(tool_context))
    return {
        "parts": [{"sku": p["sku"], "name": p["name"], "price": p["unit_cost"],
                   "on_hand": p["on_hand"], "lead_days": p["lead_time_days"]}
                  for p in s["parts"]],
        "live_offers": [{"id": p["id"], "headline": p["headline"],
                         "ends": p["ends"], "parts": p["parts"]}
                        for p in s["promotions"]],
    }


def _instruction() -> str:
    """Built fresh each import so the model knows what today is.

    A model with no date guessed 2024 for "September 30" and the promotion was
    correctly refused as being in the past. The guard did its job; the model
    should not have needed it.
    """
    from datetime import date, timedelta
    today = date.today()
    return _BASE.format(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        month_end=(today.replace(day=28) + timedelta(days=4)).replace(day=1)
                  - timedelta(days=1),
    )


_BASE = """
Today is {weekday} {today}. End of this month is {month_end}. When somebody
says a date without a year they mean the next time that date occurs, never a
past one.

You keep a field service dealer's catalogue straight. The person talking to you
owns the business.

Turn what they say into the right call. Examples:
  "defrost thermostats are 68 now"        -> change_price
  "we got 12 door gaskets in"             -> change_stock
  "add a compressor relay, 54.75, 3 day"  -> add_or_update_part
  "10 percent off gaskets until the 30th" -> start_offer
  "what do we sell"                       -> show_catalogue
  "what do I need to order"               -> what_to_reorder
  "are we short on anything"              -> what_to_reorder

Rules:
- An offer must have an end date. If they do not give one, ask. Do not guess a
  date and do not create an offer that runs forever.
- Read back what you changed, with the old value and the new one, in one short
  line. They are looking at a screen and want confirmation, not a paragraph.
- If a part name does not match anything, say which one and ask rather than
  creating a duplicate under a slightly different name.
- You may not invent prices, parts or offers. Everything comes from what they
  told you.
- On reorder advice, give the quantity AND the reason in one line: how fast
  it moves, what is left, and how long it takes to replace. The owner is
  deciding whether to spend money, so the arithmetic is the point, not the
  recommendation. If a supplier has quoted on a part, mention it, but say a
  quote is not a delivery.
- If they ask you to do something to a customer, a job or a technician, say
  that this console is only for the catalogue and the stock.
"""

console_agent = LlmAgent(
    name="console",
    model=Gemini(model=settings.simple_model, client_kwargs={"location": "global"}),
    description="Keeps the dealer's parts, prices and offers up to date.",
    instruction=_instruction(),
    tools=[add_or_update_part, change_price, change_stock,
           start_offer, stop_offer, show_catalogue, what_to_reorder],
)
