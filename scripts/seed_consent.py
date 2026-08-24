"""Who has agreed to be rung.

    .venv/Scripts/python.exe scripts/seed_consent.py

Synthetic, and shaped rather than uniform. Real opt-in for outbound contact
runs well under half, and account customers agree more readily than one-off
residential callers because they already have a relationship with the dealer.

Nobody is opted in by default. That is the whole point of the table: absence
of a record is absence of consent, and this script writes records rather than
flipping a global switch.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

RNG = random.Random(90210)

# Opt-in rates. Trade accounts say yes more often; they already take our calls.
RATE = {"business": 0.45, "person": 0.20}


def main() -> None:
    db.init()
    with db.connect() as c:
        existing = c.execute("SELECT COUNT(*) FROM outreach_consent").fetchone()[0]
        if existing:
            print(f"  {existing} consent records already on file, leaving them")
            return
        accounts = c.execute(
            "SELECT id, kind, opened_on FROM accounts").fetchall()

    rows = []
    for a in accounts:
        if RNG.random() > RATE.get(a["kind"], 0.3):
            continue
        rows.append((
            a["id"], 1, a["opened_on"] or "2025-01-01",
            "agreed on a service call",
            # A kitchen is busy at lunch. A few customers say mornings only.
            540 if RNG.random() > 0.25 else 600,
            1020 if RNG.random() > 0.25 else 960,
            30 if RNG.random() > 0.2 else 90,
        ))

    with db.txn() as c:
        c.executemany(
            """INSERT INTO outreach_consent
               (account_id,granted,granted_on,granted_via,
                quiet_before,quiet_after,max_per_days)
               VALUES (?,?,?,?,?,?,?)""", rows)

    print(f"  {len(rows)} of {len(accounts)} accounts have agreed to be rung "
          f"({len(rows)/max(len(accounts),1):.0%})")
    print(f"  the other {len(accounts)-len(rows)} have no record, "
          f"which means no marketing call ever")
if __name__ == "__main__":
    main()
