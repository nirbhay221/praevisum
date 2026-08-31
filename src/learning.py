"""Making sure a finished job actually becomes something the desk knows.

THE LOOP THAT WAS ONLY HALF CLOSED

`textback.py` does this properly. When a technician replies to close a job it
writes a `repairs` row and adds it to the search index, so the next caller with
the same symptom on the same model gets the benefit within seconds. That is the
best mechanism in this system.

It is also the ONLY route into the corpus. Counted on the live book:

    completed visits with a diagnosed cause    851
    of those, with a repairs row               670
    completed, diagnosed, and never learned    181

So one job in five was finished, diagnosed, written up in `visits.found_cause`,
and never became knowledge. The desk kept answering from 670 repairs while 851
had actually been done. Nothing was broken and nothing errored: the work simply
did not arrive anywhere the desk could read it.

A learning loop with a second entrance nobody watches is not a learning loop,
it is a sampling bias, and it biases in a specific direction. The visits that
close through the text channel are the ones with an engaged technician on a
phone with signal. The ones that close some other way, on paper, in the office,
by a manager tidying up, are exactly the awkward jobs most worth learning from.

WHAT THIS DOES NOT DO

It does not invent a diagnosis. A visit with no `found_cause` is not learned
from, because there is nothing to learn: an entry saying a machine was fixed
somehow makes the corpus worse, not bigger. Only work somebody actually
diagnosed becomes knowledge.
"""

from __future__ import annotations

import uuid

from . import db


def _repair_id() -> str:
    return f"R-{uuid.uuid4().hex[:6].upper()}"


def unlearned(dealer_id: str = "") -> list[dict]:
    """Completed, diagnosed visits that never became a repair record."""
    where = ["v.completed_at IS NOT NULL",
             "v.found_cause IS NOT NULL",
             "TRIM(COALESCE(v.found_cause,'')) != ''",
             "NOT EXISTS (SELECT 1 FROM repairs r WHERE r.visit_id = v.id)"]
    params: list = []
    if dealer_id:
        where.append("wo.dealer_id = ?")
        params.append(dealer_id)

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT v.id visit_id, v.found_cause, v.tech_note,
                       v.labor_hours, v.completed_at, v.technician_id,
                       wo.id work_order_id, wo.reported_symptom, wo.error_code,
                       wo.dealer_id,
                       a.id asset_id, a.manufacturer, a.model_number, a.family
                FROM visits v
                JOIN work_orders wo ON wo.id = v.work_order_id
                LEFT JOIN assets a ON a.id = wo.asset_id
                WHERE {' AND '.join(where)}
                ORDER BY v.completed_at""", params).fetchall()

    return [dict(r) for r in rows]


def close_the_loop(dealer_id: str = "", limit: int = 0,
                   index: bool = True) -> dict:
    """Turn every finished, diagnosed visit into knowledge the desk can use.

    Safe to run repeatedly: a visit that already has a repair row is skipped,
    so this reconciles rather than duplicating.

    Args:
        dealer_id: one business, or empty for all of them.
        limit: stop after this many, 0 for no limit.
        index: also add each one to the search index, which is what actually
            makes it reachable from a call.
    """
    pending = unlearned(dealer_id)
    if limit:
        pending = pending[:limit]

    written, indexed, skipped = 0, 0, []

    for v in pending:
        # A visit whose work order lost its asset cannot be attributed to a
        # machine, and a repair record with no model number teaches the desk
        # nothing it can match against later.
        if not v.get("asset_id") or not v.get("model_number"):
            skipped.append({"visit": v["visit_id"],
                            "why": "no machine on the work order"})
            continue

        rid = _repair_id()
        first = _was_the_first_visit(v["work_order_id"])

        with db.txn() as c:
            c.execute(
                """INSERT INTO repairs
                     (id,visit_id,asset_id,manufacturer,model_number,family,
                      reported_symptom,error_code,found_cause,tech_note,
                      labor_hours,first_visit_fix,closed_on,technician_id,
                      dealer_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, v["visit_id"], v["asset_id"], v["manufacturer"],
                 v["model_number"], v["family"], v["reported_symptom"],
                 v["error_code"], v["found_cause"], v["tech_note"],
                 v["labor_hours"], 1 if first else 0,
                 (v["completed_at"] or "")[:10], v["technician_id"],
                 v["dealer_id"]))
        written += 1

        if index and _index_it(rid, v):
            indexed += 1

    return {
        "ok": True, "written": written, "indexed": indexed,
        "skipped": skipped, "still_unlearned": len(unlearned(dealer_id)),
        "say": (f"{written} finished job(s) became knowledge the desk can "
                "actually reach. Until now they were diagnosed, written up "
                "and invisible."),
    }


def _was_the_first_visit(work_order_id: str) -> bool:
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM visits WHERE work_order_id = ?",
                      (work_order_id,)).fetchone()["n"]
    return n <= 1


def _index_it(repair_id: str, v: dict) -> bool:
    """Add one repair to the search index.

    Failing here is not fatal and is reported rather than raised: the row is
    already written, so a rebuild of the index picks it up later. A repair that
    never reaches the index is the loop failing to close, which is worth
    seeing in the result.
    """
    try:
        from . import memory
        from .domain.models import Repair

        note = v.get("tech_note") or ""
        memory.INDEX.add(Repair(
            id=repair_id, serial=v["asset_id"],
            manufacturer=v["manufacturer"], model=v["model_number"],
            reported_symptom=v.get("reported_symptom") or "",
            error_code=v.get("error_code"),
            found_cause=(v["found_cause"] + (f". {note}" if note else "")),
            parts_consumed=(),
            labor_hours=float(v.get("labor_hours") or 0.0),
            closed_on=(v.get("completed_at") or "")[:10],
            technician_id=v.get("technician_id") or ""))
        return True
    except Exception as e:
        print(f"[learning] {repair_id} written but not indexed: "
              f"{type(e).__name__}: {e}", flush=True)
        return False
