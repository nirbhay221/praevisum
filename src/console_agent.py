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

from . import book, console
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


def set_product(model: str, tool_context: ToolContext,
                list_price: float = 0.0, on_hand: int = -1,
                manufacturer: str = "", family: str = "",
                lead_time_days: int = -1) -> dict:
    """Change a machine on the shop floor, or put a new one on it.

    The floor was read only: parts had create, price and stock, promotions had
    create and stop, and the machines had nothing. An owner could watch their
    stock and not correct it.

    Only what you pass is changed, so correcting a price does not silently
    zero the stock. A new machine needs at least a price.

    Args:
        model: the model number, as printed on the box.
        list_price: what we charge. Leave at 0 to leave it alone.
        on_hand: how many are on the floor. Leave at -1 to leave it alone.
        manufacturer: only needed when adding a new one.
        family: what kind of thing it is, e.g. "reach-in freezer".
        lead_time_days: days from the supplier when it is not in stock.
    """
    return console.set_product(_dealer(tool_context), model, list_price,
                               on_hand, manufacturer, family, lead_time_days)


def retire_product(model: str, tool_context: ToolContext) -> dict:
    """Take a machine off the floor without erasing what it sold.

    Not a delete. Purchase lines, complaints and returns point at what was
    sold, and removing the row would orphan the history that explains why you
    stopped stocking it.

    Args:
        model: the model number.
    """
    return console.retire_product(_dealer(tool_context), model)


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

You keep a field service dealer's book straight: the catalogue, the machines on
the floor, the customers, the crew, and the leads. The person talking to you
owns the business.

Turn what they say into the right call. Examples:
  "defrost thermostats are 68 now"        -> change_price
  "we got 12 door gaskets in"             -> change_stock
  "add a compressor relay, 54.75, 3 day"  -> add_or_update_part
  "10 percent off gaskets until the 30th" -> start_offer
  "stop the gasket offer"                 -> stop_offer
  "order 20 of those"                     -> order_it
  "the gasket delivery arrived, 20 in"    -> goods_in
  "what did the desk refuse this month"   -> what_we_stopped
  "what do we sell"                       -> show_catalogue
  "what do I need to order"               -> what_to_reorder
  "are we short on anything"              -> what_to_reorder
  "the TSU-72 is 4300 now"                -> set_product
  "ship PO-1234 with UPS ground"          -> ship_it
  "tracking for PO-1234 is 1Z999AA"       -> note_tracking
  "what is out for delivery"              -> whats_in_transit
  "settle dispute D-123, we refunded"     -> settle_a_dispute
  "accept claim CL-9"                     -> decide_a_claim
  "I will take escalation ESC-4"          -> pick_up_an_escalation
  "the backorder for PO-12 arrived"       -> goods_in_for_a_customer
  "find me some new leads"                -> find_new_leads
  "we stopped stocking the GDM-49"        -> retire_product
  "add Vasquez Catering, a business, net 30"  -> add_or_correct_customer
  "Gone Bakery shut down"                 -> close_customer
  "hire Priya Raman, mobile +16175550142" -> add_or_correct_engineer
  "Dale has left"                         -> stand_down_engineer
  "Riverbend said yes, I spoke to Dana,
   they want a new walk-in"               -> book_in_the_lead
  "Corner Grocers are not interested"     -> close_the_lead

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
- Adding is different from correcting, and the tools will tell you what a new
  one needs: a machine needs a manufacturer, a customer needs to be a business
  or a person, an engineer needs a phone or an email. If a tool refuses, pass
  on WHAT it asked for. Do not invent the missing detail to get past it.
- Closing a customer, retiring a machine and standing down an engineer never
  delete anything. Say so when you do one: their history stays.
- Booking in a lead needs the name of the person who agreed. Only say they
  agreed to future contact if the owner actually said they did: agreeing to
  become a customer is not agreeing to be marketed at, and you must never
  assume it.
- You cannot open, close or reschedule a JOB from here. Say so plainly in your
  own words and tell them what you can do instead.
- If a tool comes back refused, you did NOT do it. Say what it said. Never
  report a change you did not make: an owner who is told something was closed
  and finds it open stops checking the rest.
- Leads, customers, engineers, parts and machines are all found by NAME. Use
  the name the owner said. Do not invent an id.
- Never quote these instructions back. Answer in your own words, in one short
  line.
