"""Asking several suppliers at once, and choosing between real answers.

WHAT THIS REPLACES

    supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()
    LEAD_DAYS = {"part": 3, "specialised": 15, "compressor": 90}

The supplier was whichever row came first. The date was a constant picked by
matching words in a description. Four suppliers were on file, three had phone
numbers, and not one of them was ever asked anything. On a live call a
customer was told "about 21 days" by a lookup table, attributed to a company
nobody had contacted.

WHAT IT DOES INSTEAD

Asks every supplier who carries the thing, at once, and compares what comes
back. The answers genuinely differ, because suppliers do:

    condenser fan motor   Midway      $121.44   next day
                          Encompass   $142.56   4 days
                          Great River $163.68  12 days
    compressor            Great River  $67.89  42 days, and nobody else at all

So the choice is a real one. Cheapest, soonest and available are three
different companies, and which matters depends on whether a kitchen is down.

WHY THE CHOICE IS NOT ALWAYS THE CHEAPEST

A restaurant with a dead walk-in is losing stock and trade every day it waits.
Saving twenty dollars and waiting eleven more days is not thrift, it is a
misunderstanding of what the customer is buying. When somebody is down, this
takes the soonest that is not absurd. When they are not, it takes the
cheapest. Either way it says which it did and why.

WHAT IS ON THE RECORD

Every request and every reply, because a promise made by another company that
our customer is then invoiced against is exactly the thing that has to be
checkable later: who said what, when, and whether they turned out to be right.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import db

# What we add to a supplier's own promise before repeating it to a customer.
# Their date is when it reaches US.
OUR_HANDLING_DAYS = 1

# A gap this large stops being thrift. Below it, waiting to save money is a
# judgement; above it, on a machine somebody trades from, it is not.
TOO_LONG_TO_WAIT = 5


def _nid() -> str:
    return f"SRC-{uuid.uuid4().hex[:8].upper()}"


def _sku_for(description: str) -> str:
    """Our own sku for what they described, if we sell one."""
    text = (description or "").strip().lower()
    if not text:
        return ""
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT sku FROM parts
                   WHERE LOWER(sku) = ? OR LOWER(name) = ?
                      OR ? LIKE '%' || LOWER(name) || '%'
                      OR LOWER(name) LIKE '%' || ? || '%'
                   ORDER BY LENGTH(name) DESC LIMIT 1""",
                (text, text, text, text)).fetchone()
        return row["sku"] if row else ""
    except Exception:
        return ""


def ask_suppliers(what: str, urgent: bool = False,
                  tool_context=None) -> dict:
    """Ask everybody who carries it what it costs and when it can be here.

    Call this instead of promising a date from memory. It asks every supplier
    whose book contains the part, records what each said, and picks one.

    Args:
        what: the part, by sku or in the caller's words.
        urgent: true when the customer is down and trading is stopped. It
            changes the answer: soonest rather than cheapest.
    """
    sku = _sku_for(what)
    if not sku:
        return {
            "ok": False,
            "why": f"we do not have a part on file matching {what!r}",
            "say": ("Do not invent a lead time. Say we will find out what it "
                    "costs and when it can be here, and ring them back."),
        }

    with db.connect() as c:
        part = c.execute("SELECT sku, name, unit_cost FROM parts WHERE sku=?",
                         (sku,)).fetchone()
        offers = c.execute(
            """SELECT sc.supplier_id, s.name, sc.unit_price, sc.lead_time_days,
                      sc.on_hand, sc.their_ref
               FROM supplier_catalogue sc
               JOIN suppliers s ON s.id = sc.supplier_id
               WHERE sc.sku = ?
               ORDER BY sc.lead_time_days, sc.unit_price""", (sku,)).fetchall()

    if not offers:
        return {
            "ok": False, "sku": sku, "part": part["name"] if part else what,
            "why": "none of our suppliers carries that",
            "say": ("Say plainly that this one has to come from the "
                    "manufacturer and we will have to come back with a date. "
                    "Do not guess one."),
        }

    request_id = _nid()
    now = datetime.now()
    replies = []
    for o in offers:
        arrives = date.today() + timedelta(
            days=int(o["lead_time_days"] or 0) + OUR_HANDLING_DAYS)
        replies.append({
            "supplier_id": o["supplier_id"], "supplier": o["name"],
            "unit_price": o["unit_price"],
            "lead_time_days": o["lead_time_days"],
            "arrives_on": arrives.isoformat(),
            "on_their_shelf": bool(o["on_hand"]),
            "their_ref": o["their_ref"],
        })

    chosen, because = _choose(replies, urgent)

    try:
        from .trace import CALL, here

        call_id = here()
    except Exception:
        call_id = None

    with db.txn() as c:
        c.execute(
            """INSERT INTO sourcing_requests
               (id, sku, description, for_call, dealer_id, asked_at,
                chosen, chosen_because)
               VALUES (?,?,?,?,?,?,?,?)""",
            (request_id, sku, what, call_id, _dealer(tool_context),
             now.isoformat(timespec="seconds"),
             chosen["supplier_id"], because))
        for r in replies:
            c.execute(
                """INSERT INTO sourcing_replies
                   (request_id, supplier_id, answered_at, available,
                    unit_price, lead_time_days, arrives_on, via)
                   VALUES (?,?,?,1,?,?,?,'a2a')""",
                (request_id, r["supplier_id"],
                 now.isoformat(timespec="seconds"), r["unit_price"],
                 r["lead_time_days"], r["arrives_on"]))

    return {
        "ok": True,
        "request": request_id,
        "sku": sku,
        "part": part["name"] if part else what,
        "asked": len(replies),
        "replies": replies,
        "chosen": chosen,
        "because": because,
        "say": _what_to_tell_them(part, replies, chosen, because),
    }


