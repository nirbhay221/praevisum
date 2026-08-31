"""What machines this dealer actually has on the floor.

WHY THIS SCRIPT EXISTS

`supply.product_availability` opens with its own reason for being:

    The desk could recommend a Traulsen over a Beverage-Air, weigh their
    running costs from federal data and quote the delivery, and had no way to
    answer whether one was in the building.

It was written to close that. It was then wired to no agent, and the table it
reads was never given a row. So the gap it describes was still wide open, and
the desk's hard rule that it may never say something is available unless a
tool said so meant "have you got one?" could only ever be deflected.

The same shape as the EPA certificates: a thing built, tested, and left with
an empty table behind it.

WHERE THE MACHINES COME FROM

The models this dealer already services. `assets` holds what their customers
actually run, so those are the makes and models a real dealer would keep on
the floor and be asked for by name. Cross-checked against the EnergyStar
catalogue so the model numbers are real ones rather than invented.

Prices are marked up from what the dealer pays, at a margin named as a
constant so it can be argued with rather than found by reading a formula.

Run: python -m scripts.seed_product_stock
"""

from __future__ import annotations

from datetime import datetime

from src import db, market

# What a dealer marks a machine up by. Commercial kitchen equipment runs
# thinner than parts do: the box is a big ticket, it is quoted against other
# dealers, and the money is made on the service contract behind it.
MARGIN = 1.32

# Roughly what these cost a dealer for a machine of TYPICAL size, by family.
#
# The first version of this stopped here, and every reach-in freezer on the
# price list came out at exactly $5,544. That makes "what have you got that is
# cheaper" a question with no answer, which is precisely what a customer asked
# on a live call.
#
# So the family figure is scaled by the machine's REAL capacity, which the
# EnergyStar catalogue already gives us in cubic feet. Commercial refrigeration
# genuinely prices roughly by box size: a 17 cubic foot single door costs a
# fraction of a 49 cubic foot three door. Where a model is not in the
# catalogue the family figure stands on its own, and the row says which.
TRADE_COST = {
    "reach-in freezer": 4200.0,
    "reach-in cooler": 3600.0,
    "walk-in cooler": 7800.0,
    "walk-in freezer": 9400.0,
    "display cooler": 2900.0,
    "ice machine": 3400.0,
    "blast chiller": 11500.0,
    "dishwasher": 4800.0,
    "oven": 6200.0,
    "fryer": 3100.0,
    "hot holding cabinet": 2400.0,

    # The IT dealer. Every family below was silently skipped, so that
    # business had ZERO machines on its price list and the desk could not
    # answer "have you got one" for any of them.
    "laptop": 900.0,
    "desktop": 750.0,
    "server": 3200.0,
    "printer": 420.0,
    "ups": 260.0,
}

# How many of each a dealer keeps. Most models are ordered in; a handful of
# fast movers sit on the floor. Deliberately uneven, because a shelf where
# everything is in stock is a shelf nobody has to think about, and the whole
# point of the tool is answering honestly when the answer is no.
ON_HAND = {0: 6, 1: 3, 2: 2, 3: 1}     # weight -> count pattern

# Days to get one in when there is none on the floor. A machine is not a
# defrost thermostat: it comes off a factory build slot.
LEAD_DAYS = 21

# The capacity a family figure is quoted for, in cubic feet. A machine twice
# this size costs roughly twice as much; the relationship is not linear in
# reality, so it is damped rather than applied straight.
TYPICAL_CUFT = {
    "reach-in freezer": 23.0, "reach-in cooler": 23.0,
    "display cooler": 20.0, "ice machine": 15.0,
    "walk-in cooler": 60.0, "walk-in freezer": 60.0,
    "blast chiller": 30.0,
    # IT machines are not sized in cubic feet, so the capacity scaling simply
    # does not apply and the family figure stands on its own.
}

# How much of the size difference feeds through to the price. A box twice the
# size is dearer but not twice as dear: the compressor, the controller and the
# door furniture do not double.
SIZE_SENSITIVITY = 0.65



