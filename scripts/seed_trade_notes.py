"""What each trade knows that the others do not.

THE GAP THIS CLOSES, MEASURED

All four vendors received a byte-identical instruction. 25,544 characters,
the same for refrigeration, IT, furniture and displays. Counting the words in
the version a FURNITURE call receives:

    refrigerant 4, EPA 5, compressor 1, freezer 4, walk-in 3,
    NSF 2, R-290 1, gasket 1, cooler 3      = 24 mentions
    chair 1

So somebody buying an office chair was governed by rules about flammable
refrigerant and federal refrigerant-handling certification, and the desk had
nothing at all to say about the thing they were actually buying.

The tenancy was in the data and in the routing and absent from the only part
that decides how the desk talks.

WHY NOTES RATHER THAN AN AGENT PER COMPANY

A gatekeeper agent per vendor is the obvious shape and it is the wrong trade
here. This desk already runs four workers, and the production guidance is
blunt that an orchestrator's context overflows past about four; Princeton
found a single agent matched or beat multi-agent on 64% of benchmarked tasks
with the same tools, at roughly half the cost.

The knowledge needs to be per-vendor. The AGENT does not. So it goes where
`families` already goes: a column, read per call, appended to the instruction.
Adding a fifth business stays a row.

WHAT BELONGS IN HERE

Only what changes what the desk SAYS or ASKS. Not marketing, not a product
list, and nothing already enforced elsewhere: the EPA certification gate is
in cover.py and belongs there, because a rule a model can talk past is not a
rule.

    python -m scripts.seed_trade_notes
"""

from __future__ import annotations

from src import db

NOTES = {
    "refrigeration": """
WHAT MATTERS IN THIS TRADE
A machine that is not holding temperature is losing somebody's stock while
you talk. Ask what is in it and how long it has been warm before anything
else: a walk-in full of Friday's delivery is a different call from an empty
back-up freezer.
Ask whether the condenser looks blocked and when it was last cleaned. It is
the commonest cause of "it was fine last week", it is free to check, and it
is the difference between a callout and a sentence.
Ice on the back is a defrost fault, not a cooling fault, and they are
different jobs with different parts.
Never talk anybody through anything behind a panel. Refrigerant circuits are
sealed, they are pressurised, and some of them are propane.
""",
    "it": """
WHAT MATTERS IN THIS TRADE
Ask what is on it before anything else. A laptop that will not start is a
hardware call; a laptop that will not start WITH THE ONLY COPY OF THE ACCOUNTS
on it is a data call, and the order of operations is different.
Ask whether it is under a business warranty or a consumer one. The same
manufacturer runs both, the terms differ by years, and people almost never
know which they bought.
Battery, keyboard and screen are wear items on most business terms. Say so
before somebody expects a free replacement.
Never advise a factory reset, a reinstall or anything that clears a disk on a
machine whose data has not been discussed.
""",
    "furniture": """
WHAT MATTERS IN THIS TRADE
Ask how many hours a day it will be sat in, and by how many people. A chair
rated for a single shift fails in a call centre and the warranty will say so.
This is the single question that decides whether a recommendation is honest.
Warranty length here is not one number. Frames, mechanisms, fabric, castors
and gas lifts all carry different terms from the same maker, and fabric is
usually the shortest. Quote the part they asked about, not the headline.
Delivery is not the job. Assembly and installation are, especially for desks
and storage, so ask which they expect before quoting.
Weight ratings are real and are a safety matter rather than a preference.
""",
    "av": """
WHAT MATTERS IN THIS TRADE
Ask where it is going and how many hours a day it will run. A consumer
television's warranty EXCLUDES commercial and public display use, so a set
mounted in a dining room is uncovered from the day it goes up, and the
customer will not find that out until it fails.
Say that BEFORE they choose, not after. The commercial line costs more and is
the cheaper machine within a year of a failure.
The lamp in a projector is a consumable with a rated life in hours. It is
never covered for the projector's term and it is a scheduled cost, not a
fault.
Mounting and cabling are somebody's job. Ask whose before quoting.
""",
}


def load() -> dict:
    db.init()
    written = []
    with db.txn() as c:
        rows = c.execute("SELECT id, trade, name FROM dealers").fetchall()
        for r in rows:
            note = NOTES.get((r["trade"] or "").lower())
            if not note:
                continue
            c.execute("UPDATE dealers SET trade_notes=? WHERE id=?",
                      (note.strip(), r["id"]))
            written.append((r["id"], r["trade"], len(note.strip())))
    return {"written": written}


if __name__ == "__main__":
    out = load()
    for did, trade, n in out["written"]:
        print(f"  {did:<8} {trade:<14} {n} characters of its own trade knowledge")