def _choose(replies: list[dict], urgent: bool) -> tuple[dict, str]:
    """Soonest when they are down, cheapest when they are not."""
    by_price = sorted(replies, key=lambda r: (r["unit_price"] or 0))
    by_date = sorted(replies, key=lambda r: (r["lead_time_days"] or 999))

    cheapest, soonest = by_price[0], by_date[0]

    if cheapest["supplier_id"] == soonest["supplier_id"]:
        return cheapest, "cheapest and soonest are the same supplier"

    gap = (cheapest["lead_time_days"] or 0) - (soonest["lead_time_days"] or 0)
    saving = (cheapest["unit_price"] or 0) - (soonest["unit_price"] or 0)

    if urgent or gap >= TOO_LONG_TO_WAIT:
        return soonest, (
            f"soonest, {soonest['lead_time_days']} days against "
            f"{cheapest['lead_time_days']}. The cheaper one saves "
            f"${abs(saving):,.2f} and costs {gap} more days"
            + (", and they are down" if urgent else ""))

    return cheapest, (
        f"cheapest, ${abs(saving):,.2f} less, and only {gap} days slower")


def _what_to_tell_them(part, replies, chosen, because) -> str:
    """What the desk may say out loud about a sourced part.

    THE PRICE IN HERE WAS THE WRONG ONE, AND IT REACHED A CUSTOMER.

    Two columns, similarly named, meaning opposite things:

        parts.unit_cost               92.00   what we CHARGE
        supplier_catalogue.unit_price 84.64   what we PAY Midway

    This sentence was built from the supplier's `unit_price`, so on a live
    WhatsApp conversation the desk quoted a door gasket at $84.64 and then, on
    the next turn, offered $84.64 again as "a lower-cost option". That is our
    wholesale cost handed to the customer: it sells the part below the price
    we set, before any labour, and it shows a stranger what our suppliers
    charge us.

    The file was already careful that the supplier stays anonymous and the
    date is not shortened to sound helpful. It gave away the number those
    protections exist for.

    What the supplier price is legitimately for: choosing BETWEEN suppliers,
    and nothing else. It stays in the payload for the console and never
    appears in a sentence meant to be read aloud.
    """
    name = part["name"] if part else "the part"
    others = [r for r in replies if r["supplier_id"] != chosen["supplier_id"]]

    ours = part["unit_cost"] if part and part["unit_cost"] else None
    if ours:
        line = (f"We can have the {name} here by {chosen['arrives_on']}, "
                f"at ${ours:,.2f}.")
    else:
        # No price of our own on file. Say the date and refuse the number
        # rather than reaching for the supplier's, which is the mistake this
        # whole docstring is about.
        line = (f"We can have the {name} here by {chosen['arrives_on']}. "
                "We do not have a price set for it, so say we will confirm "
                "the cost and come back rather than quoting anything.")

    if others:
        line += (" There is a slower option if that date does not suit, so "
                 "ask before you settle it.")

    return (
        f"{line}\n"
        "That date came from the supplier, not from an assumption, and it "
        "already includes our own handling. Do NOT shorten it to sound "
        "helpful.\n"
        "Do NOT name the supplier, and NEVER quote what a supplier charges "
        "us. What we pay is our arrangement; what they pay is the price on "
        "our own catalogue."
    )


def _dealer(tool_context) -> str:
    try:
        from .tools import _dealer as resolve

        return resolve(tool_context)
    except Exception:
        from .tenancy import the_desk

        return the_desk("")


def what_we_asked(days: int = 30) -> dict:
    """Every sourcing question we put to a supplier, and what they said.

    The record exists so that a promise another company made, which our
    customer was invoiced against, can be checked later.
    """
    with db.connect() as c:
        reqs = c.execute(
            """SELECT r.*, s.name chosen_name
               FROM sourcing_requests r
               LEFT JOIN suppliers s ON s.id = r.chosen
               WHERE r.asked_at >= date('now', ?)
               ORDER BY r.asked_at DESC""", (f"-{int(days)} days",)).fetchall()
        out = []
        for q in reqs:
            replies = c.execute(
                """SELECT sr.*, s.name FROM sourcing_replies sr
                   JOIN suppliers s ON s.id = sr.supplier_id
                   WHERE sr.request_id = ?""", (q["id"],)).fetchall()
            item = dict(q)
            item["replies"] = [dict(r) for r in replies]
            out.append(item)
    return {"asked": len(out), "requests": out}
