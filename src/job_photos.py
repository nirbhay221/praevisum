"""Keeping the photograph a customer sent about a job.

WHAT WAS HAPPENING TO IT

An inbound picture already goes through a vision model, looking for the RATING
PLATE so the desk can work out which machine it is. Useful, and not what a
customer photographs when something is wrong: they send the puddle on the
floor, the frost on the coil, the split gasket, the error code on the display.

That photo was read, answered, and dropped. The engineer then arrived knowing
the model number and nothing about what they were walking into, which is the
opposite of the reason the desk asks for one. A picture of the fault decides
WHICH PART goes on the van, and the whole point is the second visit that does
not have to happen.

WHAT IS KEPT AND WHAT IS NOT

Not the bytes. This database is a service desk, not a photo library, and
storing customer photographs raises a retention question nobody here has
answered. What is kept is what the model read, on which channel, and when --
which is what a briefing actually needs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from . import db


def _nid() -> str:
    return f"PIC-{uuid.uuid4().hex[:8].upper()}"


def the_open_job_for(account_id: str) -> dict:
    """The job a photo from this customer is most likely about.

    The newest one that is not closed. A customer with two open jobs is rare
    and a customer with none is common, so this returns nothing rather than
    guessing when it cannot tell.
    """
    if not account_id:
        return {}
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT w.id, w.asset_id, w.dealer_id, w.reported_symptom
               FROM work_orders w
               WHERE w.account_id = ? AND w.status != 'closed'
               ORDER BY w.opened_at DESC LIMIT 2""", (account_id,))]
    return rows[0] if len(rows) == 1 else {}


def keep(account_id: str, what_it_shows: str, *, channel: str = "whatsapp",
         from_number: str = "", media_type: str = "",
         manufacturer: str = "", model_number: str = "",
         work_order_id: str = "") -> dict:
    """Attach what a photo showed to the job it is about.

    Never raises. A picture that could not be filed is worth a log line and is
    not worth failing a customer's message over.
    """
    try:
        job = {}
        if not work_order_id:
            job = the_open_job_for(account_id)
            work_order_id = job.get("id", "")

        pid = _nid()
        with db.txn() as c:
            c.execute(
                """INSERT INTO job_photos
                   (id, work_order_id, account_id, asset_id, dealer_id,
                    arrived_at, channel, from_number, media_type,
                    what_it_shows, manufacturer, model_number)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, work_order_id or None, account_id or None,
                 job.get("asset_id"), job.get("dealer_id"),
                 datetime.now().isoformat(timespec="seconds"),
                 channel, from_number or None, media_type or None,
                 (what_it_shows or "").strip()[:600] or None,
                 manufacturer or None, model_number or None))

        return {"ok": True, "photo": pid, "work_order_id": work_order_id,
                "attached": bool(work_order_id)}
    except Exception as e:
        print(f"[job_photos] could not keep a photo for {account_id}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": f"{type(e).__name__}"}


def for_the_engineer(work_order_id: str) -> list[dict]:
    """What the customer has sent in about this job, for the briefing."""
    if not work_order_id:
        return []
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            """SELECT arrived_at, channel, what_it_shows, manufacturer,
                      model_number
               FROM job_photos WHERE work_order_id = ?
               ORDER BY arrived_at""", (work_order_id,))]
