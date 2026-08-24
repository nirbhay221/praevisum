"""The dealers' own premises.

    .venv/Scripts/python.exe scripts/seed_branches.py

Both dealers already had exactly one location in the book, as a stock location
with no address and no coordinates:

    D-REF  LOC-WH     Moline warehouse
    D-IT   LOC-IT-WH  Davenport depot

Those stay exactly as they are. Each becomes a branch pointing at the same
stock location, so "is the part on that shelf" is still answered by the tables
that already answer it, and nothing is copied.

Then a second site each, because a dealer with one address cannot demonstrate
"which counter is nearest to you", and the whole reason for offering a counter
is that it might be closer than a van is free.

Real Quad Cities geography, matching where the customers already are.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

# label, address, lat, lon, phone, existing stock location, has counter
BRANCHES = {
    "D-REF": [
        ("B-REF-MOLINE", "Moline warehouse and trade counter",
         "2100 5th Ave, Moline IL", 41.5067, -90.5151,
         "+13095550140", "LOC-WH", 1),
        # A satellite with no counter. Deliberately: it proves the has_counter
        # flag does something, and a customer sent to a loading bay with no
        # front desk is worse off than one who was never offered.
        ("B-REF-SILVIS", "Silvis parts depot",
         "1400 1st Ave, Silvis IL", 41.5117, -90.4151,
         "+13095550141", None, 0),
    ],
    "D-IT": [
        ("B-IT-DAVENPORT", "Davenport depot and workshop",
         "220 E 2nd St, Davenport IA", 41.5236, -90.5776,
         "+15635550140", "LOC-IT-WH", 1),
        ("B-IT-BETTENDORF", "Bettendorf service counter",
         "3100 18th St, Bettendorf IA", 41.5245, -90.5151,
         "+15635550141", None, 1),
    ],
}


def main() -> None:
    db.init()

    with db.connect() as c:
        known = {r["id"] for r in c.execute("SELECT id FROM stock_locations")}
        existing = c.execute("SELECT COUNT(*) FROM branches").fetchone()[0]

    if existing:
        print(f"  {existing} branches already on file, leaving them alone")
        return

    rows = []
    for dealer, items in BRANCHES.items():
        for bid, label, address, lat, lon, phone, stock_loc, counter in items:
            if stock_loc and stock_loc not in known:
                # Never invent a link to a shelf that does not exist. Better a
                # branch with no stock behind it than a branch pointing at
                # nothing, which would quietly answer "in stock" as no.
                stock_loc = None
            rows.append((bid, dealer, label, address, lat, lon, phone,
                         stock_loc, counter))

    with db.txn() as c:
        c.executemany(
            """INSERT INTO branches
               (id,dealer_id,label,address,lat,lon,phone_e164,
                stock_location_id,has_counter)
               VALUES (?,?,?,?,?,?,?,?,?)""", rows)

    print(f"  {len(rows)} branches added, nothing existing changed")
    with db.connect() as c:
        for r in c.execute(
                """SELECT b.dealer_id, b.label, b.has_counter, b.address,
                          b.stock_location_id
                   FROM branches b ORDER BY b.dealer_id, b.label"""):
            counter = "counter" if r["has_counter"] else "no counter"
            shelf = r["stock_location_id"] or "no stock behind it"
            print(f"    {r['dealer_id']:<7}{r['label'][:38]:<40}{counter:<12}{shelf}")


if __name__ == "__main__":
    main()
