"""Procedures a customer can do, each traceable to a source.

    .venv/Scripts/python.exe scripts/seed_remote_fixes.py

Every row here has a `source`. That is the point of the table: an unattended
agent telling somebody to go behind a live appliance has to be able to say
where the instruction came from, and a procedure with no provenance should not
exist.

Three kinds, in order of how much they can be trusted:


  RECALL     the federal remedy text, lifted from the CPSC data already loaded.
             Strongest, because it is a manufacturer's own published fix and it
             does not need our track record to be believed.

  OUR NOTES  what a technician actually wrote after resolving something that
             turned out not to need parts. Mined from the repair corpus rather
             than invented.

  GENERAL    common first-line checks. NOT taken from any manufacturer's
             manual: these were written for this seed, and they are labelled
             `general` rather than `manual` because calling them `manual`
             would be a citation to a document that does not exist.

             They are ordinary trade knowledge (a door held ajar warms a
             cabinet, a blocked condenser makes a healthy unit run flat out)
             and they are limited to things a person can do without opening a
             panel. A real deployment should replace these with the dealer's
             actual service documentation, at which point they become `manual`
             and carry a page reference.

Nothing in here asks anybody to remove a cover, touch wiring, or handle
refrigerant. If a fix needs tools it is marked, and the agent is instructed to
book the visit instead.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

# First-line checks that resolve a real share of calls without a van. Written
# for this seed, not copied from any manufacturer's documentation, which is why
# they are labelled `general`. None requires a tool or an open panel.
GENERAL_CHECKS = [
    ("reach-in freezer", "not holding temperature overnight",
     "Is the door actually closing, and is anything on a shelf holding it ajar?",
     "Clear the shelf so the door seats fully, then leave it shut for two "
     "hours and check the temperature again. A door held a few millimetres "
     "open overnight will warm a whole cabinet.",
     "general", "trade first-line check, not from a manual"),

    ("reach-in freezer", "frost building on the coil",
     "Has it been manually defrosted in the last three months?",
     "Switch it off and empty it, leave the door open until the ice has gone "
     "completely, then restart it. If frost returns within a fortnight it is "
     "not a defrost cycle problem and we should come out.",
     "general", "trade first-line check, not from a manual"),

    ("reach-in cooler", "compressor running constantly, never cycles off",
     "Is the grille at the front or back clear, and is there space behind it?",
     "Pull anything stacked away from the vents and give it at least six "
     "inches of clearance, then leave it four hours. A blocked condenser makes "
     "a healthy unit run flat out.",
     "general", "trade first-line check, not from a manual"),

    ("display cooler", "not cold enough, food spoiling on the top shelf",
     "Is it in direct sun or next to a hot appliance or a doorway?",
     "Move anything hot away from it, or shade it if it is in sun. A display "
     "cooler in direct sunlight can lose several degrees on the top shelf and "
     "nothing is faulty.",
     "general", "trade first-line check, not from a manual"),

    ("ice machine", "producing hollow, cloudy cubes",
     "When was the water filter last changed?",
     "Change the water filter and run off the first two batches of ice. "
     "Cloudy or hollow cubes are usually a scaled or exhausted filter rather "
     "than the machine.",
     "general", "trade first-line check, not from a manual"),

    ("laptop", "running incredibly slowly",
     "Has it been fully restarted, not just closed and reopened, in the last week?",
     "Shut it down completely, leave it a minute, and start it again. Closing "
     "the lid is not a restart and a machine left running for weeks will "
     "crawl.",
     "general", "trade first-line check, not from a manual"),

    ("printer", "pages are coming out smudged",
     "Is the paper the right weight, and has it been sitting somewhere damp?",
     "Load a fresh ream from a dry cupboard and print five pages. Damp paper "
     "smudges on any printer and it is not a fuser fault.",
     "general", "trade first-line check, not from a manual"),
]


def _nid() -> str:
    return f"RF-{uuid.uuid4().hex[:6].upper()}"


def main() -> None:
    db.init()

    with db.connect() as c:
        if c.execute("SELECT COUNT(*) FROM remote_fixes").fetchone()[0]:
            print("  remote fixes already seeded, leaving them alone")
            return
        dealers = {r["id"]: (r["families"] or "") for r in
                   c.execute("SELECT id, families FROM dealers")}

    rows = []

    # 1. general trade checks, written here rather than sourced
    for family, symptom, check, instruction, source, ref in GENERAL_CHECKS:
        for dealer, families in dealers.items():
            if family not in families:
                continue
            rows.append((_nid(), dealer, family, None, None, None, symptom,
                         check, instruction, source, ref, 0, None))

    # 2. federal recall remedies, which are the strongest source we hold and
    #    were until now only ever read out on the service path
    with db.connect() as c:
        recalls = c.execute(
            """SELECT recall_number, brands, remedy FROM recalls
               WHERE remedy IS NOT NULL AND LENGTH(remedy) > 40
                 AND brands IS NOT NULL LIMIT 40""").fetchall()
        owned = {r["manufacturer"].lower(): r["family"] for r in c.execute(
            "SELECT DISTINCT manufacturer, family FROM assets")}

    for r in recalls:
        brands = (r["brands"] or "").lower()
        make = next((m for m in owned if len(m) >= 4 and m in brands), None)
        if not make:
            continue
        for dealer, families in dealers.items():
            if owned[make] not in families:
                continue
            rows.append((_nid(), dealer, owned[make], None, None,
                         make.title(), "safety recall on this machine",
                         "Do you have the machine in front of you?",
                         r["remedy"][:400], "recall", r["recall_number"],
                         0, "This is a published safety remedy. Read it as "
                            "written and do not add to it."))

    with db.txn() as c:
        c.executemany(
            """INSERT INTO remote_fixes
               (id,dealer_id,family,product_type,defrost_type,manufacturer,
                symptom,check_first,instruction,source,source_ref,
                requires_tools,safety_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

    print(f"  {len(rows)} procedures seeded")
    with db.connect() as c:
        for r in c.execute(
                """SELECT source, COUNT(*) n, COUNT(DISTINCT dealer_id) d
                   FROM remote_fixes GROUP BY source"""):
            print(f"    {r['n']:>3} from {r['source']}  (across {r['d']} dealers)")


if __name__ == "__main__":
    main()
