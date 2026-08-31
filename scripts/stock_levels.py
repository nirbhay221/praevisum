"""Put a believable quantity behind each product.

WHAT WAS THERE

    on_hand=0    283 products
    on_hand=1     48 products
    on_hand=2      4 products
    on_order>0     0 products

Three hundred and thirty five products holding fifty six units between them,
and nothing anywhere on order. That is not a dealer, it is a list. It came
from a counter in the seeder: the first three rows of each family got a one
and everything after got a zero, which is not a fact about anything.

It shows on a call. Somebody asks what a business has and the honest answer
is always "one" or "none", every line reads the same, and nothing is ever
arriving, so the whole sourcing half of the system, which is the interesting
half, never has a reason to fire.

WHAT DECIDES A QUANTITY IN A REAL BUSINESS

Not chance. Price relative to the rest of the family, which is a decent proxy
for how fast the thing turns:

  A dealer stocks cheap fast lines DEEP. Printers, small UPS units, budget
  laptops. They sell weekly, they are cheap to hold, and being out of one
  loses a sale to whoever has it on the shelf.

  Mid range is held THIN. A few, reordered as they go.

  Capital equipment is NOT STOCKED AT ALL, and this is the part that reads as
  wrong until you have seen a dealership. Nobody keeps a fourteen thousand
  dollar walk-in cooler in the yard on the chance somebody wants one. It is
  quoted, ordered, and built. Zero on hand is the correct and professional
  answer, provided the desk then says "I can have that for you by the
  fourteenth" rather than "we do not have one".

AND SOME OF IT IS SELLING AWAY

The other half of the complaint. Real stock is in motion: things run down,
replenishment is in transit, and a fast line is sometimes at zero with a
delivery due Thursday. So a share of the fast movers sit at zero on hand with
units on order, which is a different sentence from "we do not carry it" and
the desk has the machinery to say it.

DETERMINISTIC

Seeded per product, so the same catalogue always produces the same book and a
test can rely on it. Reproducible, not random.

    python -m scripts.stock_levels           show the distribution
    python -m scripts.stock_levels --write   apply it
"""

from __future__ import annotations

import hashlib
import sys

from src import db

# What a fast, a middling and a capital item cost, as a share of the median
# price in their own family. Relative, so it works for a $200 printer and a
# $14,000 walk-in without a table of thresholds per family.
FAST_UNDER = 0.60
MID_UNDER = 1.60

# How deep each tier is held. A range, not a number, so the book has texture.
DEPTH = {
    "fast": (3, 14),
    "mid": (1, 4),
    "capital": (0, 1),
}

# How often a tier is currently sold out with a delivery on the way.
SELLING_AWAY = {"fast": 0.30, "mid": 0.18, "capital": 0.10}


def _roll(*parts: str) -> float:
    """A stable 0..1 for this product. Same product, same answer, always."""
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _tier(price: float, median: float) -> str:
    if median <= 0:
        return "mid"
    share = price / median
    if share <= FAST_UNDER:
        return "fast"
    if share <= MID_UNDER:
        return "mid"
    return "capital"


def plan() -> dict:
    with db.connect() as c:
        rows = c.execute(
            "SELECT rowid, dealer_id, manufacturer, model_number, family, "
            "list_price FROM product_stock WHERE list_price IS NOT NULL"
        ).fetchall()

    # The median price WITHIN each family, so every family is judged against
    # its own trade rather than against laptops.
    by_family: dict[str, list[float]] = {}
    for r in rows:
        by_family.setdefault(r["family"] or "", []).append(r["list_price"])
    median = {f: sorted(v)[len(v) // 2] for f, v in by_family.items() if v}

    out = []
    for r in rows:
        fam = r["family"] or ""
        tier = _tier(r["list_price"], median.get(fam, 0))
        lo, hi = DEPTH[tier]

        seed = f"{r['manufacturer']}|{r['model_number']}|{fam}"
        depth = lo + int(_roll(seed, "depth") * (hi - lo + 1))
        depth = min(depth, hi)

        on_order = 0
        if _roll(seed, "moving") < SELLING_AWAY[tier]:
            # Selling away: run down to nothing with replenishment in transit.
            on_order = max(1, depth if depth else lo or 1)
            depth = 0
        elif tier == "fast" and _roll(seed, "topup") < 0.35:
            # Or simply low and being topped up, which is the ordinary state
            # of a fast line.
            on_order = 2 + int(_roll(seed, "topupn") * 6)

        out.append({"rowid": r["rowid"], "tier": tier, "on_hand": depth,
                    "on_order": on_order, "family": fam,
                    "model": f"{r['manufacturer']} {r['model_number']}",
                    "price": r["list_price"]})
    return {"rows": out}


def write() -> dict:
    out = plan()
    with db.txn() as c:
        for r in out["rows"]:
            c.execute("UPDATE product_stock SET on_hand=?, on_order=? "
                      "WHERE rowid=?",
                      (r["on_hand"], r["on_order"], r["rowid"]))
    return out


if __name__ == "__main__":
    doing = "--write" in sys.argv
    out = write() if doing else plan()
    rows = out["rows"]

    tiers: dict[str, list[dict]] = {}
    for r in rows:
        tiers.setdefault(r["tier"], []).append(r)

    print(f"\n{len(rows)} products")
    for tier in ("fast", "mid", "capital"):
        rs = tiers.get(tier, [])
        if not rs:
            continue
        units = sum(r["on_hand"] for r in rs)
        coming = sum(r["on_order"] for r in rs)
        out_of = len([r for r in rs if not r["on_hand"] and r["on_order"]])
        print(f"  {tier:<8} {len(rs):>4} lines   {units:>5} on hand   "
              f"{coming:>4} on order   {out_of:>3} selling away")

    print(f"\n  {sum(r['on_hand'] for r in rows)} units on the floor, "
          f"{sum(r['on_order'] for r in rows)} arriving")
    print(f"  {len([r for r in rows if r['on_hand']])} lines you can sell today")

    print("\n  a few, for the look of it:")
    for r in sorted(rows, key=lambda x: -x["on_hand"])[:5]:
        print(f"    {r['on_hand']:>3} on hand  {r['model'][:42]:<42} "
              f"${r['price']:,.0f}")
    for r in [x for x in rows if not x["on_hand"] and x["on_order"]][:3]:
        print(f"      0 on hand  {r['model'][:42]:<42} "
              f"${r['price']:,.0f}  ({r['on_order']} arriving)")

    print("\nwritten" if doing else "\nnothing written, pass --write")
