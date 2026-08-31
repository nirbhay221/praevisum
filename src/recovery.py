"""When the job is done, and when the two accounts of it disagree.

TWO LOOPS THAT STOPPED HALFWAY

followup.queue_after_visit already asks the right question a day later: is it
still working. That is the only thing about a repair the database cannot
already tell us. But the answer went nowhere. It was never attached to the
person who did the work, so a technician whose fixes came back twice as often
as anybody else's looked identical to one whose never did.

And there was nothing at all for the case where the customer and the
technician describe different visits. It happens, it is not rare, and handling
it badly is how a repairable relationship turns into a lost account.

WHAT THE RESEARCH SAYS, AND WHY IT CHANGED THE DESIGN

The service recovery literature is consistent on one distinction that this
would otherwise have got wrong: OUTCOME failures and PROCESS failures are not
the same failure and must not draw the same response.

    outcome   the machine is still broken. What they paid for did not happen.
    process   it got fixed, but late, or messily, or rudely.

Outcome failures require materially higher compensation, and expectations rise
with severity. Treating "the technician was two hours late" and "my freezer is
still warm and I have lost a service" as the same thing insults one customer
and overpays the other.

The other finding worth building on is the service recovery paradox: a failure
that is handled well can leave somebody MORE loyal than if it had never gone
wrong. That is only true when the recovery is fast and the customer does not
have to fight for it, which is why reassignment here is immediate and does not
wait for anybody to decide who was right.

WHO WAS RIGHT IS USUALLY THE WRONG QUESTION

Both accounts are recorded, neither is adjudicated on the phone, and a
different technician goes out. Arguing with a customer about what happened in
their own kitchen cannot be won, and the machine is still broken while it is
being argued about.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from . import db
from .tenancy import the_desk

# What a make-good is worth, as a share of the visit. Outcome failures sit
# higher than process failures because the published work is unambiguous that
# they have to.
MAKE_GOOD = {
    ("outcome", "severe"): 1.00,   # they paid and it is still broken
    ("outcome", "normal"): 0.50,
    ("process", "severe"): 0.25,   # late enough to cost them trade
    ("process", "normal"): 0.10,
}

OUTCOME_WORDS = ("still", "not fixed", "same problem", "broken", "again",
                 "worse", "never worked", "not working", "no better")
SEVERE_WORDS = ("lost", "spoiled", "closed", "shut", "trade", "stock",
                "second time", "third time", "twice", "all day")


def _nid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def record_workmanship(work_order_id: str, still_working: bool | None = None,
                       on_time: bool | None = None,
                       customer_said: str = "") -> dict:
    """What the customer said afterwards, attributed to whoever did the work.

    Called with the answer to the after-visit question. Deliberately not a star
    rating: nobody learns anything from four out of five. Whether the fix held
    is a fact, it is checkable, and it is the only feedback that should change
    what this desk does next.

    Args:
        work_order_id: the job.
        still_working: did the repair hold.
        on_time: did the technician arrive in the window we promised.
        customer_said: their words, kept as said.
    """
    with db.connect() as c:
        v = c.execute(
            """SELECT v.id visit_id, v.technician_id, w.dealer_id
               FROM work_orders w
               LEFT JOIN visits v ON v.work_order_id = w.id
               WHERE w.id = ? ORDER BY v.seq DESC LIMIT 1""",
            (work_order_id,)).fetchone()
    if v is None:
        return {"ok": False, "why": "no such job"}

    wid = _nid("WM")
    with db.txn() as c:
        c.execute(
            """INSERT INTO workmanship
               (id, work_order_id, visit_id, technician_id, asked_at,
                still_working, on_time, customer_said, dealer_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (wid, work_order_id, v["visit_id"], v["technician_id"],
             datetime.now().isoformat(timespec="seconds"),
             None if still_working is None else int(bool(still_working)),
             None if on_time is None else int(bool(on_time)),
             customer_said or None, v["dealer_id"]))

    out = {"ok": True, "recorded": wid, "technician": v["technician_id"]}

    if still_working is False:
        out["say"] = ("It did not hold. Do NOT ask them to wait and see. "
                      "Raise it as a dispute now and get somebody back out: "
                      "a repair that failed is an outcome failure and they "
                      "have already paid for it once.")
    return out


