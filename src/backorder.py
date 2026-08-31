"""Sourcing a machine somebody has bought that we do not hold.

WHAT WAS MISSING

`confirm_purchase_order` set the status to confirmed and stopped. It never
asked whether we had the machine, never ordered one, and never told the
customer the wait was because we were sourcing it.

So an order could be confirmed for a freezer nobody owned, and nothing
anywhere would buy one. The customer waits, indefinitely, for a machine that
was never ordered. The schema had anticipated this the whole time: both
`purchase_orders` and `supply_orders` carry `equipment_id`. Only the link
between them was absent.

BACK-TO-BACK, WHICH IS WHAT THE TRADE CALLS IT

A purchase order raised on a supplier on the back of a customer's order, hard
pegged to it. Three details from how distributors actually run this, and each
one is a thing that goes wrong if you skip it:

  ONE SUPPLY ORDER TO ONE CUSTOMER LINE. Not pooled with replenishment.

  WHAT ARRIVES IS RESERVED. The entire point of the peg is that when the
  machine reaches the warehouse it is not taken by another order. Receiving it
  onto general stock, which is what `receive` does today, defeats the whole
  mechanism.

  IT CARRIES THE CUSTOMER REFERENCE AND THE REQUIRED DATE, so a buyer chasing
  a supplier knows who is waiting and until when, rather than chasing a line
  item with no face behind it.

WHY THE KIND MATTERS

Replenishment and a customer waiting are different orders that happened to
live in the same table looking identical. One is "the shelf is getting low".
The other has a restaurant behind it with a dinner service tonight. A buyer
opening the list could not tell which was which, so both got the same
attention, which means the wrong one got it.

THE LEAD TIMES ARE THE TRADE'S, NOT MINE

Published ranges for this industry: one to three days for common parts, five
to fifteen for specialised components, thirty to ninety for OEM compressors
and coils. A whole machine is a factory build slot, not a shelf item.

Quoting the short end of a range to sound helpful is how a customer ends up
told two weeks and waiting six, so the LONG end is quoted and beating it is a
pleasant surprise rather than the plan.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import db
from .tenancy import the_desk

# Why an order exists. The distinction a buyer needs and did not have.
REPLENISHMENT = "replenishment"      # the shelf is getting low
BACK_TO_BACK = "back_to_back"        # a named customer is waiting on this one
EMERGENCY = "emergency"              # a machine is down now

# Working days to get something in, by what it is. The upper end of the
# published trade ranges, because a promise that is beaten is a good day and a
# promise that is missed is a complaint.
LEAD_DAYS = {
    "part": 3,               # common parts, 1 to 3 days
    "specialised": 15,       # control boards, variable-speed modules, 5 to 15
    "compressor": 90,        # OEM compressors and coils, 30 to 90
    "machine": 21,           # a whole machine off a build slot
}

# Words in a description that mean the long lead time applies. A compressor is
# not a fortnight, whatever the catalogue says.
LONG_LEAD = ("compressor", "condensing unit", "coil", "evaporator coil")
SPECIALISED = ("control board", "controller", "variable speed", "inverter",
               "communicating", "module")

# THE WORDS THAT UNDO THE WORDS ABOVE, and this is not a nicety.
#
# "Compressor overload relay" and "compressor start capacitor" are shelf parts
# costing forty and fifty dollars. Matching on "compressor" quoted a customer
# NINETY DAYS for one, which is the same failure this codebase has hit twice
# before: "Continental" matching car tyres, "bunn" inside "Woven Bunny
# Baskets". A word appearing in a name is not the thing.
#
# An item that is a relay, a capacitor or a gasket is a part, whatever else is
# in its name.
ACTUALLY_A_PART = ("relay", "capacitor", "contactor", "gasket", "harness",
                   "thermostat", "switch", "fuse", "filter", "kit", "seal",
                   "valve", "sensor", "probe", "bulb", "fan blade")

# What a whole machine is called. A machine ordered by description rather than
# by catalogue id was being given a three day PART lead time, which is a
# promise nobody could keep.
A_MACHINE = ("freezer", "cooler", "refrigerator", "chiller", "ice machine",
             "merchandiser", "prep table", "walk-in", "reach-in",
             "undercounter")

# Days on top of the supplier lead time before we promise it to a customer.
# Goods in, checking it arrived undamaged, and getting it on a van. A promise
# that assumes the supplier's date IS the customer's date is a promise that
# breaks on the last mile.
OUR_HANDLING_DAYS = 2


def _lead_days(description: str, is_machine: bool,
               sku: str = "") -> tuple[int, str]:
    """How long this actually takes, and why.

    Asked of what we KNOW before what we can guess. `parts.lead_time_days` is
    a real figure sitting in the database, and inferring a lead time from
    words in a name while ignoring it is how "Compressor overload relay"
    became a ninety day wait.
    """
    low = (description or "").lower()

    # 1. The real figure, if we hold one.
    if sku:
        try:
            with db.connect() as c:
                row = c.execute(
                    "SELECT lead_time_days FROM parts WHERE sku = ?",
                    (sku,)).fetchone()
            if row and row["lead_time_days"] is not None:
                return int(row["lead_time_days"]), "our own catalogue lead time"
        except Exception:
            pass

    # 2. A part is a part whatever is in its name.
    if any(w in low for w in ACTUALLY_A_PART):
        return LEAD_DAYS["part"], "a shelf part"

    # 3. A whole machine, by catalogue id or by what it plainly is.
    if is_machine or any(w in low for w in A_MACHINE):
        return LEAD_DAYS["machine"], "a whole machine, which comes off a build slot"

    if any(w in low for w in LONG_LEAD):
        return LEAD_DAYS["compressor"], "an OEM compressor or coil, which is a factory order"
    if any(w in low for w in SPECIALISED):
        return LEAD_DAYS["specialised"], "a specialised component rather than a shelf part"
    return LEAD_DAYS["part"], "an ordinary stocked part"


def _on_the_floor(description: str, dealer_id: str = "") -> int:
    """How many we actually hold. Zero is the interesting answer.

    ASKED THE WRONG SHELF, AND ORDERED WHAT WE ALREADY HAD.

    This was scoped to one vendor, and its caller defaulted that vendor to
    D-REF. So on a live call a customer asked for a Brother HL-L2400D, this
    looked for it on the REFRIGERATION shelf, found nothing, and the desk
    said:

        "We don't have the Brother HL-L2400D in stock right now, but we can
         order it in. It typically takes about 21 days to get here."

    There were thirteen of them, on the IT shelf, at $187.56. A supply order
    was raised against a supplier we did not need to buy from, and the
    customer was promised a date three weeks out for something that could
    have gone out that afternoon.

    ONE DESK MEANS ONE FLOOR. The caller rang one number and is buying from
    one counter. Which of our suppliers happens to hold the thing is our
    business, and it is exactly the question this is supposed to answer, so
    it now looks across all of them unless a caller deliberately narrows it.
    """
    try:
        with db.connect() as c:
            if dealer_id:
                row = c.execute(
                    """SELECT on_hand FROM product_stock
                       WHERE dealer_id = ?
                         AND (? LIKE '%' || model_number || '%'
                              OR model_number = ?)
                       ORDER BY on_hand DESC LIMIT 1""",
                    (dealer_id, description or "", description or "")
                ).fetchone()
            else:
                row = c.execute(
                    """SELECT on_hand FROM product_stock
                       WHERE (? LIKE '%' || model_number || '%'
                              OR model_number = ?)
                       ORDER BY on_hand DESC LIMIT 1""",
                    (description or "", description or "")).fetchone()
        return int(row["on_hand"]) if row else 0
    except Exception:
        return 0


def _who_supplies(description: str, fallback: str = "") -> str:
    """Which vendor a supply order belongs to.

    The stock check looks across the whole desk on purpose, because a caller
    buying from one counter does not care whose shelf it came off. The supply
    order is different: it is a real order placed with a real vendor's
    supplier, and `supply_orders.dealer_id` is a foreign key. It needs an
    actual vendor, not an empty string.

    Asked of the thing itself, through the same family-to-vendor route the
    desk uses on a live call, so the order lands on the book that would have
    filled it.
    """
    try:
        from .desk import _vendor_for

        found = _vendor_for(description or "")
        if found.get("found"):
            return found["dealer_id"]
    except Exception as e:
        print(f"[backorder] could not tell whose order this is: "
              f"{type(e).__name__}: {e}", flush=True)

    if fallback:
        return fallback

    try:
        with db.connect() as c:
            row = c.execute("SELECT id FROM dealers ORDER BY id LIMIT 1").fetchone()
        return row["id"] if row else ""
    except Exception:
        return ""


def source_order(purchase_order_id: str, dealer_id: str = "") -> dict:
    """Raise supply orders for anything on this order we do not hold.

    Called when a customer order is confirmed. Every line we cannot fill off
    the shelf gets its own supply order, pegged to that line and reserved, so
    what arrives cannot be sold to somebody else.

    Args:
        purchase_order_id: the confirmed customer order.
        dealer_id: narrow to one vendor's shelf. Empty, the default, means
            the whole desk, which is the right question on a counter that
            several suppliers stand behind. A default naming one tenant is
            how a printer we had thirteen of got ordered in from scratch.
    """
    with db.connect() as c:
        po = c.execute("SELECT id, status FROM purchase_orders WHERE id=?",
                       (purchase_order_id,)).fetchone()
        if po is None:
            return {"ok": False, "why": "no such order"}

        lines = c.execute(
            """SELECT line_no, sku, equipment_id, description, qty
               FROM purchase_lines WHERE po_id=? ORDER BY line_no""",
            (purchase_order_id,)).fetchall()

        supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()

    if not lines:
        return {"ok": False, "why": "that order has no lines on it"}

    sourced, from_stock = [], []
    for line in lines:
        held = _on_the_floor(line["description"], dealer_id)
        if held >= (line["qty"] or 1) and not line["equipment_id"]:
            from_stock.append(line["description"])
            continue
        if held >= (line["qty"] or 1):
            from_stock.append(line["description"])
            continue

        days, why = _lead_days(line["description"],
                                bool(line["equipment_id"]), line["sku"])
        expected = date.today() + timedelta(days=days)
        promised = expected + timedelta(days=OUR_HANDLING_DAYS)

        order_id = "SO-" + uuid.uuid4().hex[:6].upper()
        try:
            with db.txn() as c:
                c.execute(
                    """INSERT INTO supply_orders
                       (id,dealer_id,supplier_id,sku,equipment_id,qty,unit_cost,
                        reason,status,placed_at,expected_at,kind,
                        for_purchase_order,for_line,promised_by,reserved)
                       VALUES (?,?,?,?,?,?,?,?,'placed',?,?,?,?,?,?,1)""",
                    (order_id, _who_supplies(line["description"], dealer_id),
                     supplier["id"] if supplier else None,
                     line["sku"], line["equipment_id"], line["qty"] or 1, None,
                     f"{purchase_order_id} line {line['line_no']}: a customer "
                     f"is waiting on this. {why}",
                     datetime.now().isoformat(timespec="seconds"),
                     expected.isoformat(), BACK_TO_BACK,
                     purchase_order_id, line["line_no"], promised.isoformat()))
                c.execute(
                    "UPDATE purchase_lines SET sourced_by=? "
                    "WHERE po_id=? AND line_no=?",
                    (order_id, purchase_order_id, line["line_no"]))
        except Exception as e:
            print(f"[backorder] could not raise {order_id}: "
                  f"{type(e).__name__}: {e}", flush=True)
            return {"ok": False,
                    "why": "we could not raise the supply order",
                    "say": "Do NOT promise a delivery date. Say we are "
                           "arranging it and somebody will confirm."}

        sourced.append({
            "supply_order": order_id,
            "what": line["description"],
            "lead_days": days,
            "why": why,
            "expected_here": expected.isoformat(),
            "promised_by": promised.isoformat(),
        })

    if not sourced:
        return {
            "ok": True, "sourced": [], "from_stock": from_stock,
            "say": "Everything on this order is on the floor. Give them the "
                   "delivery window quote_delivery returned.",
        }

    longest = max(sourced, key=lambda s: s["lead_days"])
    return {
        "ok": True,
        "sourced": sourced,
        "from_stock": from_stock,
        "ready_by": longest["promised_by"],
        "say": (
            "Tell them plainly that we do not have it on the floor and are "
            f"ordering it in: {longest['why']}. Give the date, "
            f"{longest['promised_by']}, and say it is when we expect to have "
            "it WITH THEM rather than when the supplier ships it. "
            "Give them the order number. Do not shorten the date to sound "
            "helpful: somebody told two weeks who waits six rings back angry, "
            "and somebody told six weeks who gets it in four does not."),
    }


def waiting_on(dealer_id: str = "") -> dict:
    """Customer orders waiting on a supplier, worst overdue first.

    The list a buyer needs in the morning. Replenishment is deliberately left
    out: this is only the orders with somebody waiting on the other end.
    """
    dealer_id = the_desk(dealer_id)
    today = date.today().isoformat()
    with db.connect() as c:
        rows = c.execute(
            """SELECT so.id, so.for_purchase_order, so.for_line, so.status,
                      so.expected_at, so.promised_by, so.reason,
                      po.account_id, a.name account
               FROM supply_orders so
               LEFT JOIN purchase_orders po ON po.id = so.for_purchase_order
               LEFT JOIN accounts a ON a.id = po.account_id
               WHERE so.dealer_id = ? AND so.kind = ?
                 AND so.status NOT IN ('received','cancelled')
               ORDER BY so.promised_by""", (dealer_id, BACK_TO_BACK)).fetchall()

    out = [dict(r) | {"late": bool(r["promised_by"] and r["promised_by"] < today)}
           for r in rows]
    return {
        "waiting": len(out),
        "late": sum(1 for r in out if r["late"]),
        "orders": out,
        "say": "Each of these has a named customer on the other end. A late "
               "one is somebody who was given a date and has not been rung.",
    }


def receive_reserved(supply_order_id: str) -> dict:
    """A pegged delivery arrived. It belongs to its customer, not the shelf.

    Receiving a back-to-back order onto general stock is the failure the peg
    exists to prevent: the machine turns up, goes on the shelf, and is sold to
    whoever asks next while the customer who paid for it keeps waiting.

    Args:
        supply_order_id: the supply order that arrived.
    """
    with db.connect() as c:
        so = c.execute(
            """SELECT id, kind, status, for_purchase_order, for_line, reserved
               FROM supply_orders WHERE id = ?""", (supply_order_id,)).fetchone()
    if so is None:
        return {"ok": False, "why": "no such supply order"}
    if so["status"] == "received":
        return {"ok": True, "already": True, "supply_order": supply_order_id}

    with db.txn() as c:
        c.execute("UPDATE supply_orders SET status='received', received_at=? "
                  "WHERE id=?",
                  (datetime.now().isoformat(timespec="seconds"), supply_order_id))

    if so["kind"] != BACK_TO_BACK or not so["for_purchase_order"]:
        # Ordinary replenishment. The existing goods-in path owns the shelf.
        from .supply import receive

        return receive(supply_order_id)

    return {
        "ok": True,
        "supply_order": supply_order_id,
        "reserved_for": so["for_purchase_order"],
        "line": so["for_line"],
        "say": (f"This one is spoken for: it belongs to {so['for_purchase_order']}. "
                "Do NOT put it on the general shelf and do not let it be sold "
                "to anybody else. Ring that customer and book the delivery."),
    }
