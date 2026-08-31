"""Record that one named account has agreed to be rung.

WHY THIS IS A SCRIPT AND NOT SOMETHING THE DESK CAN DO

A consent record is the single most consequential row in this database. It is
what stands between the business and $500 to $1,500 a call, and it is a claim
about something a HUMAN did: somebody agreed, in some way, on some date. No
part of this system should be able to write one on its own, and nothing in
src/ does. `_consent()` only ever reads.

So this is deliberately a hand-run script that names one account, states how
the agreement was obtained, and refuses to guess at any of it.

WHAT `written` MEANS AND WHY IT MATTERS

The FCC treats an AI-generated voice as an artificial or prerecorded voice, and
a MARKETING call using one needs prior express WRITTEN consent. Oral consent
taken on a service call is real and is not enough for an offer, which is why
outreach.py checks the form and not merely the fact.

Recording `written` here is therefore a factual claim. Only pass it when there
is something written to point at, and put where it is in `evidence`.

A safety recall is not marketing and is queued regardless of any of this. That
distinction is carried in code, not here.

    python -m scripts.grant_consent A-NUL \\
        --via "account owner, testing their own number" \\
        --form written \\
        --evidence "owner asked for this number to be called, 2026-08-30"
"""

from __future__ import annotations

import argparse
from datetime import date

from src import db

# Narrower than the law allows, on purpose. Nobody wants a sales call at
# 08:01 and the difference is not worth defending.
DEFAULT_FROM = 9 * 60
DEFAULT_TO = 20 * 60
DEFAULT_CAP = 3


def grant(account_id: str, via: str, form: str, evidence: str,
          quiet_before: int = DEFAULT_FROM, quiet_after: int = DEFAULT_TO,
          max_per_days: int = DEFAULT_CAP) -> dict:
    form = (form or "").strip().lower()
    if form not in ("written", "oral"):
        return {"ok": False, "why": "form must be written or oral"}
    if not via.strip() or not evidence.strip():
        return {"ok": False,
                "why": "say how it was obtained and what the evidence is. A "
                       "consent row with no provenance is worse than none, "
                       "because it looks like proof"}

    with db.connect() as c:
        acct = c.execute("SELECT id, name FROM accounts WHERE id = ?",
                         (account_id,)).fetchone()
    if acct is None:
        return {"ok": False, "why": f"no account {account_id}"}

    with db.txn() as c:
        c.execute(
            """INSERT INTO outreach_consent
                 (account_id,granted,granted_on,granted_via,quiet_before,
                  quiet_after,max_per_days,consent_form,evidence_ref)
               VALUES (?,1,?,?,?,?,?,?,?)
               ON CONFLICT(account_id) DO UPDATE SET
                 granted=1, granted_on=excluded.granted_on,
                 granted_via=excluded.granted_via,
                 quiet_before=excluded.quiet_before,
                 quiet_after=excluded.quiet_after,
                 max_per_days=excluded.max_per_days,
                 consent_form=excluded.consent_form,
                 evidence_ref=excluded.evidence_ref,
                 revoked_on=NULL""",
            (account_id, date.today().isoformat(), via.strip(),
             quiet_before, quiet_after, max_per_days, form, evidence.strip()))

    return {"ok": True, "account": account_id, "name": acct["name"],
            "form": form,
            "hours": f"{quiet_before // 60:02d}:00 to {quiet_after // 60:02d}:00",
            "say": ("Marketing calls are now permitted to this account. A "
                    "request to stop overrides this at any time and is kept "
                    "for four years.")}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("account_id")
    p.add_argument("--via", required=True,
                   help="how the agreement was obtained, in plain words")
    p.add_argument("--form", default="oral", choices=("written", "oral"),
                   help="written is required for MARKETING calls")
    p.add_argument("--evidence", required=True,
                   help="what a person could go and look at to verify it")
    p.add_argument("--from-hour", type=int, default=DEFAULT_FROM // 60)
    p.add_argument("--to-hour", type=int, default=DEFAULT_TO // 60)
    args = p.parse_args()

    out = grant(args.account_id, args.via, args.form, args.evidence,
                args.from_hour * 60, args.to_hour * 60)
    if not out.get("ok"):
        print("  " + out["why"])
        raise SystemExit(1)

    print(f"  {out['account']} ({out['name']}) may be rung, "
          f"{out['form']} consent, {out['hours']} local")
    print(f"  {out['say']}")


if __name__ == "__main__":
    main()
