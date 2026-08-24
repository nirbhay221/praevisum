"""Give the buying advice something to be built on.

    .venv/Scripts/python.exe scripts/seed_complaints.py

Complaints are the half of the evidence that never generates a van. A machine
that breaks gets a service call and lands in the repair corpus. A machine that
is merely deafening, or eats filters, or has a door seal that perishes in a
year, gets mentioned on a phone call and, until the complaints table existed,
vanished. Those sentences are the ones somebody about to spend four thousand
dollars actually wants to hear.

Synthetic, like the rest of the book, and shaped rather than sprinkled. Real
dealers know that two or three models in their range are the ones customers
grumble about, a couple are the ones nobody ever rings about, and the rest sit
in between. Spreading complaints evenly would produce a ranking that cannot
tell any model from any other, which is the state the recommendation engine was
already in and the reason this exists.

Every complaint is attached to a real customer who really owns that machine, so
the counts and the denominators agree with each other.
"""

from __future__ import annotations

import random
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

RNG = random.Random(8021)

# What customers actually grumble about, in the register they use on the phone.
# Keyed by category so the ranking can report what a model is complained about
# rather than only how often.
GRIPES = {
    "noise": [
        ("You can hear it through the wall in the dining room", "minor"),
        ("It is deafening when the compressor kicks in", "major"),
        ("The fan rattles constantly, staff have started unplugging it", "major"),
    ],
    "parts_cost": [
        ("Every part for it costs a fortune", "minor"),
        ("Quoted nearly four hundred for a control board, that is absurd", "major"),
        ("Cheaper to replace the whole unit than fix it", "major"),
    ],
    "design": [
        ("The door seal perished inside a year", "major"),
        ("Shelves are flimsy, two have already bent", "minor"),
        ("You cannot get to the condenser without pulling the whole thing out",
         "major"),
        ("Drain line is in a stupid place and blocks constantly", "major"),
    ],
    "running_cost": [
        ("Our electricity bill went up noticeably after we put it in", "minor"),
        ("It runs constantly in summer and never catches up", "major"),
    ],
    "reliability": [
        ("Third time this year it has gone down", "unusable"),
        ("We have lost stock twice because of it", "unusable"),
        ("It has never really held temperature properly since day one",
         "unusable"),
    ],
    "support": [
        ("Manufacturer warranty line is useless, took three weeks", "major"),
        ("Nobody could tell us which filter it takes", "minor"),
    ],
    "install": [
        ("Took two visits to get it level and running", "minor"),
        ("Arrived with a dented panel and it took a month to sort", "major"),
    ],
}

# How many complaints per machine in service, by how the model is regarded.
# A "grumbled about" model does not break more often, it just annoys people,
# which is exactly the signal service calls cannot see.
PROFILES = {
    "grumbled": 0.55,
    "ordinary": 0.12,
    "solid": 0.02,
}


def main() -> None:
    with db.connect() as c:
        existing = c.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
        if existing:
            print(f"  {existing} complaints already present, leaving them alone")
            return

        rows = c.execute(
            """SELECT a.id asset_id, a.manufacturer, a.model_number, a.family,
                      s.account_id, ac.dealer_id
               FROM assets a
               JOIN sites s ON s.id = a.site_id
               JOIN accounts ac ON ac.id = s.account_id
               WHERE a.retired_on IS NULL""").fetchall()

    by_model: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_model[(r["dealer_id"], r["manufacturer"], r["model_number"])].append(r)

    # Only models we have enough of to say anything about are worth shaping.
    # Assigning a reputation to a model we sold twice would recreate the
    # sample-of-one problem in a new table.
    big = sorted([k for k, v in by_model.items() if len(v) >= 4],
                 key=lambda k: (-len(by_model[k]), str(k)))

    reputation: dict[tuple, str] = {}
    for i, key in enumerate(big):
        if i % 5 == 0:
            reputation[key] = "grumbled"
        elif i % 5 in (1, 2):
            reputation[key] = "solid"
        else:
            reputation[key] = "ordinary"

    made = []
    today = datetime.now()
    for key, owners in by_model.items():
        profile = reputation.get(key, "ordinary")
        rate = PROFILES[profile]
        for owner in owners:
            if RNG.random() > rate:
                continue
            category = RNG.choice(list(GRIPES))
            what, severity = RNG.choice(GRIPES[category])
            made.append((
                f"CMP-{uuid.uuid4().hex[:6].upper()}",
                owner["dealer_id"], owner["account_id"], owner["asset_id"],
                owner["manufacturer"], owner["model_number"], owner["family"],
                what, category, severity,
                (today - timedelta(days=RNG.randint(5, 500)))
                .isoformat(timespec="seconds"),
            ))

    with db.txn() as c:
        c.executemany(
            """INSERT INTO complaints
               (id,dealer_id,account_id,asset_id,manufacturer,model_number,
                family,what,category,severity,raised_at,status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'open')""", made)

    print(f"  {len(made)} complaints raised across "
          f"{len({(m[4], m[5]) for m in made})} models")

    with db.connect() as c:
        print("\n  most complained about, with the denominator:")
        for r in c.execute(
                """SELECT g.manufacturer, g.model_number, g.complaints,
                          s.units, g.categories
                   FROM model_complaints g
                   JOIN model_supplied s ON s.manufacturer = g.manufacturer
                                        AND s.model_number = g.model_number
                   ORDER BY CAST(g.complaints AS REAL) / s.units DESC
                   LIMIT 5"""):
            print(f"    {r['complaints']:>2} of {r['units']:>2}  "
                  f"{r['manufacturer']} {r['model_number'][:26]}")
            print(f"            {r['categories']}")


if __name__ == "__main__":
    main()
