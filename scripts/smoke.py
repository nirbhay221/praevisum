"""Runs the whole differentiator with no credentials and no phone call.

This is the value of the project in ~30 lines: a fault comes in, and the
technician gets told what to put in the van. Run it before anything else works.

    .venv/Scripts/python.exe scripts/smoke.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools import (  # noqa: E402
    build_briefing,
    check_stock,
    find_technician,
    identify_caller,
    open_work_order,
    prior_repairs,
    promise_slot,
)

SEP = "-" * 72


def show(title: str, obj) -> None:
    print(f"\n{SEP}\n{title}\n{SEP}")
    print(json.dumps(obj, indent=2))


# 1. the phone rings
caller = identify_caller("+13095550101")
show("1. who is calling", caller)

unit = next(u for u in caller["units"] if u["family"] == "reach-in freezer")

# 2. the fault, in their words
symptom = "not holding temp overnight, seems fine during service"
history = prior_repairs(unit["serial"], symptom)
show("2. what this unit and this model have needed before", {
    "this_unit": history["this_unit"],
    "same_model_elsewhere": history["same_model_elsewhere"],
    "commonly_needed": history["commonly_needed"],
})

# 3. can we finish it today?
skus = [p["sku"] for p in history["commonly_needed"]]
stock = check_stock(skus)
show("3. parts", stock)

# 4. who can go
techs = find_technician(unit["family"])
show("4. qualified technicians", techs)

# 5. commit, on the call
wo = open_work_order(caller["customer_id"], unit["serial"], symptom, error_code="dEF")
promise = promise_slot(wo["work_order_id"], techs["technicians"][0]["id"],
                       "Thursday 2-4pm", skus)
show("5. the promise", promise)

# 6. what the technician gets before leaving  <- the whole point
if promise.get("promised"):
    show("6. BRIEFING SENT TO TECHNICIAN", build_briefing(wo["work_order_id"]))
else:
    print("\npromise refused - nothing gets briefed. That is correct behaviour.")
