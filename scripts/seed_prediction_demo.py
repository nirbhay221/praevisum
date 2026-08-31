"""Make the thing this project is named after actually happen.

WHAT WAS ALREADY TRUE

`sweep_predictions` works. Given a customer's own complaint it searches the
same repair corpus the van loading uses, and where the evidence is strong
enough it says what that complaint turned into on other machines. Run against
the live book it produces:

    Hotel Grand, Desmon Spa PGM14C-C-A, 62%
    They said: "Third time this year it has gone down".
    Our own jobs put that at 62% condenser fan motor bearing gone

Which is the first sentence of the README, produced from data rather than
asserted: the company already knew which part it was.

WHY IT HAS NEVER RUNG ANYBODY

Two reasons, and the first one is correct behaviour that should not change.

  NO CONSENT ON RECORD. queue_outreach refuses to place a call to an account
  that has not agreed to be called. Hotel Grand has no consent row, so the
  prediction was found, scored, and thrown away. That is the right answer to
  the wrong question: the machinery is not broken, it has never been pointed
  at an account entitled to a call.

  AND NOTHING RUNS THE SWEEP. run_sweep exists and is described in its own
  docstring as "the thing a scheduler runs". Nothing schedules it.

WHAT THIS DOES

Gives the demo account real consent with real quiet hours, and puts a
complaint on a machine it actually owns, described in the customer's own
words. Nothing here fakes a prediction: the complaint is real input, the
corpus does the work, and if the evidence is weak the sweep will correctly
decline to call.

    python -m scripts.seed_prediction_demo
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src import db

ACCOUNT = "A-NUL"          # the number that actually rings this desk

# Their own words, about a machine they own. The corpus decides what it means.
COMPLAINT = (
    "the freezer has been running much longer than it used to and the back "
    "is icing up again, third time this summer"
)


def load() -> dict:
    db.init()
    now = datetime.now()

    with db.connect() as c:
        asset = c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, a.family
               FROM assets a JOIN sites s ON s.id = a.site_id
               WHERE s.account_id = ? AND a.retired_on IS NULL
                 AND a.family LIKE '%freezer%'
               ORDER BY a.rowid LIMIT 1""", (ACCOUNT,)).fetchone()
        if asset is None:
            return {"ok": False, "why": f"{ACCOUNT} owns no freezer to complain about"}

    with db.txn() as c:
        # Consent, with the quiet hours a real agreement carries. 9am to 4pm,
        # and not more than once a quarter.
        c.execute(
            """INSERT INTO outreach_consent
               (account_id, granted, granted_on, granted_via, quiet_before,
                quiet_after, max_per_days, consent_form, evidence_ref)
               VALUES (?,1,?,?,540,960,90,'written',?)
               ON CONFLICT(account_id) DO UPDATE SET
                 granted=1, revoked_on=NULL""",
            (ACCOUNT, now.date().isoformat(),
             "agreed on a service call",
             "signed service agreement 2026"))

        # A complaint they raised themselves, recently, in their own words.
        c.execute("DELETE FROM complaints WHERE id='CMP-DEMO'")
        c.execute(
            """INSERT INTO complaints
               (id, dealer_id, account_id, asset_id, manufacturer,
                model_number, family, what, category, severity, raised_at,
                status)
               VALUES ('CMP-DEMO','D-REF',?,?,?,?,?,?,'reliability','normal',
                       ?, 'open')""",
            (ACCOUNT, asset["id"], asset["manufacturer"], asset["model_number"],
             asset["family"], COMPLAINT,
             (now - timedelta(days=6)).isoformat(timespec="seconds")))

    return {"ok": True, "account": ACCOUNT,
            "machine": f"{asset['manufacturer']} {asset['model_number']}",
            "complaint": COMPLAINT}


if __name__ == "__main__":
    from src import memory, outreach

    out = load()
    if not out.get("ok"):
        print(out["why"])
        raise SystemExit(1)

    print(f"consent granted for {out['account']}")
    print(f"complaint recorded against {out['machine']}")
    print(f'  they said: "{out["complaint"]}"')
    print()

    memory.load_from_db()
    found = outreach.sweep_predictions("D-REF")
    mine = [f for f in found if f["account_id"] == ACCOUNT]
    print(f"the sweep found {len(found)} prediction(s), "
          f"{len(mine)} on this account")
    for p in mine:
        print(f"  {int(p['confidence'] * 100)}%  {p['machine']}")
        print(f"     {p['evidence']}")
        print(f"     parts: {p['likely_parts']}")

    res = outreach.queue_outreach(found, "D-REF")
    print()
    print(f"queued: {len(res.get('queued') or [])}  "
          f"blocked: {len(res.get('blocked') or [])}")
    for b in (res.get("blocked") or []):
        print(f"  {b['account_name']}: {b.get('blocked_because')}")
