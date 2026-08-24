"""Stage an open visit, then close it the way a technician actually would.

Run on the VM, where Gemma lives:
    ./.venv/bin/python scripts/test_textback.py
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.memory as memory  # noqa: E402
from src import db  # noqa: E402
from src.textback import close_by_text  # noqa: E402

MESSAGES = [
    "was the mullion harness again at the hinge side, fitted it, about two hours. "
    "tell the next guy it chafes there",
    "evap fan motor had seized, swapped it out, 90 mins, all good",
    "diagnosed the control board but dont have one on the van, need to come back",
]


def stage(symptom: str) -> tuple[str, str]:
    with db.txn() as c:
        t = c.execute("SELECT id, name, phone FROM technicians LIMIT 1").fetchone()
        a = c.execute(
            """SELECT ast.id, ast.manufacturer, ast.model_number, s.id site
               FROM assets ast JOIN sites s ON s.id = ast.site_id
               WHERE ast.family='reach-in freezer' LIMIT 1""").fetchone()
        acc = c.execute("SELECT account_id FROM sites WHERE id=?", (a["site"],)).fetchone()
        wo = "WO-" + uuid.uuid4().hex[:6].upper()
        v = "V-" + uuid.uuid4().hex[:6].upper()
        c.execute("""INSERT INTO work_orders
                     (id,account_id,site_id,asset_id,reported_symptom,status,opened_at)
                     VALUES (?,?,?,?,?,?,?)""",
                  (wo, acc["account_id"], a["site"], a["id"], symptom, "scheduled",
                   datetime.now().isoformat(timespec="seconds")))
        c.execute("INSERT INTO visits (id,work_order_id,seq,technician_id) VALUES (?,?,1,?)",
                  (v, wo, t["id"]))
    return t["phone"], v


def main() -> None:
    memory.load_from_db()
    print(f"corpus at start: {memory.INDEX.size()} repairs\n")

    for msg in MESSAGES:
        phone, visit = stage("door keeps sweating and sticking shut")
        print("-" * 74)
        print(f'technician texts: "{msg[:66]}"')
        t0 = time.time()
        r = close_by_text(phone, msg, visit)
        if not r.get("ok"):
            print(f"  FAILED: {r}")
            continue
        u = r["understood"]
        print(f"  parsed by {r['parsed_by']} in {time.time()-t0:.0f}s")
        print(f"    cause     {u['found_cause'][:60]}")
        print(f"    parts     {u['parts']}")
        print(f"    hours     {u['hours']}      fixed: {u['fixed']}")
        print(f"    note      {u['note']}")
        if r["unrecognised"]:
            print(f"    unmatched {r['unrecognised']}")
        print(f"    repair    {r['repair_written']}   searchable: {r['searchable_now']}")
        print(f"    replies   {r['reply_to_technician'][:66]}")

    print("-" * 74)
    print(f"\ncorpus at end: {memory.INDEX.size()} repairs")


if __name__ == "__main__":
    main()
