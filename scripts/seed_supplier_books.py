"""Give each supplier a book of its own, so asking them is a real question.

WHY THIS HAS TO EXIST BEFORE ANY SOURCING IS WORTH DOING

Four suppliers were on file and none of them could be asked anything. There
was no catalogue, no price, no lead time, nothing. So backorder.py did the
only thing available to it:

    supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()

and paired that with a constant from a lookup table. A customer waiting on a
condenser fan motor was told "about 21 days" by a dict.

Asking three suppliers is only meaningful if their answers can differ, and
they differ in the ways real suppliers do rather than at random:

  MIDWAY PARTS CO is the local factor. Cheapest on ordinary parts, holds them
  on the shelf, next day. Does not stock the specialised lines at all.

  ENCOMPASS SUPPLY is the national distributor. Dearer, but carries almost
  everything including control boards, and ships from a central warehouse so
  the lead time is steady rather than fast.

  GREAT RIVER is the OEM route. Dearest and slowest, and the only one
  carrying the OEM lines. When the others say no, they are the answer.
  On this book that mostly means control boards: there is no whole
  compressor on file, only a start capacitor and an overload relay,
  both of which are shelf parts.

That is a genuine trade-off, which is the point: cheapest, fastest and
available are three different suppliers, and choosing between them while a
kitchen is down is a decision rather than a lookup.

The prices sit around what our own catalogue says a part costs, because
parts.unit_cost is what we pay for them.

    python -m scripts.seed_supplier_books
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from src import db

# supplier -> (price multiplier, lead time, stocks it, what they are)
PROFILE = {
    "SUP-1": {
        "name": "Midway Parts Co",
        "multiplier": 0.92, "lead": 1, "stocks": ("part",),
        "note": "local factor, next day on shelf stock, no specialised lines",
    },
    "SUP-2": {
        "name": "Encompass Supply",
        "multiplier": 1.08, "lead": 4, "stocks": ("part", "specialised"),
        "note": "national distributor, carries almost everything, steady",
    },
    "SUP-3": {
        "name": "Great River Refrigeration Supply",
        "multiplier": 1.24, "lead": 12, "stocks": ("part", "specialised", "oem"),
        "note": "OEM route, dearest and slowest, the only source for a "
                "compressor or a coil",
    },
}

# Which tier a part belongs to.
#
# THE TRAP THIS FILE FELL INTO ONCE.
#
# The first version matched the word "compressor" and put a 42 day OEM lead
# time on a "Compressor start capacitor", which is a fifty five dollar shelf
# part. backorder.py already carries a comment about exactly this, because it
# made the same mistake before:
#
#     "Compressor overload relay" is a fifty dollar shelf part and matching
#     on the word "compressor" quoted a customer NINETY DAYS for one.
#
# So the shelf-part list is imported from there rather than written again.
# Two lists that can disagree about what a compressor is are worse than one.
SPECIALISED_WORDS = ("control board", "electronic", "module", "inverter")
OEM_WORDS = ("compressor", "evaporator coil", "condenser coil")


def _tier(name: str) -> str:
    from src.backorder import ACTUALLY_A_PART

    low = (name or "").lower()
    # A relay, a capacitor or a harness is a shelf part whatever else its
    # name contains. This test comes first for that reason.
    if any(w in low for w in ACTUALLY_A_PART):
        return "part"
    if any(w in low for w in OEM_WORDS):
        return "oem"
    if any(w in low for w in SPECIALISED_WORDS):
        return "specialised"
    return "part"


def load() -> dict:
    db.init()
    now = datetime.now().isoformat(timespec="seconds")

    with db.connect() as c:
        # A supplier quotes on ITS OWN vendor's parts. All four suppliers
        # here belong to the refrigeration business, and the first version of
        # this had them quoting on laptop batteries and printer fusers.
        parts = c.execute(
            """SELECT p.sku, p.name, p.unit_cost, p.dealer_id
               FROM parts p WHERE p.unit_cost IS NOT NULL""").fetchall()
        sup_rows = c.execute("SELECT id, dealer_id FROM suppliers").fetchall()
        have = {r["id"]: r["dealer_id"] for r in sup_rows}

    written = 0
    with db.txn() as c:
        for sup, p in PROFILE.items():
            if sup not in have:
                continue
            c.execute("UPDATE suppliers SET notes=? WHERE id=?",
                      (p["note"], sup))
            for part in parts:
                if part["dealer_id"] and have.get(sup) and \
                        part["dealer_id"] != have[sup]:
                    continue          # another vendor's part, not theirs
                tier = _tier(part["name"])
                if tier not in p["stocks"]:
                    continue          # they genuinely do not carry it
                price = round(part["unit_cost"] * p["multiplier"], 2)
                # OEM lines take longer from everybody who has them.
                lead = p["lead"] + (30 if tier == "oem" else
                                    4 if tier == "specialised" else 0)
                on_hand = 4 if (tier == "part" and sup == "SUP-1") else \
                          2 if tier == "part" else 0

                # AND SOMETIMES THE CHEAP ONE IS OUT.
                #
                # The first version made price and speed perfectly
                # correlated: the cheapest supplier was also always the
                # fastest, so "cheapest or soonest" was never a decision and
                # the choice logic could not fire. Real books do not look
                # like that. A local factor who is normally next-day is
                # sometimes out of a line, and then the dearer distributor
                # who has it on the shelf is genuinely the better answer.
                #
                # Deterministic ACROSS PROCESSES. Python's built-in hash()
                # is salted per interpreter, so the first version of this
                # produced a different book on every run and the claim that a
                # test could rely on it was false.
                seed = hashlib.sha256(
                    f"{sup}{part['sku']}".encode()).hexdigest()
                out_of_stock = (int(seed[:8], 16) % 5) == 0
                if out_of_stock:
                    on_hand = 0
                    lead += 9
                c.execute(
                    """INSERT OR REPLACE INTO supplier_catalogue
                       (supplier_id, sku, their_ref, unit_price,
                        lead_time_days, on_hand, min_order_qty, updated_at)
                       VALUES (?,?,?,?,?,?,1,?)""",
                    (sup, part["sku"], f"{sup[-1]}{part['sku'][2:8]}",
                     price, lead, on_hand, now))
                written += 1

    with db.connect() as c:
        rows = c.execute(
            """SELECT s.name, COUNT(*) lines, MIN(sc.lead_time_days) fastest,
                      ROUND(AVG(sc.unit_price), 2) avg_price
               FROM supplier_catalogue sc JOIN suppliers s ON s.id = sc.supplier_id
               GROUP BY s.id ORDER BY avg_price""").fetchall()
    return {"written": written, "books": [dict(r) for r in rows]}


if __name__ == "__main__":
    out = load()
    print(f"{out['written']} catalogue lines written\n")
    for b in out["books"]:
        print(f"  {b['name'][:34]:<34} {b['lines']:>3} lines  "
              f"from {b['fastest']} days  avg ${b['avg_price']:,.2f}")

    from src import db as _db
    with _db.connect() as c:
        print("\n  who can supply a condenser fan motor, and on what terms:")
        for r in c.execute(
                """SELECT s.name, sc.unit_price, sc.lead_time_days, sc.on_hand
                   FROM supplier_catalogue sc JOIN suppliers s ON s.id=sc.supplier_id
                   WHERE sc.sku='P-CONDFAN' ORDER BY sc.lead_time_days"""):
            shelf = f"{r['on_hand']} on their shelf" if r["on_hand"] else "to order"
            print(f"    {r['name'][:32]:<32} ${r['unit_price']:>7.2f}  "
                  f"{r['lead_time_days']:>2} days  {shelf}")

        print("\n  and a compressor, which most of them cannot get at all:")
        for r in c.execute(
                """SELECT s.name, sc.unit_price, sc.lead_time_days
                   FROM supplier_catalogue sc JOIN suppliers s ON s.id=sc.supplier_id
                   WHERE sc.sku LIKE '%COMPRESS%' ORDER BY sc.lead_time_days"""):
            print(f"    {r['name'][:32]:<32} ${r['unit_price']:>7.2f}  "
                  f"{r['lead_time_days']:>2} days")