def how_they_are_doing(technician_id: str = "", dealer_id: str = "",
                       days: int = 180) -> dict:
    """Whose fixes hold, from what customers actually said afterwards.

    Not a league table for its own sake. The useful use is choosing who to
    send back out when one has already failed, and noticing when somebody
    needs help rather than blame.
    """
    dealer_id = the_desk(dealer_id)
    where = "wm.asked_at >= date('now', ?)"
    params: list = [f"-{int(days)} days"]
    if technician_id:
        where += " AND wm.technician_id = ?"
        params.append(technician_id)
    if dealer_id:
        where += " AND wm.dealer_id = ?"
        params.append(dealer_id)

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT t.id, t.name,
                       COUNT(*) asked,
                       SUM(CASE WHEN wm.still_working = 1 THEN 1 ELSE 0 END) held,
                       SUM(CASE WHEN wm.still_working = 0 THEN 1 ELSE 0 END) failed,
                       SUM(CASE WHEN wm.on_time = 0 THEN 1 ELSE 0 END) late
                FROM workmanship wm
                JOIN technicians t ON t.id = wm.technician_id
                WHERE {where}
                GROUP BY t.id ORDER BY failed DESC, asked DESC""",
            params).fetchall()

    out = []
    for r in rows:
        asked = r["asked"] or 0
        out.append({
            "technician_id": r["id"], "name": r["name"],
            "asked": asked, "held": r["held"] or 0,
            "failed": r["failed"] or 0, "late": r["late"] or 0,
            "held_rate": round((r["held"] or 0) / asked, 2) if asked else None,
        })
    return {"technicians": out, "days": days}


def _classify(customer_says: str) -> tuple[str, str]:
    """Outcome or process, normal or severe, from what they actually said."""
    low = (customer_says or "").lower()
    kind = "outcome" if any(w in low for w in OUTCOME_WORDS) else "process"
    severity = "severe" if any(w in low for w in SEVERE_WORDS) else "normal"
    return kind, severity


def raise_dispute(work_order_id: str, customer_says: str,
                  technician_says: str = "") -> dict:
    """Two accounts of one visit. Record both, send somebody else, make good.

    Nothing here decides who was right, and that is deliberate. An argument
    about what happened in somebody's own kitchen cannot be won on the phone,
    and the machine is still broken for the whole length of it.

    Args:
        work_order_id: the job being disputed.
        customer_says: their account, in their words.
        technician_says: the technician's account, if we have it yet.
    """
    kind, severity = _classify(customer_says)

    with db.connect() as c:
        job = c.execute(
            """SELECT w.id, w.dealer_id, w.asset_id, a.family,
                      v.id visit_id, v.technician_id
               FROM work_orders w
               LEFT JOIN assets a ON a.id = w.asset_id
               LEFT JOIN visits v ON v.work_order_id = w.id
               WHERE w.id = ? ORDER BY v.seq DESC LIMIT 1""",
            (work_order_id,)).fetchone()
    if job is None:
        return {"ok": False, "why": "no such job"}

    other = _somebody_else(job["technician_id"], job["dealer_id"],
                           job["family"])

    did = _nid("DIS")
    with db.txn() as c:
        c.execute(
            """INSERT INTO disputes
               (id, work_order_id, visit_id, raised_at, customer_says,
                technician_says, kind, severity, reassigned_to, dealer_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (did, work_order_id, job["visit_id"],
             datetime.now().isoformat(timespec="seconds"),
             customer_says or None, technician_says or None,
             kind, severity, (other or {}).get("technician_id"),
             job["dealer_id"]))

    share = MAKE_GOOD.get((kind, severity), 0.10)

    return {
        "ok": True,
        "dispute": did,
        "kind": kind,
        "severity": severity,
        "reassigned_to": other,
        "make_good_share": share,
        "say": _what_to_say(kind, severity, other, technician_says),
    }


