"""Did the parts actually leave the building.

WHY THIS IS THE GAP WORTH CLOSING

This project opens with a sentence: a technician drives an hour and does not
have the part, and the company already knew which part it was.

The desk works the part out from 670 repairs, weighs carrying it against a
wasted trip, holds it against the visit in one transaction so nobody else can
take it, and texts the technician a briefing telling them what to load.

Then nothing checks that anybody picked it up.

`reservations` records `reserved_at` and `released_at`. That is a claim on
stock, not a fact about a van. A held part and a loaded part are different
things, and the difference is the exact failure this system exists to prevent.

WHAT THE EVIDENCE SAYS

Insufficient or incorrect parts on site is 51% of failed first visits, against
25% for skills and 13% for time. The field-service audits are blunter still:
where first-time fix sits below 75%, the cause is a technician leaving the
depot without confirmed parts, and making confirmation mandatory rather than
optional moves the number within a fortnight. It is a preparation problem.

WHY IT IS ONE WORD

The technician already replies to the briefing by text to close a job, in
whatever words they use, with grease on their hands. Asking them to open an
app before leaving would be a second system to ignore, and app fragmentation
is among the most cited adoption barriers in mobile workforce research.

So it is the same thread, and the answer is a word. Reading "loaded" is far
easier than what close_by_text already does.

WHAT IT REFUSES TO DO

It does not mark a part loaded because a technician said something vaguely
positive. Only a clear confirmation counts, because a reservation wrongly
marked picked is worse than one left open: it turns a question the desk could
have asked into a fact nobody will check.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import db

# How close to the promised window we start caring that nobody has confirmed.
# Far enough ahead that the parts can still be fetched, near enough that the
# technician is thinking about this job rather than the last one.
WARN_BEFORE_MINUTES = 45

# What counts as a confirmation. Deliberately narrow. A reservation wrongly
# marked picked is worse than one left open, because it converts a question
# the desk could still ask into a fact nobody will check again.
CONFIRMED = re.compile(
    r"\b(loaded|got (?:them|it|the parts?)|picked (?:up|them)?|"
    r"have (?:them|it|the parts?)|on the van|in the van|all set|ready)\b", re.I)

# And what looks like confirmation but is not. A technician saying they do not
# have something must never be read as having it.
DENIED = re.compile(
    r"\b(not|no|don'?t|cannot|can'?t|missing|out of stock|none|without)\b", re.I)


def wants_confirmation(visit_id: str) -> list[dict]:
    """The parts this visit is waiting on, if any."""
    with db.connect() as c:
        rows = c.execute(
            """SELECT r.sku, p.name FROM reservations r
               JOIN parts p ON p.sku = r.sku
               WHERE r.visit_id = ? AND r.released_at IS NULL
                 AND r.picked_at IS NULL""", (visit_id,)).fetchall()
    return [{"sku": r["sku"], "name": r["name"]} for r in rows]


def ask_line(visit_id: str) -> str:
    """The sentence added to the briefing. Empty when nothing is held.

    Named parts rather than a count, because "reply LOADED when you have the
    two parts" is a question somebody can answer without going to look.
    """
    parts = wants_confirmation(visit_id)
    if not parts:
        return ""
    named = " and ".join(p["name"].lower() for p in parts[:3])
    return f"Reply LOADED when you have the {named}."


def confirm_loaded(technician_phone: str, message: str = "loaded",
                   visit_id: str = "") -> dict:
    """A technician says they have the parts. Nothing else in the system says so.

    Args:
        technician_phone: who replied.
        message: their words, so a denial is not read as a confirmation.
        visit_id: optional. Their next unconfirmed visit if omitted, which is
            what replying to a briefing thread means in practice.
    """
    if DENIED.search(message or "") or not CONFIRMED.search(message or ""):
        return {"ok": False, "confirmed": False,
                "why": "that does not clearly say the parts are loaded",
                "say": "Ask plainly whether they have the parts. Do not assume."}

    with db.connect() as c:
        tech = c.execute("SELECT id, name FROM technicians WHERE phone = ?",
                         (technician_phone,)).fetchone()
        if tech is None:
            return {"ok": False, "why": "that number is not a technician on file"}

        if visit_id:
            visit = c.execute("SELECT id FROM visits WHERE id = ?",
                              (visit_id,)).fetchone()
        else:
            visit = c.execute(
                """SELECT v.id FROM visits v
                   JOIN reservations r ON r.visit_id = v.id
                   WHERE v.technician_id = ? AND v.completed_at IS NULL
                     AND r.released_at IS NULL AND r.picked_at IS NULL
                   ORDER BY v.id DESC LIMIT 1""", (tech["id"],)).fetchone()

        if visit is None:
            return {"ok": False,
                    "why": "no visit of theirs is waiting on parts"}

    now = datetime.now().isoformat(timespec="seconds")
    with db.txn() as c:
        c.execute(
            """UPDATE reservations SET picked_at = ?
               WHERE visit_id = ? AND released_at IS NULL AND picked_at IS NULL""",
            (now, visit["id"]))

    return {"ok": True, "confirmed": True, "visit": visit["id"],
            "technician": tech["name"],
            "reply_to_technician": f"Thanks {tech['name'].split()[0]}, "
                                   "marked as loaded."}


def unconfirmed(dealer_id: str = "D-REF", at: datetime | None = None) -> list[dict]:
    """Visits due soon where nobody has said the parts are on the van.

    The point of the whole feature: this is knowable while the technician is
    still at the depot, and the alternative is finding out an hour away.
    """
    at = at or datetime.now()
    horizon = (at + timedelta(minutes=WARN_BEFORE_MINUTES)).isoformat(timespec="seconds")

    with db.connect() as c:
        rows = c.execute(
            # When the visit actually starts lives on `appointments`, not on
            # the visit. `visits.promised_at` is when the promise was MADE and
            # `promised_window` is human prose, so neither can be compared to
            # a clock. promise_slot writes both rows in one transaction, so
            # the join is safe.
            """SELECT v.id visit, ap.starts_at, t.name technician, t.phone,
                      a.manufacturer, a.model_number,
                      GROUP_CONCAT(p.name, ', ') parts
               FROM visits v
               JOIN reservations r  ON r.visit_id = v.id
               JOIN parts p         ON p.sku = r.sku
               JOIN work_orders w   ON w.id = v.work_order_id
               JOIN appointments ap ON ap.visit_id = v.id
               LEFT JOIN assets a   ON a.id = w.asset_id
               LEFT JOIN technicians t ON t.id = v.technician_id
               WHERE w.dealer_id = ? AND v.completed_at IS NULL
                 AND r.released_at IS NULL AND r.picked_at IS NULL
                 AND ap.starts_at <= ?
               GROUP BY v.id ORDER BY ap.starts_at""",
            (dealer_id, horizon)).fetchall()

    return [{
        "visit": r["visit"],
        "starts_at": r["starts_at"],
        "technician": r["technician"],
        "phone": r["phone"],
        "machine": f"{r['manufacturer']} {r['model_number']}".strip(),
        "parts": r["parts"],
        "say": (f"{(r['technician'] or 'the technician').split()[0]} has not "
                f"confirmed the parts for {r['starts_at'][11:16]}. Ask before "
                "they leave, not after."),
    } for r in rows]


def how_often_unloaded(dealer_id: str = "D-REF", days: int = 90) -> dict:
    """How often a briefed part did not make it onto the van.

    A number nobody else in this field can produce, because nobody else knows
    what was supposed to be carried.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with db.connect() as c:
        row = c.execute(
            """SELECT COUNT(*) held,
                      COALESCE(SUM(CASE WHEN r.picked_at IS NOT NULL THEN 1 ELSE 0 END),0) picked
               FROM reservations r
               JOIN visits v      ON v.id = r.visit_id
               JOIN work_orders w ON w.id = v.work_order_id
               WHERE w.dealer_id = ? AND r.reserved_at >= ?""",
            (dealer_id, cutoff)).fetchone()

    held, picked = row["held"] or 0, row["picked"] or 0
    if not held:
        return {"held": 0, "say": "No parts have been held in this window."}

    return {
        "held": held,
        "confirmed_loaded": picked,
        "never_confirmed": held - picked,
        "confirmed_rate": round(picked / held, 2),
        "say": ("A part held and never confirmed is not proof it was left "
                "behind. It is proof nobody checked, which is the condition "
                "the audits find underneath a poor first-time fix rate."),
    }
