"""Ordering from a supplier, which is the direction that was missing.

WHAT WAS THERE, AND WHY IT WAS ONLY HALF

`restock_advice` works out what to reorder, how many, and what a stockout
costs, from parts this dealer has actually consumed. It is careful work: the
cost of being short is a truck roll rather than the price of the part, which
is why a cheap part with a long lead time can be more urgent than an expensive
one.

Then it stopped. `purchase_orders` is the customer's side, where `account_id`
is who is buying from us. Nothing anywhere recorded this dealer ordering from
a supplier, so the advice was handed to a person and the system never learned
whether anything happened.

That made two completely different situations look identical from inside:

    we knew and did not order
    we ordered and it is late

The first is a process failure and the second is a supplier problem, and until
now the shelf ran empty the same way in both.

WHY WHAT WAS ADVISED IS KEPT NEXT TO WHAT WAS ORDERED

`advised_qty` is what the arithmetic said and `qty` is what somebody actually
bought. A buyer who consistently orders half the recommendation is either
wiser than the model or costing the company truck rolls, and there is no way
to tell which without both numbers side by side.

MACHINES ARE STOCKED FOR THE OPPOSITE REASON TO PARTS

Parts are held because a missing one fails a service call, so availability
beats cost efficiency. A machine is held at real capital cost against a sale
that may not come, so cost efficiency beats availability. Two tables, two
policies, because one of each would force the wrong answer on the other.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from . import db


def _nid() -> str:
    return f"SO-{uuid.uuid4().hex[:6].upper()}"


def place_supply_order(sku: str, qty: int, dealer_id: str = "D-REF",
                       advised_qty: int = 0, reason: str = "",
                       stockout_cost: float = 0.0,
                       supplier_id: str = "") -> dict:
    """Order a part from a supplier, and record why.

    Args:
        sku: the part.
        qty: how many are actually being ordered.
        dealer_id: whose shelf.
        advised_qty: what restock_advice recommended, kept so the two can be
            compared later.
        reason: why, in the same money the van loading uses.
        stockout_cost: what being short of this one costs.
        supplier_id: who from. Taken from the part if omitted.
    """
    if qty <= 0:
        return {"ok": False, "why": "nothing to order"}

    with db.connect() as c:
        part = c.execute(
            """SELECT sku, name, unit_cost, lead_time_days, supplier_id
               FROM parts WHERE sku = ? AND dealer_id = ?""",
            (sku, dealer_id)).fetchone()
    if part is None:
        return {"ok": False, "why": "no such part on this dealer's catalogue"}

    supplier = supplier_id or part["supplier_id"]
    lead = part["lead_time_days"] or 0
    oid = _nid()
    now = datetime.now()

    with db.txn() as c:
        c.execute(
            """INSERT INTO supply_orders
               (id,dealer_id,supplier_id,sku,advised_qty,qty,unit_cost,reason,
                stockout_cost,status,placed_at,expected_at)
               VALUES (?,?,?,?,?,?,?,?,?,'placed',?,?)""",
            (oid, dealer_id, supplier, sku, advised_qty or None, qty,
             part["unit_cost"], reason or None, stockout_cost or None,
             now.isoformat(timespec="seconds"),
             (now + timedelta(days=lead)).isoformat(timespec="seconds")))

    return {"ok": True, "order": oid, "sku": sku, "name": part["name"],
            "qty": qty, "advised": advised_qty or None,
            "expected": (now + timedelta(days=lead)).date().isoformat(),
            "cost": round((part["unit_cost"] or 0) * qty, 2)}


def receive(order_id: str, qty: int = 0) -> dict:
    """Stock arrived. Puts it on the shelf and closes the order.

    Args:
        order_id: the supply order.
        qty: how many actually turned up, if it was short.
    """
    with db.connect() as c:
        o = c.execute("SELECT * FROM supply_orders WHERE id = ?",
                      (order_id,)).fetchone()
    if o is None:
        return {"ok": False, "why": "no such order"}
    if o["status"] == "received":
        return {"ok": True, "already": True, "order": order_id}

    got = qty or o["qty"]
    now = datetime.now().isoformat(timespec="seconds")

    with db.txn() as c:
        c.execute("""UPDATE supply_orders SET status='received', received_at=?
                     WHERE id=?""", (now, order_id))
        if o["sku"]:
            # Onto the main shelf rather than a van. A delivery arrives at the
            # counter, and which van carries it is a separate decision the van
            # loading already makes per visit.
            loc = c.execute(
                """SELECT id FROM stock_locations
                   WHERE dealer_id = ? AND kind <> 'van' ORDER BY id LIMIT 1""",
                (o["dealer_id"],)).fetchone()
            if loc is not None:
                c.execute(
                    """INSERT INTO stock (location_id,sku,on_hand)
                       VALUES (?,?,?)
                       ON CONFLICT(location_id,sku) DO UPDATE SET
                         on_hand = on_hand + excluded.on_hand""",
                    (loc["id"], o["sku"], got))

    return {"ok": True, "order": order_id, "sku": o["sku"], "received": got,
            "short_by": (o["qty"] - got) if got < o["qty"] else 0}


def on_order(dealer_id: str = "D-REF") -> dict:
    """What is coming, what is late, and what was never ordered at all.

    The distinction the missing table made impossible. A shelf that runs empty
    because nobody placed the order is a different problem from one that runs
    empty because a supplier is late, and they need different phone calls.
    """
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as c:
        open_orders = c.execute(
            """SELECT o.*, p.name FROM supply_orders o
               LEFT JOIN parts p ON p.sku = o.sku
               WHERE o.dealer_id = ? AND o.status NOT IN ('received','cancelled')
               ORDER BY o.expected_at""", (dealer_id,)).fetchall()

    late = [o for o in open_orders if (o["expected_at"] or "") < now]
    return {
        "open": len(open_orders),
        "late": len(late),
        "coming": [{"order": o["id"], "sku": o["sku"], "name": o["name"],
                    "qty": o["qty"], "expected": (o["expected_at"] or "")[:10],
                    "late": (o["expected_at"] or "") < now}
                   for o in open_orders],
        "say": ("A part that is short and not on this list was never ordered, "
                "which is a different problem from a supplier being late and "
                "needs a different phone call."),
    }


def advised_but_not_ordered(dealer_id: str = "D-REF") -> list[dict]:
    """What the arithmetic asked for and nobody bought.

    The gap the missing table hid. Every row here is a truck roll the desk
    already priced and somebody quietly declined to prevent.
    """
    from .restock import restock_advice

    advice = restock_advice(dealer_id)
    if not advice.get("ok", True):
        return []

    with db.connect() as c:
        pending = {r["sku"] for r in c.execute(
            """SELECT DISTINCT sku FROM supply_orders
               WHERE dealer_id = ? AND status NOT IN ('received','cancelled')""",
            (dealer_id,))}

    out = []
    for row in advice.get("order", []):
        if row["sku"] in pending:
            continue
        out.append({
            "sku": row["sku"], "name": row["name"],
            "advised": row.get("order_qty") or row.get("target"),
            "why": row.get("note") or row.get("why"),
            "say": "Advised and not ordered. Nothing is coming.",
        })
    return out


# --------------------------------------------------------------------------
# whole machines, which are stocked for the opposite reason to parts
# --------------------------------------------------------------------------

def product_availability(manufacturer: str, model_number: str = "",
                         dealer_id: str = "D-REF") -> dict:
    """Whether we actually have a machine, and how long if not.

    The desk could recommend a Traulsen over a Beverage-Air, weigh their
    running costs from federal data and quote the delivery, and had no way to
    answer whether one was in the building. Its own hard rule is never to say
    something is available unless a tool said so, and for machines no tool
    could say anything at all.

    Args:
        manufacturer: the make.
        model_number: the model, if they have it.
        dealer_id: whose shelf.
    """
    with db.connect() as c:
        if model_number:
            row = c.execute(
                """SELECT * FROM product_stock
                   WHERE dealer_id=? AND manufacturer=? AND model_number=?""",
                (dealer_id, manufacturer, model_number)).fetchone()
        else:
            row = c.execute(
                """SELECT * FROM product_stock
                   WHERE dealer_id=? AND manufacturer=?
                   ORDER BY on_hand DESC LIMIT 1""",
                (dealer_id, manufacturer)).fetchone()

    if row is None:
        return {"stocked": False,
                "manufacturer": manufacturer, "model": model_number,
                "why": "we do not stock that machine",
                "say": "Say we do not carry it rather than implying we might. "
                       "Offer to price it in if they want, and do not invent a "
                       "lead time."}

    return {
        "stocked": True,
        "manufacturer": row["manufacturer"], "model": row["model_number"],
        "on_hand": row["on_hand"], "on_order": row["on_order"],
        "lead_time_days": row["lead_time_days"],
        "price": row["list_price"],
        "say": (f"{row['on_hand']} in stock." if row["on_hand"] else
                f"None in stock. {row['lead_time_days']} days from the "
                f"supplier, and {row['on_order']} already on order."
                if row["on_order"] else
                f"None in stock, {row['lead_time_days']} days to get one."),
    }