def _somebody_else(was: str | None, dealer_id: str | None,
                   family: str | None) -> dict | None:
    """A different technician, qualified for the machine.

    Sending the same person back to a customer who has just complained about
    them is the one option guaranteed to make it worse, whoever was right.
    """
    try:
        with db.connect() as c:
            rows = c.execute(
                """SELECT id, name FROM technicians
                   WHERE active = 1 AND (? IS NULL OR dealer_id = ?)
                     AND (? IS NULL OR id <> ?)
                   ORDER BY id""",
                (dealer_id, dealer_id, was, was)).fetchall()

        from .cover import can_work_on

        for r in rows:
            if not family:
                return {"technician_id": r["id"], "name": r["name"]}
            try:
                if can_work_on(r["id"], family).get("allowed"):
                    return {"technician_id": r["id"], "name": r["name"]}
            except Exception:
                continue
    except Exception as e:
        print(f"[recovery] could not find another technician: "
              f"{type(e).__name__}: {e}", flush=True)
    return None


def _what_to_say(kind: str, severity: str, other: dict | None,
                 technician_says: str) -> str:
    who = f"{other['name']} " if other else "a different engineer "

    if kind == "outcome":
        line = ("They paid for this to be fixed and it is not fixed. Do not "
                "defend the first visit and do not ask them to wait and see. "
                "Say plainly that it should have been sorted the first time.")
    else:
        line = ("The machine got fixed and the way it was done was not good "
                "enough. Acknowledge that specifically rather than "
                "apologising in general.")

    both = ""
    if technician_says:
        both = ("You have both accounts on file and they do not agree. Do NOT "
                "relay what the technician said and do NOT argue the point: "
                "you were not there, and there is nothing to win. ")

    return (
        f"{line} {both}"
        f"Tell them {who}is coming out, and ask THEM what time suits, rather "
        "than offering the next slot in the diary: they have already had one "
        "day arranged around us that did not work.\n"
        + ("Because this is a severe outcome failure, the revisit is free and "
           "so is the original visit. Say so without being asked."
           if (kind, severity) == ("outcome", "severe") else
           "Say what the revisit will cost them, which should be nothing, "
           "before they ask.")
    )


def settle_dispute(dispute_id: str, made_good: str = "",
                   value: float = 0.0) -> dict:
    """Close it, recording what we actually gave them.

    Written down because a make-good nobody recorded is one the next person on
    this account cannot see, and being offered the same apology twice is worse
    than not being offered one.
    """
    with db.txn() as c:
        c.execute(
            """UPDATE disputes
               SET made_good=?, made_good_value=?, settled_at=?
               WHERE id=?""",
            (made_good or None, float(value or 0),
             datetime.now().isoformat(timespec="seconds"), dispute_id))
    return {"ok": True, "dispute": dispute_id, "made_good": made_good,
            "say": "Recorded. The next person who opens this account will see "
                   "what they were given and will not offer it again."}


def open_disputes(dealer_id: str = "") -> dict:
    """Everything unsettled, worst first."""
    dealer_id = the_desk(dealer_id)
    where = "d.settled_at IS NULL"
    params: list = []
    if dealer_id:
        where += " AND d.dealer_id = ?"
        params.append(dealer_id)

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT d.*, a.name account
                FROM disputes d
                JOIN work_orders w ON w.id = d.work_order_id
                JOIN accounts a ON a.id = w.account_id
                WHERE {where}
                ORDER BY CASE WHEN d.kind='outcome' THEN 0 ELSE 1 END,
                         CASE WHEN d.severity='severe' THEN 0 ELSE 1 END,
                         d.raised_at""", params).fetchall()
    return {"open": len(rows), "disputes": [dict(r) for r in rows]}
