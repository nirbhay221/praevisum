"""Bring a database up to date. Safe to run twice, safe to run on the live one.

    .venv/Scripts/python.exe scripts/migrate.py

Everything here is a data fix that cannot live in a schema file, because it is
about rows rather than tables. It exists because the fixes below were first
applied by hand to one database, which meant the VM and the laptop quietly
disagreed about what fits what.

WHAT IT FIXES

  Fitments that cross equipment families. The seed wrote one fitment row per
  part per asset, so an uninterruptible power supply was offered an LCD panel,
  a laptop battery and a keyboard. The fitment join decides what goes in the
  van, so wrong rows there are a technician driving an hour with the wrong box.

  A first pass at this mapped most parts to the families they belong on and
  pruned the rest, but left three parts unmapped. An unmapped part is treated
  as fitting anything, so the prune silently skipped them: a UPS battery
  cartridge was still offered for a laptop, and a compressor start capacitor
  for an oven and a fryer. That is the same bug the prune was written to fix,
  surviving in the rows the prune could not see.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

# Which equipment families each part legitimately goes on. A part left out of
# this map fits anything, which is almost never true, so leaving one out is
# how the bad rows survive.
FAMILIES = {
    # refrigeration
    "P-CONDFAN": "reach-in freezer,reach-in cooler,display cooler,"
                 "walk-in cooler,ice machine",
    "P-CONTROLBOA": "reach-in freezer,reach-in cooler,display cooler,"
                    "walk-in cooler,ice machine,dishwasher,oven",
    "P-DEFROSTHEA": "reach-in freezer,walk-in cooler",
    "P-DEFROSTTHE": "reach-in freezer,reach-in cooler,display cooler,"
                    "walk-in cooler",
    "P-DOORGASKET": "reach-in freezer,reach-in cooler,display cooler,"
                    "walk-in cooler",
    "P-EVAPFAN": "reach-in freezer,reach-in cooler,display cooler,"
                 "walk-in cooler,ice machine",
    "P-MULLIONHAR": "reach-in freezer,reach-in cooler,display cooler",
    "P-WATERVALVE": "ice machine,dishwasher",
    "REF-COMPRESSOR": "reach-in freezer,reach-in cooler,walk-in cooler",

    # Anything with a compressor to start. Not the dishwasher, the oven or the
    # fryer, which is what it was being offered for.
    "P-STARTCAPAC": "reach-in freezer,reach-in cooler,display cooler,"
                    "walk-in cooler,ice machine",

    # IT
    "IT-BATTERY": "laptop",
    "IT-FUSER": "printer",
    "IT-KEYBOARD": "laptop",
    "IT-LCDPANEL": "laptop",
    "IT-MAINBOARD": "laptop,desktop,server",
    "IT-RAM": "laptop,desktop,server",
    "IT-SSD": "laptop,desktop,server",
    "IT-WIFICARD": "laptop",

    # A fan and heatsink assembly is a laptop and desktop part. A UPS battery
    # cartridge goes in a UPS and nowhere else. Both were unmapped, so both
    # were being offered for every machine either dealer touches.
    "IT-FANASSEMBL": "laptop,desktop",
    "IT-UPSBATTERY": "ups",
}

PRUNE = """
DELETE FROM fitments WHERE rowid IN (
    SELECT f.rowid FROM fitments f
    JOIN parts p ON p.sku = f.sku
    WHERE p.families IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM assets a
        WHERE a.manufacturer = f.manufacturer
          AND a.model_number LIKE f.model_pattern
          AND (',' || p.families || ',') LIKE ('%,' || a.family || ',%')))
"""


def main() -> None:
    db.init()          # adds parts.families if this database predates it

    with db.connect() as c:
        before = c.execute("SELECT COUNT(*) FROM fitments").fetchone()[0]
        known = {r["sku"] for r in c.execute("SELECT sku FROM parts")}

    unknown = set(FAMILIES) - known
    unmapped = known - set(FAMILIES)

    with db.txn() as c:
        for sku, fams in FAMILIES.items():
            c.execute("UPDATE parts SET families=? WHERE sku=?", (fams, sku))
        removed = c.execute(PRUNE).rowcount

    with db.connect() as c:
        after = c.execute("SELECT COUNT(*) FROM fitments").fetchone()[0]

    print(f"  families set on {len(known & set(FAMILIES))} parts")
    print(f"  fitments {before} -> {after} ({removed} crossing rows removed)")
    if unknown:
        print(f"  in the map but not in this database: {sorted(unknown)}")
    if unmapped:
        # The condition that let the original bug survive. Worth shouting
        # about, because an unmapped part is an unrestricted part.
        print(f"  WARNING unmapped parts, these fit anything: {sorted(unmapped)}")

    _report()


def _report() -> None:
    """What each kind of machine is now offered. The check that matters."""
    with db.connect() as c:
        rows = c.execute(
            """SELECT a.family,
                      (SELECT GROUP_CONCAT(DISTINCT p.name)
                       FROM parts p JOIN fitments f ON f.sku = p.sku
                       WHERE f.manufacturer = a.manufacturer
                         AND a.model_number LIKE f.model_pattern) parts
               FROM assets a
               GROUP BY a.family ORDER BY a.family""").fetchall()

    print("\n  what each machine type is offered now:")
    for r in rows:
        parts = (r["parts"] or "nothing").split(",")
        shown = ", ".join(sorted(parts)[:4])
        more = f" (+{len(parts) - 4})" if len(parts) > 4 else ""
        print(f"    {r['family']:<22} {shown}{more}")


if __name__ == "__main__":
    main()
