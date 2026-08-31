"""Make ice machines sellable.

WHAT WAS WRONG

find_equipment has required `daily_kwh IS NOT NULL` since it was written. Of
585 certified ice machines in the catalogue, the number carrying daily_kwh is
zero. So every ice machine has been invisible to the only tool that recommends
equipment, for the whole life of the project, and the desk answered "nothing
in the catalogue matches" to anybody asking for one.

It was not a missing dataset. ENERGY STAR simply does not rate ice machines
the way it rates a freezer, because a freezer's job is to hold a box cold and
an ice machine's job is to produce a weight of ice. So they publish:

    harvest_rate_lbs_ice_day        287
    energy_use_kwh_100_lbs_ice      5.9
    potable_water_use_gal_100_lbs_ice  12.7

The loader parsed the columns the refrigerator datasets use and left these in
the raw JSON, where nothing reads them.

WHAT THIS COMPUTES, AND WHY IT IS HONEST

    daily_kwh = harvest_rate * energy_use_per_100_lbs / 100

That is not an estimate. It is the machine's own published consumption at its
own published output, which is the number a buyer is comparing when they ask
what it costs to run. It is stated at full duty, and a machine that is not
harvesting all day uses less, so it is a ceiling rather than a guess. The
alternative, leaving them out of the catalogue entirely, is worse.

Harvest rate goes into its own column because it is the sizing question. A
restaurant does not ask an ice machine's cubic feet. They ask how much ice it
makes in a day, and the rule of thumb in the trade is 1.5 to 2 lb per cover.

    python -m scripts.backfill_ice_machines
"""

from __future__ import annotations

import json

from src import db

ICE_DATASETS = ("Certified Commercial Ice Machines", "Commercial Ice Machines",
                "Ice Machines")


def load() -> dict:
    db.init()

    with db.connect() as c:
        have = {r[1].lower() for r in c.execute("PRAGMA table_info(equipment)")}

    with db.txn() as c:
        for col, decl in (("ice_lbs_day", "ice_lbs_day REAL"),
                          ("water_gal_100lbs", "water_gal_100lbs REAL")):
            if col not in have:
                c.execute(f"ALTER TABLE equipment ADD COLUMN {decl}")

    marks = ",".join("?" for _ in ICE_DATASETS)
    with db.connect() as c:
        rows = c.execute(
            f"SELECT id, raw FROM equipment WHERE dataset IN ({marks})",
            ICE_DATASETS).fetchall()

    done, skipped = 0, 0
    with db.txn() as c:
        for r in rows:
            try:
                d = json.loads(r["raw"] or "{}")
            except (ValueError, TypeError):
                skipped += 1
                continue

            harvest = _number(d.get("harvest_rate_lbs_ice_day"))
            per100 = _number(d.get("energy_use_kwh_100_lbs_ice"))
            water = _number(d.get("potable_water_use_gal_100_lbs_ice"))

            if not harvest or not per100:
                skipped += 1
                continue

            kwh = round(harvest * per100 / 100.0, 2)
            c.execute(
                """UPDATE equipment
                   SET daily_kwh = ?, ice_lbs_day = ?, water_gal_100lbs = ?
                   WHERE id = ?""",
                (kwh, harvest, water or None, r["id"]))
            done += 1

    with db.connect() as c:
        chk = c.execute(
            """SELECT COUNT(*) n, MIN(CAST(daily_kwh AS REAL)) lo,
                      MAX(CAST(daily_kwh AS REAL)) hi
               FROM equipment
               WHERE product_type LIKE '%Ice Making%'
                 AND daily_kwh IS NOT NULL""").fetchone()

    return {"updated": done, "skipped": skipped,
            "sellable_now": chk["n"], "kwh_low": chk["lo"], "kwh_high": chk["hi"]}


def _number(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    out = load()
    print(f"  computed daily kWh for {out['updated']} ice machines "
          f"({out['skipped']} had no published harvest or energy figure)")
    print(f"  ice machines now findable: {out['sellable_now']}, "
          f"running {out['kwh_low']} to {out['kwh_high']} kWh a day")
