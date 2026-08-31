"""It arrived. Now say so, ring them, and close the order.

THE LOOP THAT WAS LEFT OPEN

An order could be placed, confirmed, sourced and promised, and then nothing
ever happened again. There was no delivery event of any kind, so:

  ORDERS NEVER CLOSED. Everything this desk ever sold sat at "confirmed"
  forever. Nobody could answer "did that arrive" without ringing the customer
  and asking, which is the question a system with a carrier reference should
  never need to ask.

  COVER WAS DATED FROM THE PROMISE. ownership.becomes_theirs had to use the
  promised date because it was the only date there was. If the carrier ran two
  days late, the customer lost two days of warranty, silently, in our favour.
  The real date corrects it here.

  NOBODY CHECKED. A machine can arrive damaged, or be the wrong one, or be
  left at the back of a loading bay, and the first anybody hears of it is a
  complaint days later. One short call the day it lands catches all three.

WHY THE CALL IS QUEUED AND NOT PLACED

Because it is an outbound call to a customer, and this service already has
opinions about those: consent, quiet hours, and a frequency cap, all enforced
in outreach.py before anything dials. A delivery check-in is not special
enough to bypass them. Somebody whose freezer arrives at seven in the evening
gets rung in the morning.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import db
from .tenancy import the_desk


def _nid() -> str:
    return f"DEL-{uuid.uuid4().hex[:8].upper()}"


def carrier_delivered(po_id: str, delivered_on: str = "", carrier: str = "",
                      carrier_ref: str = "") -> dict:
    """The carrier says it landed. Record the real date and queue the call.

    This is the webhook end: UPS, or whoever, telling us a tracking number was
    delivered. Nothing here talks to the customer, it only records the fact and
    puts the check-in where the outbound machinery will pick it up.

    Args:
        po_id: the customer order it belongs to.
        delivered_on: the date the carrier reports. Today if not given.
        carrier: who carried it.
        carrier_ref: their tracking number, so a query can be chased.
    """
    when = (delivered_on or date.today().isoformat())[:10]
    now = datetime.now().isoformat(timespec="seconds")

    with db.connect() as c:
        po = c.execute(
            "SELECT id, account_id, contact_id, status FROM purchase_orders "
            "WHERE id=?", (po_id,)).fetchone()
        if po is None:
            return {"ok": False, "why": "no such order"}

        already = c.execute(
            "SELECT id FROM deliveries WHERE po_id=?", (po_id,)).fetchone()

    if already is not None:
        # Carriers resend. A duplicate webhook must not produce a second
        # delivery, a second correction to the warranty date, or a second call.
        #
        # IT MUST STILL MAKE SURE THE MACHINE IS ON THEIR ACCOUNT.
        #
        # This returned here and stopped, which was correct while delivery did
        # nothing else. Once registering the machine moved into this function,
        # that early exit became the one path that skips it -- so every order
        # delivered BEFORE the registration step existed would stay
        # unregistered forever, and re-reporting the delivery, which is the
        # obvious thing to try, would keep returning "already delivered" and
        # keep doing nothing.
        #
        # becomes_theirs hands back what is already there rather than minting
        # a duplicate, so calling it on a repeat is safe and is the only way
        # the backlog ever gets fixed.
        settled: dict = {}
        try:
            from .ownership import becomes_theirs

            settled = becomes_theirs(po_id, delivered_on or "")
        except Exception as e:
            print(f"[delivery] {po_id} was already delivered and could not be "
                  f"put on the account: {type(e).__name__}: {e}", flush=True)

        return {"ok": True, "already": True, "delivery": already["id"],
                "now_theirs": settled.get("registered") or [],
                "already_theirs": settled.get("already_registered") or [],
                "why": "this order was already marked delivered"}

    did = _nid()
    with db.txn() as c:
        c.execute(
            """INSERT INTO deliveries
               (id, po_id, carrier, carrier_ref, delivered_on, notified_at)
               VALUES (?,?,?,?,?,?)""",
            (did, po_id, carrier or None, carrier_ref or None, when, now))
        c.execute("UPDATE purchase_orders SET status='delivered' WHERE id=?",
                  (po_id,))

    # THE WARRANTY CLOCK MOVES TO THE TRUTH.
    #
    # It was set from the promised date because that was the only date in
    # existence when the order was confirmed. Now there is a real one, and if
    # the carrier ran late the customer is owed those days.
    moved = _correct_the_cover(po_id, when)

    # AND IT BECOMES THEIRS. THIS WAS THE MISSING STEP.
    #
    # ownership.becomes_theirs turns the lines of a delivered order into
    # machines on the customer's account, and NOTHING CALLED IT. Not this
    # function, which is the only place that knows a delivery happened; not
    # the carrier webhook, which comes through here; not the console button,
    # which comes through here too.
    #
    # So every path ended the same way: the order went to 'delivered', the
    # warranty dates were corrected against machines that did not exist, and
    # the customer's machine list never changed. On the next call about that
    # machine the desk could not find it, because it had never been written.
    #
    # It is safe to call on a repeat delivery report: becomes_theirs returns
    # what is already registered rather than minting a second machine.
    became: dict = {}
    try:
        from .ownership import becomes_theirs

        became = becomes_theirs(po_id, when)
    except Exception as e:
        # A machine that did not get registered is worth a loud line and is
        # not worth failing the delivery over: the delivery itself is true.
        print(f"[delivery] {po_id} is delivered but could not be put on the "
              f"account: {type(e).__name__}: {e}", flush=True)

    queued = _queue_check_in(po, when)

    return {
        "ok": True,
        "delivery": did,
        "order": po_id,
        "delivered_on": when,
        "now_theirs": became.get("registered") or [],
        "already_theirs": became.get("already_registered") or [],
        "not_machines": became.get("skipped") or [],
        "cover_corrected": moved,
        "check_in": queued,
        "say": ("Do not tell the customer it arrived. They know: it is in "
                "their kitchen. The call is to ask whether it arrived in one "
                "piece and is the right machine, and then to close the order."),
    }


def _correct_the_cover(po_id: str, delivered_on: str) -> list[dict]:
    """Re-date anything sold on this order to the day it actually landed."""
    moved = []
    try:
        with db.connect() as c:
            rows = c.execute(
                """SELECT a.id, a.installed_on, a.manufacturer, a.model_number
                   FROM assets a
                   JOIN sites s ON s.id = a.site_id
                   JOIN purchase_orders p ON p.account_id = s.account_id
                   WHERE p.id = ? AND a.installed_source = 'sold_by_us'
                     AND a.installed_on <> ?""",
                (po_id, delivered_on)).fetchall()

        for r in rows:
            with db.txn() as c:
                c.execute("UPDATE assets SET installed_on=? WHERE id=?",
                          (delivered_on, r["id"]))
            moved.append({"asset_id": r["id"], "was": r["installed_on"],
                          "now": delivered_on,
                          "machine": f"{r['manufacturer']} {r['model_number']}"})
    except Exception as e:
        print(f"[delivery] could not correct the cover date for {po_id}: "
              f"{type(e).__name__}: {e}", flush=True)
    return moved


def _queue_check_in(po, delivered_on: str) -> dict:
    """Put the check-in call where the outbound machinery will find it.

    Not placed here. outreach.py holds consent, quiet hours and the frequency
    cap, and a delivery check-in has no business jumping any of them.
    """
    try:
        with db.connect() as c:
            phone = c.execute(
                """SELECT e164 FROM phones WHERE contact_id = ?
                   ORDER BY verified DESC, e164 LIMIT 1""",
                (po["contact_id"],)).fetchone()
        if phone is None:
            return {"queued": False, "why": "no number on that contact"}

        from .followup import _queue

        return _queue(
            "delivery_check_in", phone["e164"],
            account_id=po["account_id"], contact_id=po["contact_id"],
            # Soon, but not now. outreach.py holds the quiet hours, so a
            # delivery that lands at seven in the evening is rung in the
            # morning rather than immediately.
            delay=timedelta(hours=2),
            context=(f"Their order {po['id']} was delivered on {delivered_on}. "
                   "Ask whether it arrived undamaged and is the right machine, "
                   "and whether anything is missing. If all three are fine, "
                   "close the order. If not, do not argue on the phone: record "
                   "what they say and raise it."))
    except Exception as e:
        print(f"[delivery] could not queue the check-in for {po['id']}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"queued": False, "why": f"{type(e).__name__}"}


def close_order(po_id: str, confirmed_by: str = "",
                condition: str = "ok", note: str = "") -> dict:
    """The customer confirmed it. Close it.

    An order is not finished when we ship it, it is finished when the person
    who paid says the right thing arrived in one piece.

    Args:
        po_id: the order.
        confirmed_by: who said so, in their words.
        condition: ok, damaged, wrong, or missing.
        note: anything they said worth keeping.
    """
    now = datetime.now().isoformat(timespec="seconds")
    condition = (condition or "ok").strip().lower()

    with db.connect() as c:
        row = c.execute("SELECT id FROM deliveries WHERE po_id=?",
                        (po_id,)).fetchone()
    if row is None:
        return {"ok": False,
                "why": "no delivery recorded for that order yet",
                "say": "Do not close an order the carrier has not reported. "
                       "Check where it actually is first."}

    with db.txn() as c:
        c.execute(
            """UPDATE deliveries
               SET checked_in_at=?, confirmed_by=?, condition=?, note=?
               WHERE id=?""",
            (now, confirmed_by or None, condition, note or None, row["id"]))
    # NOT a new status on the order.
    #
    # purchase_orders.status is constrained to the carrier's vocabulary:
    # draft, confirmed, picked, shipped, delivered, cancelled. "Closed" is not
    # a shipping state and does not belong in that list. What actually closes
    # an order is a person confirming they have it, and that fact lives on the
    # delivery row where the rest of the delivery lives. open_orders reads
    # checked_in_at for exactly this reason.

    if condition != "ok":
        return {
            "ok": True, "order": po_id, "closed": False,
            "condition": condition,
            "say": ("Do NOT close this and do not argue about it on the "
                    "phone. Say plainly that it should not have arrived like "
                    "that, that you are raising it now, and that somebody "
                    "will ring them back with what we are doing about it. "
                    "Then raise it."),
        }

    return {"ok": True, "order": po_id, "closed": True,
            "say": "Thank them and let them go. The order is finished."}


def open_orders(dealer_id: str = "") -> dict:
    """Orders that went out and were never confirmed as landed.

    The list somebody should work through: everything we believe we delivered
    where nobody has heard from the customer since.
    """
    dealer_id = the_desk(dealer_id)
    where = "p.status IN ('confirmed','delivered')"
    params: list = []
    if dealer_id:
        where += " AND a.dealer_id = ?"
        params.append(dealer_id)

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT p.id, p.status, p.placed_at, p.subtotal,
                       a.name account, d.delivered_on, d.checked_in_at
                FROM purchase_orders p
                JOIN accounts a ON a.id = p.account_id
                LEFT JOIN deliveries d ON d.po_id = p.id
                WHERE {where} AND (d.checked_in_at IS NULL)
                ORDER BY p.placed_at""", params).fetchall()

    return {"open": len(rows), "orders": [dict(r) for r in rows]}