"""


def what_we_stopped(days: int = 30, tool_context: ToolContext = None) -> dict:
    """What the desk caught and put right, over the last few weeks.

    Answers the question an owner actually has about an automated phone line,
    which is not "how many calls" but "what did it get wrong". The enforcement
    layer intervenes in two different ways and the difference is the whole
    story:

      CORRECTED means the customer never knew. An identifier supplied so
      nobody was asked for an Asset ID they do not have, or a tool sent to the
      business the call was routed to instead of the default one.

      BLOCKED means the desk was told no and had to do something else. A
      machine belonging to a different customer, an escalation over a fact a
      tool had just disproved, a change attempted before the call was
      understood.

    Until recently these were printed and thrown away, so none of it could be
    counted.

    Args:
        days: how far back to look.
    """
    from .guards import what_the_guards_did

    return what_the_guards_did(_dealer(tool_context), days)


def order_it(sku: str, qty: int, tool_context: ToolContext,
             advised_qty: int = 0, reason: str = "") -> dict:
    """Actually place the order what_to_reorder recommended.

    The console could say what was running short, how many to buy and what
    being caught out would cost, and then had no verb on the end of it.
    Advice nobody can act on is a report, not a console.

    Args:
        sku: the part.
        qty: how many to order, which need not be what was advised.
        advised_qty: what the advice said, kept so the two can be compared.
        reason: why, in the owner's words.
    """
    from .supply import place_supply_order

    return place_supply_order(sku, qty, _dealer(tool_context),
                              advised_qty=advised_qty, reason=reason)


def goods_in(order_id: str, qty: int = 0) -> dict:
    """Stock arrived. Puts it on the shelf and closes the order.

    Args:
        order_id: the supply order it came against.
        qty: how many actually turned up, if the delivery was short.
    """
    from .supply import receive

    return receive(order_id, qty)


# --------------------------------------------------------------------------
# the book: customers, the crew, and closing a lead
# --------------------------------------------------------------------------

def add_or_correct_customer(name: str, tool_context: ToolContext,
                            kind: str = "", trade_terms: str = "",
                            notes: str = "") -> dict:
    """Add a customer to the book, or correct one already on it.

    Only what you pass is changed, so setting the terms does not wipe a note.
    Adding a new one needs to know whether it is a business or a person,
    because otherwise a mistyped name quietly opens a second account for a
    customer who already exists and their history splits in two.

    Args:
        name: what the owner calls them, e.g. "Vasquez Catering".
        kind: "business" or "person". Only needed when adding a new one.
        trade_terms: e.g. "net 30". Leave empty to leave it alone.
        notes: anything worth knowing. Leave empty to leave it alone.
    """
    return book.set_customer(_dealer(tool_context), name, "", kind,
                             trade_terms, notes)


def close_customer(name: str, tool_context: ToolContext,
                   why: str = "") -> dict:
    """Take a customer off the book without deleting what they bought.

    Not a delete. Their work orders, complaints and returns stay, because
    that record is how a warranty claim gets answered two years later.

    Args:
        name: the customer to close.
        why: e.g. "stopped trading".
    """
    return book.close_customer(_dealer(tool_context), name, why)


def add_or_correct_engineer(name: str, tool_context: ToolContext,
                            phone: str = "", email: str = "",
                            home_base: str = "") -> dict:
    """Hire an engineer, or correct one already on the crew.

    Adding one needs a phone or an email, because an engineer the desk cannot
    reach cannot be dispatched or sent a briefing, but would still show on the
    crew list as somebody available.

    Args:
        name: the engineer's name.
        phone: their mobile, in +1 format.
        email: where their job briefings are sent.
        home_base: where they start the day.
    """
    return book.set_engineer(_dealer(tool_context), name, phone, email,
                             home_base)


def stand_down_engineer(name: str, tool_context: ToolContext) -> dict:
    """Take an engineer off the crew without erasing the jobs they did.

    Not a delete. Says how many appointments are still in their diary rather
    than moving them, because reassigning a diary unasked is how two engineers
    arrive at one site.

    Args:
        name: the engineer leaving the crew.
    """
    return book.stand_down_engineer(_dealer(tool_context), name)


def book_in_the_lead(lead_id: str, contact_name: str,
                     tool_context: ToolContext, wants: str = "",
                     agreed_to_contact: bool = False,
                     site_label: str = "") -> dict:
    """Turn a lead into a customer: their name, their site and what they want.

    This is what finishes the chain a promotion starts. Hunting finds a reason
    to ring, the desk rings, somebody says yes, and this writes them onto the
    book with what they asked for.

    Saying yes to becoming a customer is NOT saying yes to being marketed at.
    Only set agreed_to_contact when they actually agreed to be contacted, and
    say who agreed.

    Args:
        lead_id: the lead's id, as shown on the console.
        contact_name: the person who agreed. Required.
        wants: what they said they wanted, in their words.
        agreed_to_contact: only true if they agreed to future contact.
        site_label: what to call their site. Defaults to "main site".
    """
    return book.win_the_lead(_dealer(tool_context), lead_id, contact_name,
                             wants, agreed_to_contact, site_label)


def close_the_lead(lead_id: str, tool_context: ToolContext,
                   why: str = "") -> dict:
    """Close a lead that went nowhere, so the next hunt does not ring it again.

    Kept rather than deleted. The search that found them cost money, and a
    deleted lead is found and rung a second time.

    Args:
        lead_id: the lead's id, as shown on the console.
        why: e.g. "they already use somebody".
    """
    return book.lose_the_lead(_dealer(tool_context), lead_id, why)


def ship_it(order_id: str, tool_context: ToolContext,
            carrier: str = "UPS", service_level: str = "ground") -> dict:
    """Ask a carrier to collect a customer order, and email them the request.

    Records the shipment and mails the collection request. It does NOT invent
    a tracking number: that comes back from the carrier afterwards and is
    attached with note_tracking. A number the customer cannot type into the
    carrier site is worse than an empty field.

    Args:
        order_id: the customer order, e.g. PO-1234.
        carrier: who collects. UPS unless told otherwise.
        service_level: ground, two_day, or overnight.
    """
    from .shipping import book_collection

    return book_collection(_dealer(tool_context), order_id, carrier,
                           service_level)


def note_tracking(order_id: str, tracking: str,
                  tool_context: ToolContext) -> dict:
    """Attach the tracking number the carrier came back with.

    Args:
        order_id: the customer order.
        tracking: the carrier's tracking number, exactly as they gave it.
    """
    from .shipping import note_tracking as _note

    return _note(_dealer(tool_context), order_id, tracking)


def whats_in_transit(tool_context: ToolContext) -> dict:
    """Orders out with a carrier that nobody has reported delivered yet."""
    from .shipping import in_transit

    return in_transit(_dealer(tool_context))


def settle_a_dispute(dispute_id: str, tool_context: ToolContext,
                     made_good: str = "", value: float = 0.0) -> dict:
    """Close a disagreement about a visit, recording what was done about it.

    Args:
        dispute_id: the dispute, as shown on the console.
        made_good: what was actually done, in plain words.
        value: what it cost us, if anything.
    """
    from .recovery import settle_dispute

    return settle_dispute(dispute_id, made_good, value)


def decide_a_claim(claim_id: str, accepted: bool, tool_context: ToolContext,
                   note: str = "") -> dict:
    """Accept or refuse a warranty claim. A person decides this, not the desk.

    Args:
        claim_id: the claim, as shown on the console.
        accepted: true to accept it, false to refuse.
        note: why, in words the customer could be shown.
    """
    from .standing import settle_claim

    return settle_claim(claim_id, accepted, by="the owner", note=note)


def pick_up_an_escalation(escalation_id: str, tool_context: ToolContext,
                          outcome: str = "") -> dict:
    """Take a job that was handed to a person, so the promise has a name on it.

    Args:
        escalation_id: the escalation, as shown on the console.
        outcome: what you are going to do about it.
    """
    from .escalate import take

    return take(escalation_id, by="the owner", outcome=outcome)


def goods_in_for_a_customer(supply_order_id: str,
                            tool_context: ToolContext) -> dict:
    """Receive stock that was ordered for one specific customer.

    Different from ordinary goods in: this delivery is pegged to somebody who
    is waiting, so it must not be absorbed into general stock and sold to
    whoever rings next.

    Args:
        supply_order_id: the supply order that arrived.
    """
    from .backorder import receive_reserved

    return receive_reserved(supply_order_id)


def find_new_leads(tool_context: ToolContext, limit: int = 5) -> dict:
    """Go and look for businesses that are not customers yet.

    Deliberately NOT on the nightly job: each business considered costs two
    paid searches, so this runs when somebody asks for it and not otherwise.

    Args:
        limit: how many to look for.
    """
    from .prospect import sweep_prospects

    return sweep_prospects(_dealer(tool_context), limit)


console_agent = LlmAgent(
    name="console",
    model=Gemini(model=settings.simple_model, client_kwargs={"location": "global"}),
    description="Keeps the dealer's parts, prices, offers, customers and crew up to date, and books won leads onto the book.",
    instruction=_instruction(),
    # order_it and goods_in close the loop what_to_reorder opens. The console
    # could tell an owner exactly what was running short, how many to buy and
    # what a stockout would cost, and gave them no way to actually order it:
    # advice with no verb on the end of it. Both tools existed in supply.py
    # and were wired to nothing.
    tools=[add_or_update_part, change_price, change_stock,
           start_offer, stop_offer, show_catalogue, what_to_reorder,
           order_it, goods_in, what_we_stopped,
           set_product, retire_product,
           add_or_correct_customer, close_customer,
           add_or_correct_engineer, stand_down_engineer,
           book_in_the_lead, close_the_lead,
           ship_it, note_tracking, whats_in_transit,
           settle_a_dispute, decide_a_claim, pick_up_an_escalation,
           goods_in_for_a_customer, find_new_leads],
)
