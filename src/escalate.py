"""A job we cannot staff, handed to somebody who can do something about it.

WHAT THIS REPLACES

One line in scheduling.py:

    "advice": "Say so plainly and offer to have a supervisor call back."

On a live call a restaurant with a freezer sitting at fifteen degrees was told
exactly that. No supervisor was named. No callback was recorded anywhere.
Nothing was queued. Nobody was going to ring. It was a shrug with a job title
on it, and by then the customer had already been quoted a price and had a work
order opened for a visit that could never have been staffed.

An escalation that is not written down is not an escalation, it is a way of
ending an awkward conversation.

WHAT AN HONEST ONE LOOKS LIKE

  A NAME. "A supervisor will call you" is not something a customer can hold us
  to. "Dale Brenner will ring you before six" is.

  A TIME. Not "shortly". A restaurant deciding whether to move stock into a
  neighbour's walk-in needs to know whether we mean an hour or tomorrow.

  A ROW SOMEBODY SEES. It goes on the follow-up queue the sweep already reads,
  so it is delivered by the same machinery as every other promise this system
  makes, rather than living in a transcript nobody opens.

  AND IT IS ASKED FIRST. cover.can_we_serve is a single query and it belongs
  BEFORE the quote, not after the work order. Knowing we cannot staff a job
  changes the whole conversation, and finding out last turns a price into an
  apology.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import db
from .tenancy import the_desk

# How quickly somebody promises to come back on an escalation, by how bad it
# is. A failing freezer in a working kitchen is not a next-week problem: stock
# is spoiling while nobody rings.
URGENT_HOURS = 2
ORDINARY_HOURS = 24

# Families where the machine failing means food is spoiling right now.
PERISHABLE = ("freezer", "cooler", "chiller", "ice machine", "refrigerat")


def _manager(dealer_id: str) -> tuple[str, str]:
    """Who picks these up. A person, not a role."""
    try:
        with db.connect() as c:
            row = c.execute(
                "SELECT manager_name, manager_phone FROM dealers WHERE id=?",
                (dealer_id,)).fetchone()
        if row and row["manager_name"]:
            return row["manager_name"], row["manager_phone"] or ""
    except Exception:
        pass
    return "", ""


def _urgent(family: str) -> bool:
    return any(w in (family or "").lower() for w in PERISHABLE)


def raise_it(reason: str, asset_id: str = "", detail: str = "",
             work_order_id: str = "", dealer_id: str = "") -> dict:
    """Hand a job we cannot staff to a named person, with a time on it.

    Args:
        reason: no_qualified_technician, no_slot, or other.
        asset_id: the machine, if there is one.
        detail: what exactly is missing, in plain words.
        work_order_id: the job, if one was opened.
        dealer_id: whose branch.
    """
    dealer_id = the_desk(dealer_id)
    from .trace import CALL, here

    family = ""
    account_id = None
    phone = ""
    contact_id = None

    if asset_id:
        try:
            with db.connect() as c:
                row = c.execute(
                    """SELECT a.family, s.account_id FROM assets a
                       JOIN sites s ON s.id = a.site_id WHERE a.id = ?""",
                    (asset_id,)).fetchone()
                if row:
                    family, account_id = row["family"] or "", row["account_id"]
        except Exception as e:
            print(f"[escalate] could not read the machine: "
                  f"{type(e).__name__}: {e}", flush=True)

    call_id = here()
    if call_id:
        try:
            with db.connect() as c:
                row = c.execute(
                    """SELECT cl.from_e164, cl.contact_id FROM calls cl
                       WHERE cl.id = ?""", (call_id,)).fetchone()
                if row:
                    phone, contact_id = row["from_e164"], row["contact_id"]
        except Exception:
            pass

    hours = URGENT_HOURS if _urgent(family) else ORDINARY_HOURS
    by = datetime.now() + timedelta(hours=hours)
    name, _ = _manager(dealer_id)

    who = name or "the branch manager"
    when = (f"within {hours} hours" if hours < 6
            else f"by {by.strftime('%A')} morning")
    promised = f"{who} will ring you back {when}"

    eid = "ESC-" + uuid.uuid4().hex[:6].upper()
    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO escalations
                   (id,dealer_id,call_id,account_id,asset_id,work_order_id,
                    reason,detail,promised,promised_by,opened_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, dealer_id, call_id or None, account_id, asset_id or None,
                 work_order_id or None, reason, detail or None, promised,
                 by.isoformat(timespec="seconds"),
                 datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        print(f"[escalate] could not record {eid}: {type(e).__name__}: {e}",
              flush=True)
        return {"ok": False,
                "why": "we could not record the escalation",
                "say": "Do not promise a callback you cannot see recorded. "
                       "Tell them plainly that you are having trouble logging "
                       "it and take their number again."}

    # On the same queue as every other promise this system makes, so it is
    # delivered by machinery that already runs rather than by hope.
    queued = None
    if phone:
        try:
            from . import followup

            queued = followup._queue(
                "escalation", phone, dealer_id=dealer_id,
                account_id=account_id, contact_id=contact_id,
                from_call=call_id or None, work_order_id=work_order_id or None,
                context=f"{promised}. {detail}"[:400],
                delay=timedelta(minutes=5))
        except Exception as e:
            print(f"[escalate] not queued: {type(e).__name__}: {e}", flush=True)

    return {
        "ok": True,
        "escalation_id": eid,
        "to": who,
        "promised": promised,
        "by": by.isoformat(timespec="seconds"),
        "urgent": _urgent(family),
        "queued": bool(queued and queued.get("ok")),
        "say": (
            f"Say it as a commitment with a name and a time on it: '{promised}'. "
            "Give them the reference. Do NOT say 'a supervisor will call you' "
            "and do NOT say 'shortly': a kitchen deciding whether to move "
            "stock into a neighbour's walk-in needs to know whether we mean an "
            "hour or tomorrow. Say it ONCE and then stop. Repeating it does "
            "not make it more reassuring, it makes it sound like there is "
            "nothing else you can do."),
    }


def open_escalations(dealer_id: str = "") -> list[dict]:
    """What is waiting on a person, and what was promised on their behalf."""
    dealer_id = the_desk(dealer_id)
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as c:
        rows = c.execute(
            """SELECT id, reason, detail, promised, promised_by, asset_id,
                      opened_at
               FROM escalations
               WHERE dealer_id = ? AND state IN ('open','picked_up')
               ORDER BY promised_by""", (dealer_id,)).fetchall()
    return [dict(r) | {"overdue": bool(r["promised_by"] and r["promised_by"] < now)}
            for r in rows]


def take(escalation_id: str, by: str, outcome: str = "") -> dict:
    """A person picks it up. Recorded so a promise cannot quietly lapse."""
    if not by:
        return {"ok": False, "why": "somebody has to take it, by name"}

    with db.txn() as c:
        c.execute(
            """UPDATE escalations SET state=?, taken_by=?, taken_at=?, outcome=?
               WHERE id=?""",
            ("resolved" if outcome else "picked_up", by,
             datetime.now().isoformat(timespec="seconds"), outcome or None,
             escalation_id))
    return {"ok": True, "escalation_id": escalation_id,
            "state": "resolved" if outcome else "picked_up"}
