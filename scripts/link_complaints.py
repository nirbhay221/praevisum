"""Make complaints precede the faults they warn about.

    .venv/Scripts/python.exe scripts/link_complaints.py

THE PROBLEM

`seed_complaints.py` shaped complaints by model reputation and then picked the
wording at random. Realistic to read, and statistically useless: complaints and
service calls came out uncorrelated.

    units  faults  complaints  model
       24      40           0  True Refrigeration TUC-27F
       20      34           0  Continental 1FEN
       21      36          13  Lenovo LOQ 15IPH11E

Any forecast built on that is fitting noise. Which matters, because the whole
argument for recording complaints is that they are a LEADING indicator: a
customer says "the door seal is going" weeks before anybody replaces a gasket.
If the data does not contain that lag, the feature cannot be shown to work and,
worse, might look like it works by accident.

WHAT THIS DOES

Rewrites a share of complaints as genuine early warnings. Each one is attached
to a machine that really did have that repair later, worded the way somebody
grumbles before a thing fully fails, and dated before the repair closed.

The rest are left as they are, because that is also true: most complaints never
become a service call. Somebody who thinks the shelves are flimsy is not
predicting anything. A forecast that assumed every complaint was a fault in
waiting would order parts for grumbles about noise.

Nothing invented: the symptom, the part and the machine all come from a repair
that is already in the book. Only the phrasing and the date are new.
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

RNG = random.Random(4417)

# How a customer describes a fault that is coming, rather than one that has
# arrived. The key to this whole idea: they ring about the finished fault, but
# they have been living with the warning for weeks.
EARLY_WARNING = {
    "door sweating": [
        "the door sweats every morning and we have to yank it open",
        "there is condensation round the frame that was not there last year",
    ],
    "not cold enough": [
        "the top shelf never seems as cold as the bottom",
        "we have started putting the milk lower down because the top is warm",
    ],
    "hollow, cloudy": [
        "the ice has been coming out cloudy lately",
        "cubes are not forming properly, they come out hollow",
    ],
    "tripping the breaker": [
        "it hesitates when it starts, sort of grunts before it kicks in",
        "the lights dim for a second every time it starts up",
    ],
    "frost building": [
        "there is a bit of frost building up at the back that we keep chipping off",
        "we are defrosting it by hand more often than we used to",
    ],
    "temperature swinging": [
        "the temperature wanders a few degrees during the day",
        "it does not hold as steady as it did when it was new",
    ],
    "loud rattling": [
        "it has started making a noise at the back, a sort of rattle",
        "there is a rattle when the fan comes on that is getting worse",
    ],
    "error code": [
        "the display flickers an error now and then, then clears itself",
        "it throws a code occasionally but carries on working",
    ],
    "screen has gone black": [
        "the screen flickers when you move the lid",
        "the display dims on and off, it comes back if you tap it",
    ],
    "keeps shutting itself off": [
        "it gets very hot underneath and the fan roars",
        "the battery does not last like it used to",
    ],
    "running incredibly slowly": [
        "it has been getting slower over the last few months",
        "it hangs for a while when you open anything big",
    ],
}

# How much of the complaint book becomes a real early warning. Not all of it:
# most grumbles never become a job, and a forecast that assumed otherwise
# would order parts every time somebody said a machine was loud.
PREDICTIVE_SHARE = 0.45


def phrasing(symptom: str) -> str | None:
    low = (symptom or "").lower()
    for key, options in EARLY_WARNING.items():
        if key in low:
            return RNG.choice(options)
    return None


def main() -> None:
    with db.connect() as c:
        repairs = c.execute(
            """SELECT r.id, r.dealer_id, r.asset_id, r.manufacturer,
                      r.model_number, r.family, r.reported_symptom,
                      r.parts_consumed, r.closed_on, s.account_id
               FROM repairs r
               JOIN assets a ON a.id = r.asset_id
               JOIN sites s ON s.id = a.site_id
               WHERE r.parts_consumed <> '' AND r.closed_on IS NOT NULL""").fetchall()
        complaints = c.execute("SELECT id FROM complaints").fetchall()

    if not complaints:
        print("  no complaints to link. Run seed_complaints.py first")
        return

    usable = [r for r in repairs if phrasing(r["reported_symptom"])]
    if not usable:
        print("  no repairs with a symptom we know how to foreshadow")
        return

    want = int(len(complaints) * PREDICTIVE_SHARE)
    chosen = RNG.sample(complaints, min(want, len(complaints)))
    RNG.shuffle(usable)

    linked = 0
    with db.txn() as c:
        for row, repair in zip(chosen, usable):
            said = phrasing(repair["reported_symptom"])
            closed = date.fromisoformat(repair["closed_on"][:10])
            # Weeks earlier, not days. The point of the whole exercise is the
            # gap between somebody noticing and somebody ringing.
            raised = closed - timedelta(days=RNG.randint(14, 75))

            c.execute(
                """UPDATE complaints
                   SET dealer_id=?, account_id=?, asset_id=?, manufacturer=?,
                       model_number=?, family=?, what=?, category='reliability',
                       severity=?, raised_at=?, predicted_repair=?
                   WHERE id=?""",
                (repair["dealer_id"], repair["account_id"], repair["asset_id"],
                 repair["manufacturer"], repair["model_number"],
                 repair["family"], said,
                 RNG.choice(["minor", "minor", "major"]),
                 raised.isoformat(), repair["id"], row["id"]))
            linked += 1

    print(f"  {linked} of {len(complaints)} complaints rewritten as early warnings")
    _report()


def _report() -> None:
    with db.connect() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM complaints WHERE predicted_repair IS NOT NULL"
        ).fetchone()[0]
        lag = c.execute(
            """SELECT AVG(JULIANDAY(r.closed_on) - JULIANDAY(cm.raised_at)) d
               FROM complaints cm JOIN repairs r ON r.id = cm.predicted_repair"""
        ).fetchone()["d"]
        print(f"  {n} are now genuine early warnings, "
              f"raised on average {lag:.0f} days before the repair closed")

        print("\n  do complaints now track faults?")
        print(f"  {'units':>5}{'faults':>7}{'compl':>7}  model")
        for r in c.execute(
                """SELECT s.manufacturer, s.model_number, s.units,
                          COALESCE(g.complaints,0) comp,
                          (SELECT COUNT(*) FROM repairs r
                           WHERE r.manufacturer=s.manufacturer
                             AND r.model_number=s.model_number) faults
                   FROM model_supplied s
                   LEFT JOIN model_complaints g
                          ON g.manufacturer=s.manufacturer
                         AND g.model_number=s.model_number
                   WHERE s.units>=4 ORDER BY s.units DESC LIMIT 8"""):
            print(f"  {r['units']:>5}{r['faults']:>7}{r['comp']:>7}  "
                  f"{r['manufacturer']} {r['model_number'][:24]}")


if __name__ == "__main__":
    main()