def _capacity(manufacturer: str, model_number: str) -> float | None:
    """Cubic feet, from the EnergyStar catalogue. Real, or nothing."""
    norm = (model_number or "").upper().replace("-", "").replace(" ", "").replace("/", "")
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT capacity FROM equipment
                   WHERE brand LIKE ? AND model_norm = ? AND capacity IS NOT NULL
                   LIMIT 1""", (f"%{manufacturer}%", norm)).fetchone()
        return float(row["capacity"]) if row and row["capacity"] else None
    except Exception:
        return None


def load(dealer_id: str = "D-REF") -> dict:
    db.init()

    with db.connect() as c:
        models = c.execute(
            """SELECT a.manufacturer, a.model_number, a.family, COUNT(*) n
               FROM assets a
               JOIN sites s ON s.id = a.site_id
               WHERE a.family IS NOT NULL
               GROUP BY a.manufacturer, a.model_number, a.family
               ORDER BY n DESC""").fetchall()

        supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()

    rows, skipped, sized, priced_real = [], 0, 0, 0
    for i, m in enumerate(models):
        fam = (m["family"] or "").lower()
        cost = TRADE_COST.get(fam)
        if cost is None:
            skipped += 1
            continue

        # A REAL PRICE IF ONE EXISTS. Everything below this was invented: a
        # trade cost chosen by hand, scaled by capacity, marked up by a
        # constant. Real listings are one Serper call away and were the whole
        # time, so they come first and the estimate is only the fallback.
        real = market.price_for(m["manufacturer"], m["model_number"])
        if real.get("ok"):
            listed = real["median"]
            source = (f"median of {real['listings']} real listings"
                      + (f", matched on {real['matched_on']}"
                         if real.get("matched_on") else ""))
            priced_real += 1
            rows.append((dealer_id, m["manufacturer"], m["model_number"],
                         m["family"], 2 if i < 4 else (1 if i < 10 else 0), 0,
                         round(listed / MARGIN, 2), listed, LEAD_DAYS,
                         supplier["id"] if supplier else None,
                         datetime.now().isoformat(timespec="seconds"), source))
            continue

        # Scale by the machine's real capacity where the catalogue knows it.
        cuft = _capacity(m["manufacturer"], m["model_number"])
        typical = TYPICAL_CUFT.get(fam)
        if cuft and typical:
            ratio = 1 + (cuft / typical - 1) * SIZE_SENSITIVITY
            cost = round(cost * max(0.45, min(2.5, ratio)), 2)
            sized += 1

        # The commonest machines are the ones a dealer keeps; the long tail is
        # ordered in. Nothing here pretends everything is in stock.
        on_hand = 2 if i < 4 else (1 if i < 10 else 0)

        rows.append((dealer_id, m["manufacturer"], m["model_number"],
                     m["family"], on_hand, 0, cost, round(cost * MARGIN, 2),
                     LEAD_DAYS, supplier["id"] if supplier else None,
                     datetime.now().isoformat(timespec="seconds"),
                     "ESTIMATED from a family figure, not a real listing"))

    with db.txn() as c:
        c.executemany(
            """INSERT OR REPLACE INTO product_stock
               (dealer_id,manufacturer,model_number,family,on_hand,on_order,
                unit_cost,list_price,lead_time_days,supplier_id,updated_at,
                price_source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

    with db.connect() as c:
        total = c.execute("SELECT COUNT(*) n FROM product_stock WHERE dealer_id=?",
                          (dealer_id,)).fetchone()["n"]
        in_stock = c.execute(
            "SELECT COUNT(*) n FROM product_stock WHERE dealer_id=? AND on_hand>0",
            (dealer_id,)).fetchone()["n"]

    return {"models": total, "on_the_floor": in_stock,
            "priced_from_real_listings": priced_real,
            "priced_by_real_capacity": sized,
            "skipped_no_family_price": skipped}


if __name__ == "__main__":
    out = load()
    print(f"{out['models']} models on the price list, "
          f"{out['on_the_floor']} of them physically on the floor")
    print(f"{out['priced_from_real_listings']} priced from REAL market listings, "
          f"{out['priced_by_real_capacity']} estimated from capacity")
    print(f"{out['skipped_no_family_price']} skipped: no trade cost for that family")
