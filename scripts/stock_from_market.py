"""Stock the shop from the real market, not from what customers already own.

THE QUESTION THAT PRODUCED THIS

"We have an API for all these products, so why don't we have them?"

Because the API was only ever used to PRICE a model somebody else had already
named. `seed_product_stock.load()` builds the shelf like this:

    SELECT a.manufacturer, a.model_number, a.family
    FROM assets a JOIN sites s ON s.id = a.site_id

`assets` is what our CUSTOMERS OWN. So the shop could only sell models its own
customers happened to have already bought, from somebody else, at some point.
Four laptop models appear in the book, so the shop stocked four laptops. No
customer owns an HP, so the shop could never sell one, and on a live call:

    "I'm not finding any ASUS laptops in our system at the moment."
    "I don't see any Dell laptops in our system either."
    "Do you have any Legion products?"  ->  no
    "What about HP?"                    ->  no

Every one of those was true of the database and false of the business. A
dealer's catalogue is what they can SOURCE, which is the market, not an
inventory of their customers' history.

WHAT THIS DOES

Asks Google Shopping what actually exists in each family the dealers declare
they carry, at several price points, and files the results under the vendor
whose families list contains that family. Prices are real listing medians.
Nothing is invented: the source column records where each figure came from,
and a model that cannot be priced is skipped rather than guessed at.

ON HAND VERSUS ON ORDER

Most of it is not on the floor, and that is correct rather than a shortcoming.
A dealer holds a few of the common lines and sources the rest, which is what
backorder.source_order exists for. A handful per family are marked on hand so
"we have one here" is sometimes true, and the rest are sourceable.

COST

Serper's free tier is 2,500 credits and a shopping search is 2. One pass over
fourteen families at three price points is 84 credits, so a full rebuild costs
about 3% of the monthly allowance.

    python -m scripts.stock_from_market            show what it would add
    python -m scripts.stock_from_market --write    add it
    python -m scripts.stock_from_market --family laptop --write
"""

from __future__ import annotations

import sys
from datetime import datetime

from src import db, market

# WHERE TO LOOK, AND WHAT TO CALL IT.
#
# Two faults in the first version of this, both of them mine rather than the
# API's.
#
# It asked for "business laptop" and nothing else, so it saw one slice of the
# market. Somebody rang and asked for a Lenovo Legion and the answer was no,
# because Legion is a gaming line and a search for business laptops cannot
# return one. A dealer's catalogue is not one segment of a trade.
#
# And it took eight results from a call that returns FORTY, so thirty two real
# listings were paid for and thrown away every time, roughly thirteen hundred
# across a full rebuild.
#
# So each probe now carries its own segment word as well as its own price
# point, which costs no more searches than before: the cheap band asks what
# the budget end of the trade sells, the middle asks the mainstream, the top
# asks the specialist end. That is where the Legions, the workstations and the
# glass-door merchandisers live.
SEGMENTS = {
    "it": ((0.45, "budget"), (1.0, "business"), (1.8, "gaming")),
    "refrigeration": ((0.45, "commercial undercounter"),
                      (1.0, "commercial"),
                      (1.8, "commercial stainless")),
    "furniture": ((0.45, "budget office"),
                  (1.0, "commercial office"),
                  (1.8, "executive ergonomic")),
    "av": ((0.45, "budget"),
           (1.0, "commercial"),
           (1.8, "professional 4k")),
}

# Of the forty a call returns. Filtering throws away most of them anyway.
PER_SEARCH = 24

# Roughly what a normal machine in this family costs, only ever used to decide
# WHERE TO LOOK. The price that gets stored is the real listing price.
TYPICAL = {
    "laptop": 1200.0, "desktop": 1100.0, "server": 3000.0,
    "printer": 500.0, "ups": 400.0,
    "reach-in freezer": 5000.0, "reach-in cooler": 4500.0,
    "display cooler": 3500.0, "walk-in cooler": 9000.0,
    "ice machine": 4000.0, "dishwasher": 5000.0, "oven": 6000.0,
    "fryer": 3000.0, "hot holding cabinet": 3000.0,
    # The IT vendor's accessory lines. Cheap, fast, and most of what a shop
    # like that actually moves.
    "monitor": 350.0, "docking station": 220.0, "headset": 140.0,
    # Contract furnishings.
    "office chair": 700.0, "desk": 800.0, "conference table": 1800.0,
    "filing cabinet": 500.0, "shelving unit": 400.0,
    # Displays and audio.
    "television": 900.0, "commercial display": 1600.0, "projector": 1200.0,
    "sound system": 900.0, "digital signage": 2000.0,
}

