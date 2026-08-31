"""Complaints that describe danger, because the generated ones never do.

WHY THIS IS NEEDED AND WHAT IT IS NOT

hazard.py reads complaints for danger and fires when the same model draws
dangerous reports from more than one customer. Run against the seeded book it
finds nothing, and the reason is worth stating plainly rather than working
around quietly:

    45 nuisance, 8 degraded, 0 unsafe, 0 dangerous

Every generated complaint is about cost, noise or performance. "Cheaper to
replace the whole unit than fix it." "The fan rattles constantly." "Cubes come
out hollow." Those are real grumbles and none of them is a hazard, so the
detector correctly finds no hazards. The feature is not broken; the corpus has
nothing in it to detect.

Real dealers do receive the other kind. Somebody rings to say it smells hot,
or the breaker went again, or there was a spark when they plugged it in. The
CPSC recall records already in this database exist because enough people made
exactly those calls to somebody.

So this writes that kind of complaint, from separate customers, on a model
that many of them own. Everything else stays real: the classifier is not
told the answer, the aggregation is not told the answer, and if the words
below did not read as dangerous the sweep would decline to call anybody.

THE MODEL WAS CHOSEN, NOT INVENTED

    Beverage-Air HR1HC***G********   display cooler
    18 customers own one
    the certified catalogue says it runs R-290, which is propane

That last fact is the one that matters. The desk already refuses to send a
technician without the certification to open a flammable circuit. Until now
it has never used the same fact to warn the person standing next to it.

    python -m scripts.seed_hazard_reports
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src import db

MAKE = "Beverage-Air"
MODEL = "HR1HC***G********"

# What people actually say when a machine is frightening them. Deliberately
# in their own register, not a technician's: nobody rings up and reports a
# "thermal event".
REPORTS = [
    "there was a burning smell yesterday and the back panel is hot to touch",
    "it sparked when the chef plugged it back in and the breaker went",
    "we can smell gas near it and there is a hissing noise from the back",
]

# One more that is NOT dangerous, on the same model, so the classifier has
# something to correctly leave alone.
BENIGN = "the light inside has stopped working"


def load() -> dict:
    db.init()
    now = datetime.now()

    with db.connect() as c:
        owners = c.execute(
            """SELECT a.id asset_id, s.account_id, ac.name account
               FROM assets a
               JOIN sites s ON s.id = a.site_id
               JOIN accounts ac ON ac.id = s.account_id
               WHERE a.manufacturer = ? AND a.model_number = ?
                 AND a.retired_on IS NULL
               ORDER BY ac.name""", (MAKE, MODEL)).fetchall()

    if len(owners) < len(REPORTS) + 1:
        return {"ok": False,
                "why": f"only {len(owners)} customers own a {MAKE} {MODEL}"}

    written = []
    with db.txn() as c:
        c.execute("DELETE FROM complaints WHERE id LIKE 'CMP-HAZ%'")

        for i, (report, owner) in enumerate(zip(REPORTS, owners), 1):
            c.execute(
                """INSERT INTO complaints
                   (id, dealer_id, account_id, asset_id, manufacturer,
                    model_number, family, what, category, severity,
                    raised_at, status)
                   VALUES (?,'D-REF',?,?,?,?,'display cooler',?,
                           'reliability','major',?,'open')""",
                (f"CMP-HAZ{i}", owner["account_id"], owner["asset_id"],
                 MAKE, MODEL, report,
                 (now - timedelta(days=i * 2)).isoformat(timespec="seconds")))
            written.append((owner["account"], report))

        benign_owner = owners[len(REPORTS)]
        c.execute(
            """INSERT INTO complaints
               (id, dealer_id, account_id, asset_id, manufacturer,
                model_number, family, what, category, severity,
                raised_at, status)
               VALUES ('CMP-HAZ0','D-REF',?,?,?,?,'display cooler',?,
                       'reliability','minor',?,'open')""",
            (benign_owner["account_id"], benign_owner["asset_id"],
             MAKE, MODEL, BENIGN,
             (now - timedelta(days=1)).isoformat(timespec="seconds")))

    return {"ok": True, "model": f"{MAKE} {MODEL}",
            "written": written, "owners": len(owners),
            "benign": (benign_owner["account"], BENIGN)}


if __name__ == "__main__":
    from src.hazard import classify, stop_using_it, sweep_hazards

    out = load()
    if not out.get("ok"):
        print(out["why"])
        raise SystemExit(1)

    print(f"{out['model']}, owned by {out['owners']} customers\n")
    print("complaints written, and how the classifier reads them "
          "without being told:")
    for account, report in out["written"]:
        v = classify(report, MAKE, MODEL)
        raised = " (raised: flammable charge)" if v["raised_for_refrigerant"] else ""
        print(f"  {v['level']:<10}{raised:<30} {account}")
        print(f"      \"{report}\"")
    account, report = out["benign"]
    v = classify(report, MAKE, MODEL)
    print(f"  {v['level']:<10}{'':<30} {account}")
    print(f"      \"{report}\"")

    print()
    swept = sweep_hazards("D-REF")
    print(f"patterns found: {len(swept['patterns'])}")
    for p in swept["patterns"]:
        print(f"\n  {p['manufacturer']} {p['model_number']}")
        print(f"    {p['dangerous_reports']} dangerous reports across "
              f"{p['households']} sites, {len(p['owners'])} customers own one")
        print()
        print(stop_using_it(p)["say"])
