"""One live, public promotion per company, so the station break has something
to read on every line.

WHY IT WAS NEEDED

`promotions` held four rows and all four belonged to D-REF, two of them trade
accounts only. So the hold-music break could only ever speak to a refrigeration
caller, and a customer routed to furniture, IT or audio-visual heard music and
nothing else -- which reads as three of the four businesses having no offers at
all rather than as a data gap.

WHAT IT WILL NOT DO

Overwrite anything. A promotion is a commercial commitment somebody made, and
a seeding script that edits live offers is a script that changes what a
customer was told. It adds one per company that has no public one running, and
leaves every existing row alone.

    python scripts/seed_promotions.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

# Deliberately plain, and deliberately not a discount on everything. A real
# promotion names a thing and a number, because that is what a caller can act
# on and what the desk can be held to.
OFFERS = {
    "D-FURN": ("Free assembly and delivery on any desk or chair order "
               "over five hundred dollars"),
    "D-IT": ("A free three year Essential protection plan with any laptop "
             "over one thousand dollars"),
    "D-AV": ("Twenty percent off projector mounts and screens when bought "
             "with a projector"),
    "D-REF": ("Free first-year labour on planned maintenance"),
}


def main() -> None:
    today = date.today()
    ends = (today + timedelta(days=45)).isoformat()

    with db.connect() as c:
        dealers = [r["id"] for r in c.execute("SELECT id FROM dealers")]
        live = {r["dealer_id"] for r in c.execute(
            """SELECT DISTINCT dealer_id FROM promotions
               WHERE ends >= ? AND (terms IS NULL OR terms NOT LIKE '%trade%')""",
            (today.isoformat(),))}

    added = 0
    for dealer in dealers:
        if dealer in live:
            print(f"  {dealer}: already has a public offer running")
            continue
        headline = OFFERS.get(dealer)
        if not headline:
            print(f"  {dealer}: no offer written for this company, skipped")
            continue
        with db.txn() as c:
            c.execute(
                """INSERT INTO promotions (dealer_id, headline, starts, ends, terms)
                   VALUES (?,?,?,?,?)""",
                (dealer, headline, today.isoformat(), ends,
                 "One per customer. Cannot be combined with other offers."))
        added += 1
        print(f"  {dealer}: {headline[:58]} (until {ends})")

    print(f"  added {added}")

    from src.radio import line_for

    print("\n  what a caller on hold would hear:")
    for dealer in dealers:
        said = line_for(dealer)
        print(f"    {dealer:7} {said or '(nothing, and silence is correct)'}")


if __name__ == "__main__":
    main()
