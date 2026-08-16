"""Proof that the system learns. No credentials, no phone, no cloud.

Three acts:

  1. A fault nobody has seen before comes in. The briefing has nothing useful
     to say about it, and says so.
  2. The technician fixes it and writes down what it actually was.
  3. A DIFFERENT site, a DIFFERENT unit of the same model, and a caller who
     describes the fault in completely different words - and the briefing now
     knows what to send.

Act 3 is the whole argument. The caller says "sweating and freezing shut".
The technician wrote "mullion heater harness open". Nothing matches on words.

    .venv/Scripts/python.exe scripts/loop.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.memory import INDEX  # noqa: E402
from src.tools import (  # noqa: E402
    build_briefing,
    close_work_order,
    find_technician,
    identify_caller,
    open_work_order,
    prior_repairs,
    promise_slot,
)

SEP = "=" * 74


def act(n: int, title: str) -> None:
    print(f"\n{SEP}\nACT {n}. {title}\n{SEP}")


# ==========================================================================
act(1, "A fault nobody has seen before")

NEW_FAULT = "the door keeps icing up and it is sweating around the frame"

caller = identify_caller("+13095550101")
unit = "TRL-G24-8871"                       # Pearl Street's Traulsen
print(f"caller  : {caller['name']}")
print(f"unit    : Traulsen G22010  ({unit})")
print(f"says    : \"{NEW_FAULT}\"")
print(f"corpus  : {INDEX.size()} closed repairs on record")

before = prior_repairs(unit, NEW_FAULT)
recalled = before["similar_faults_recalled"]
print(f"\nrecall  : {len(recalled)} similar faults found")
for h in recalled:
    print(f"         - {h['found'][:64]}  (score {h['score']})")
print(f"suggests: {[p['name'] for p in before['commonly_needed']] or 'nothing specific'}")
print("\n-> the defrost parts are a guess. Nobody has fixed THIS fault yet.")

# ==========================================================================
act(2, "Dwight fixes it, and writes down what it actually was")

techs = find_technician("reach-in freezer", unit)
nearest = techs["technicians"][0]
print(f"dispatch: {nearest['name']}, {nearest['distance_mi']} mi, "
      f"~{nearest['drive_minutes']} min from {techs['site']}")
print("          (ordered by drive time, not just who is qualified)")

wo = open_work_order(caller["customer_id"], unit, NEW_FAULT)
promise_slot(wo["work_order_id"], nearest["id"], "Friday 9-11am", ["TRL-556700"])

closed = close_work_order(
    wo["work_order_id"],
    found_cause="mullion heater harness open at the connector, so the door frame "
                "runs below dew point and the gasket freezes to it",
    parts_consumed=["TRL-556700"],
    labor_hours=1.75,
    tech_note="not a defrost fault. Check the harness at the hinge side before "
              "touching the defrost circuit, it chafes there on this model",
    first_visit_fix=True,
)
print(f"\nlearned : {closed['learned'][:120]}...")
print(f"corpus  : {closed['corpus_size']} closed repairs on record  (+1)")

# ==========================================================================
act(3, "Different site. Different unit. Different words.")

OTHER_WORDS = "freezer door is sweating and keeps freezing shut in the mornings"

caller2 = identify_caller("+13095550102")
unit2 = "TRL-G24-9903"                      # Rivertown Tap's Traulsen, same model
print(f"caller  : {caller2['name']}")
print(f"unit    : Traulsen G22010  ({unit2})   <- a different physical unit")
print(f"says    : \"{OTHER_WORDS}\"")
print("\n          the technician wrote 'mullion heater harness open at the connector'")
print("          the caller said   'sweating and keeps freezing shut'")
print("          no shared keywords. Substring matching returns nothing here.")

after = prior_repairs(unit2, OTHER_WORDS)
print(f"\nrecall  : {len(after['similar_faults_recalled'])} similar faults found")
for h in after["similar_faults_recalled"]:
    print(f"         - {h['found'][:66]}")
    print(f"            score {h['score']}, {h['why']}, parts {list(h['parts_consumed'])}")

wo2 = open_work_order(caller2["customer_id"], unit2, OTHER_WORDS)
techs2 = find_technician("reach-in freezer", unit2)
skus = [p["sku"] for p in after["commonly_needed"]][:2]
promise_slot(wo2["work_order_id"], techs2["technicians"][0]["id"], "Monday 8-10am", skus)
brief = build_briefing(wo2["work_order_id"])

print(f"\n{'-'*74}\nBRIEFING SENT TO {brief['technician'].upper()}\n{'-'*74}")
print(f"  site    : {brief['site']}")
print(f"  unit    : {brief['unit']}")
print(f"  reported: {brief['reported']}")
print("  LOAD    :")
for p in brief["load_these"]:
    print(f"           - {p['name']}  ({p['sku']})")
print("  because :")
for h in brief["similar_faults_recalled"][:2]:
    print(f"            {h['found'][:68]}")
print(f"  in van  : {brief['already_in_van']}")

print(f"\n{SEP}")
print("Act 1 knew nothing. Act 3 knew, because Act 2 told it.")
print("Nobody wrote a rule. One technician wrote one sentence.")
print(SEP)