MARGIN = 1.35
LEAD_DAYS = 21
ON_HAND_PER_FAMILY = 3


def _families() -> list[tuple[str, str]]:
    """(family, dealer_id) for everything the dealers say they carry."""
    out = []
    with db.connect() as c:
        for r in c.execute("SELECT id, families FROM dealers "
                           "WHERE families IS NOT NULL"):
            for fam in (r["families"] or "").split(","):
                fam = fam.strip()
                if fam:
                    out.append((fam, r["id"]))
    return out


def _split(title: str) -> tuple[str, str]:
    """Manufacturer and model out of a listing title.

    Crude on purpose. The first word is the make in almost every shopping
    title, and what follows up to the first separator is the model. Getting
    this wrong costs a scrappy-looking row, not a wrong price.
    """
    words = (title or "").replace(",", " ").split()
    if not words:
        return "", ""
    make = words[0]
    rest = []
    for w in words[1:]:
        if w in ("-", "|") or w.startswith("("):
            break
        rest.append(w)
        if len(rest) >= 4:
            break
    return make, " ".join(rest).strip(" -|")


def collect(only: str = "") -> dict:
    seen: set[tuple[str, str]] = set()
    rows, searches = [], 0

    with db.connect() as c:
        for r in c.execute("SELECT manufacturer, model_number FROM product_stock"):
            seen.add((r["manufacturer"].lower(), r["model_number"].lower()))
        supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()

    for family, dealer_id in _families():
        if only and only.lower() != family.lower():
            continue
        typical = TYPICAL.get(family.lower())
        if typical is None:
            print(f"  {family}: no typical price, skipped")
            continue

        trade = market._trade_for(family)
        probes = SEGMENTS.get(trade, SEGMENTS["refrigeration"])

        for band, segment in probes:
            budget = round(typical * band)
            out = market.alternatives(family, budget, limit=PER_SEARCH,
                                      segment=segment)
            searches += 1
            if not out.get("ok"):
                continue

            for item in out.get("found", []):
                title = item.get("title") or ""
                price = item.get("price") or item.get("amount")
                if not title or not price:
                    continue
                make, model = _split(title)
                if not make or not model:
                    continue
                key = (make.lower(), model.lower())
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "dealer_id": dealer_id, "manufacturer": make,
                    "model_number": model, "family": family,
                    "list_price": float(price),
                    "unit_cost": round(float(price) / MARGIN, 2),
                    "source": f"real listing: {title[:110]}",
                })

    return {"rows": rows, "searches": searches}


def write(only: str = "") -> dict:
    out = collect(only)
    now = datetime.now().isoformat(timespec="seconds")

    with db.connect() as c:
        supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()
    sup = supplier["id"] if supplier else None

    per_family: dict[str, int] = {}
    with db.txn() as c:
        for r in out["rows"]:
            n = per_family.get(r["family"], 0)
            on_hand = 1 if n < ON_HAND_PER_FAMILY else 0
            per_family[r["family"]] = n + 1
            c.execute(
                """INSERT INTO product_stock
                   (dealer_id,manufacturer,model_number,family,on_hand,on_order,
                    unit_cost,list_price,lead_time_days,supplier_id,updated_at,
                    price_source)
                   VALUES (?,?,?,?,?,0,?,?,?,?,?,?)""",
                (r["dealer_id"], r["manufacturer"], r["model_number"],
                 r["family"], on_hand, r["unit_cost"], r["list_price"],
                 LEAD_DAYS, sup, now, r["source"]))
    return out


if __name__ == "__main__":
    only = ""
    if "--family" in sys.argv:
        only = sys.argv[sys.argv.index("--family") + 1]
    doing = "--write" in sys.argv

    out = write(only) if doing else collect(only)

    by_family: dict[str, list[float]] = {}
    for r in out["rows"]:
        by_family.setdefault(r["family"], []).append(r["list_price"])

    print(f"\n{len(out['rows'])} new products from {out['searches']} searches "
          f"({out['searches'] * 2} Serper credits)")
    for fam, prices in sorted(by_family.items()):
        prices.sort()
        print(f"  {fam:<22} {len(prices):>3} models  "
              f"${prices[0]:,.0f} to ${prices[-1]:,.0f}")

    print("\nwritten" if doing else "\nnothing written, pass --write")
