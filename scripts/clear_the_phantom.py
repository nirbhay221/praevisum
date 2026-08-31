"""Point a job at the machine it is really about, and delete the invented one.

WHAT HAPPENED

On a live call the desk could not resolve a freezer the customer had bought
that morning, so it registered a NEW machine with manufacturer "unknown" and
model "unknown", opened a work order against that, and quoted the customer
$240.85 to repair a machine that was still under our own cover.

register_asset now refuses "unknown" outright and hands back the machine they
already own when there is exactly one of that family. This clears up what the
old behaviour left behind: a blank asset with a real engineer booked against
it.

Deliberately moves the job rather than deleting it. Somebody is expecting an
engineer on Tuesday, and the fix for bad data is not to cancel their visit.

    python scripts/clear_the_phantom.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402


def main() -> None:
    with db.connect() as c:
        blanks = [dict(r) for r in c.execute(
            """SELECT a.id, a.family, s.account_id
               FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE LOWER(COALESCE(a.manufacturer, '')) IN ('', 'unknown')
                  OR LOWER(COALESCE(a.model_number, '')) IN ('', 'unknown')""")]

    if not blanks:
        print("  no invented machines on the book")
        return

    for b in blanks:
        with db.connect() as c:
            # The real one they own of the same kind. Only when there is
            # exactly one: moving a job onto the wrong machine is worse than
            # leaving it on a blank.
            same = [r["id"] for r in c.execute(
                """SELECT a.id FROM assets a JOIN sites s ON s.id = a.site_id
                   WHERE s.account_id = ? AND a.family = ? AND a.id != ?
                     AND LOWER(COALESCE(a.manufacturer,'')) NOT IN ('', 'unknown')""",
                (b["account_id"], b["family"], b["id"]))]
            jobs = [r["id"] for r in c.execute(
                "SELECT id FROM work_orders WHERE asset_id = ?", (b["id"],))]

        if len(same) != 1:
            print(f"    {b['id']}: {len(same)} real {b['family']}s on that "
                  f"account, leaving it alone")
            continue

        with db.txn() as c:
            c.execute("UPDATE work_orders SET asset_id = ? WHERE asset_id = ?",
                      (same[0], b["id"]))
            # RETIRED, NOT DELETED.
            #
            # Deleting it raises a foreign key failure, and that failure is
            # correct: quotes, visits and complaints already point at this row
            # and destroying their subject would take real history with it.
            # A retired asset is excluded from everything that matters -- it
            # cannot be scheduled, quoted or offered -- and the trail of what
            # the desk did on that call survives for anybody who has to
            # explain it later.
            c.execute("UPDATE assets SET retired_on = date('now'), "
                      "location_note = 'registered in error on a call' "
                      "WHERE id = ?", (b["id"],))
        print(f"    {b['id']} ({b['family']}) -> {same[0]}, "
              f"{len(jobs)} job(s) moved, blank retired")

    with db.connect() as c:
        left = c.execute(
            """SELECT COUNT(*) FROM assets
               WHERE LOWER(COALESCE(manufacturer,'')) IN ('', 'unknown')
                 AND retired_on IS NULL"""
        ).fetchone()[0]
    print(f"  invented machines still on the book: {left}")


if __name__ == "__main__":
    main()
