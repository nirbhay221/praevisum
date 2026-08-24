"""Make the installed base look like a dealer's book instead of a catalogue dump.

    .venv/Scripts/python.exe scripts/consolidate_range.py

THE PROBLEM

The generator picked a random model out of the 88,544 certified machines for
every asset it created. The result:

    420 assets, 397 distinct models, 1.06 units per model
    376 models with exactly one unit

That is not a dealer. A dealer carries a range. They have relationships with a
few manufacturers, they install the same reach-in freezer forty times, and when
a customer asks "what should I buy" the answer comes from having put that exact
machine into forty kitchens and seen what happened.

With one unit per model there is nothing to see. `recommend_equipment` can
compute a fault rate, but every rate is over a sample of one, which is why it
was cheerfully calling a machine "recommended" on the strength of a single
install that had not broken yet. The ranking was not the bug. The bug was that
the evidence never existed.

WHAT THIS DOES

Picks a realistic range per dealer per equipment family, then moves the
existing assets onto it. Nothing else changes: same customers, same sites, same
faults, same dates, same repair narratives. Only which model the machine is.

Sales are not uniform either. A dealer has a bestseller they put everywhere and
a tail they sold twice, so the reassignment follows a decaying weight rather
than spreading evenly. Otherwise every model comes back with an identical count
and "which is most reliable" becomes a coin toss again.

The repair rows carry the make and model denormalised, so they are rewritten to
match their machine. A repair claiming a different model from the asset it
points at would poison exactly the retrieval this product runs on.
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

RNG = random.Random(20260821)

# How many models a dealer actually carries in one equipment family. Small on
# purpose: the whole point is having enough of each to have an opinion.
RANGE_PER_FAMILY = 4

# Weight of each model in the range, bestseller first. Roughly what a dealer's
# mix looks like: one machine they install constantly, a solid second, then a
# tail they sold a handful of times.
MIX = [0.45, 0.28, 0.17, 0.10]


def main() -> None:
    with db.connect() as c:
        assets = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, a.family,
                      s.account_id
               FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE a.retired_on IS NULL""").fetchall()

    # group by which business owns them and what kind of machine it is
    by_group: dict[tuple, list] = defaultdict(list)
    dealer_of: dict[str, str] = {}
    with db.connect() as c:
        for r in c.execute("SELECT id, dealer_id FROM accounts"):
            dealer_of[r["id"]] = r["dealer_id"]

    for a in assets:
        dealer = dealer_of.get(a["account_id"]) or "D-REF"
        by_group[(dealer, a["family"])].append(a)

    moves: list[tuple[str, str, str]] = []      # asset_id, manufacturer, model
    summary: list[tuple] = []

    for (dealer, family), group in sorted(by_group.items(), key=lambda kv: str(kv[0])):
        models = sorted({(a["manufacturer"], a["model_number"]) for a in group})
        if len(models) <= 1:
            continue

        # The range: whichever models already appear most often, so the choice
        # is anchored in what the generator already leaned towards rather than
        # being invented here.
        counts = defaultdict(int)
        for a in group:
            counts[(a["manufacturer"], a["model_number"])] += 1
        ranked = sorted(models, key=lambda m: (-counts[m], m))
        keep = ranked[:min(RANGE_PER_FAMILY, len(ranked))]

        weights = MIX[:len(keep)]
        total = sum(weights)
        weights = [w / total for w in weights]

        for a in group:
            pick = RNG.choices(keep, weights=weights, k=1)[0]
            if (a["manufacturer"], a["model_number"]) != pick:
                moves.append((a["id"], pick[0], pick[1]))

        summary.append((dealer, family, len(group), len(models), len(keep)))

    if not moves:
        print("  nothing to consolidate")
        return

    with db.txn() as c:
        for asset_id, mfr, model in moves:
            c.execute(
                "UPDATE assets SET manufacturer=?, model_number=? WHERE id=?",
                (mfr, model, asset_id))
            # The repair rows carry make and model of their own. Left alone
            # they would describe a machine that no longer exists at that site.
            c.execute(
                """UPDATE repairs SET manufacturer=?, model_number=?
                   WHERE asset_id=?""", (mfr, model, asset_id))

    print(f"  moved {len(moves)} machines onto their dealer's actual range\n")
    print(f"  {'dealer':<8}{'family':<22}{'units':>6}{'was':>6}{'now':>6}")
    for dealer, family, n, before, after in summary:
        print(f"  {dealer:<8}{family:<22}{n:>6}{before:>6}{after:>6}")

    _report()


def _report() -> None:
    with db.connect() as c:
        tot = c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        mods = c.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT manufacturer,model_number FROM assets)"
        ).fetchone()[0]
        print(f"\n  {tot} assets across {mods} models "
              f"({tot/max(mods,1):.1f} units per model)")

        print("\n  models we now have enough of to have an opinion:")
        rows = c.execute(
            """SELECT manufacturer, model_number, units FROM model_supplied
               WHERE units >= 4 ORDER BY units DESC LIMIT 10""").fetchall()
        if not rows:
            print("    none, which means the range is still too wide")
        for r in rows:
            print(f"    {r['units']:>3}  {r['manufacturer']} {r['model_number']}")

        # A repair must never claim a different machine from the asset it
        # points at, because retrieval reads the repair and the van is loaded
        # from the asset.
        bad = c.execute(
            """SELECT COUNT(*) FROM repairs r JOIN assets a ON a.id = r.asset_id
               WHERE r.manufacturer <> a.manufacturer
                  OR r.model_number <> a.model_number""").fetchone()[0]
        print(f"\n  repairs disagreeing with their machine: {bad}")


if __name__ == "__main__":
    main()
