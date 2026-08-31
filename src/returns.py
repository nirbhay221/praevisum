"""Things coming back.

Split out of ops.py. A part coming back is an inventory event; a machine
coming back is evidence about that model. Conflating them lets a customer
who miscounted an order make a good machine look bad.
"""


from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from . import db
from .tenancy import the_desk
from .thresholds import *  # noqa: F401,F403



def _nid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:6].upper()}"


def _spoken_window(start: datetime, end: datetime) -> str:
    """A time window the way a person would say it down the phone.

    Built by hand rather than with strftime because the no-padding directive
    differs between platforms, and this runs on Windows in development and
    Linux in production.
    """
    def clock(t: datetime) -> str:
        hour = t.hour % 12 or 12
        suffix = "am" if t.hour < 12 else "pm"
        return f"{hour}{suffix}" if t.minute == 0 else f"{hour}:{t.minute:02d}{suffix}"

    today = datetime.now().date()
    if start.date() == today:
        day = "today"
    elif start.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = start.strftime("%A")
    return f"{day} {clock(start)} to {clock(end)}"

# THINGS COMING BACK
# ==========================================================================

# A return blamed on the machine is worth more than a complaint. A complaint
# is annoyance; a return is somebody deciding they would rather have nothing.

_MACHINE_FAULT = ("faulty", "not_as_described")


def register_return(kind: str, reason: str, said: str = "",
                    sku: str = "", asset_id: str = "", account_id: str = "",
                    qty: int = 1, condition: str = "unopened",
                    call_id: str = "", dealer_id: str = "") -> dict:
    """Record something coming back, and put it on the shelf if it can go there.

    Two different events share the word "return". A part coming back is stock:
    unopened, it goes straight back and the reorder advice must know, or we buy
    what is already sitting by the door. A machine coming back is evidence
    against that model, and stronger evidence than a complaint.

    Args:
        kind: "part" or "machine".
        reason: faulty, not_as_described, damaged_in_transit, ordered_wrong,
            changed_mind, duplicate, or other.
        said: their words for why, which is what the next customer wants.
        sku: the part, when kind is "part".
        asset_id: the machine, when kind is "machine".
        account_id: who is returning it.
        qty: how many.
        condition: unopened, opened, used or damaged. Only unopened goes back
            on the shelf.
        call_id: the call this came from.
        dealer_id: whose book.
    """
    dealer_id = the_desk(dealer_id)
    kind = (kind or "").strip().lower()
    reason = (reason or "").strip().lower()
    condition = (condition or "unopened").strip().lower()

    if kind not in ("part", "machine"):
        return {"ok": False, "why": 'kind must be "part" or "machine"'}
    if reason not in ("faulty", "not_as_described", "damaged_in_transit",
                      "ordered_wrong", "changed_mind", "duplicate", "other"):
        return {"ok": False, "why": "unrecognised reason", "reason_given": reason}
    if kind == "part" and not sku:
        return {"ok": False, "why": "a part return needs a SKU"}
    if kind == "machine" and not asset_id:
        return {"ok": False, "why": "a machine return needs the machine"}

    rid = _nid("RET")
    manufacturer = model = None
    restocked = 0

    with db.txn() as c:
        if kind == "machine":
            row = c.execute(
                "SELECT manufacturer, model_number FROM assets WHERE id=?",
                (asset_id,)).fetchone()
            if row is None:
                return {"ok": False, "why": "unknown machine"}
            manufacturer, model = row["manufacturer"], row["model_number"]

        # Only unopened stock is sellable again. Putting an opened part back
        # would have the desk promise a technician something nobody can fit.
        if kind == "part" and condition == "unopened" and reason != "damaged_in_transit":
            loc = c.execute(
                """SELECT id FROM stock_locations
                   WHERE dealer_id=? AND kind='warehouse' LIMIT 1""",
                (dealer_id,)).fetchone()
            if loc:
                c.execute(
                    """INSERT INTO stock (sku,location_id,on_hand) VALUES (?,?,?)
                       ON CONFLICT(sku,location_id)
                       DO UPDATE SET on_hand = on_hand + ?""",
                    (sku, loc["id"], qty, qty))
                restocked = 1

        c.execute(
            """INSERT INTO returns
               (id,dealer_id,account_id,from_call,kind,sku,asset_id,
                manufacturer,model_number,qty,reason,said,condition,
                restocked,opened_at,status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
            (rid, dealer_id, account_id or None, call_id or None, kind,
             sku or None, asset_id or None, manufacturer, model, qty,
             reason, said or None, condition, restocked,
             datetime.now().isoformat(timespec="seconds")))

    return {
        "ok": True, "return_id": rid, "kind": kind, "reason": reason,
        "back_on_shelf": bool(restocked),
        "told_caller": (
            "Confirm what is coming back and why, in their words. Do not "
            "promise a refund amount: say it will be confirmed when we have "
            "it back and have looked at it."
            if reason in _MACHINE_FAULT else
            "Confirm what is coming back. Do not promise a refund amount."),
    }


def returns_about(manufacturer: str, model_number: str = "") -> dict:
    """How often a model gets given back, against how many we supplied.

    Reported with the denominator for the same reason complaints are: three
    returns out of forty is noise and three out of four is a verdict, and the
    bare number three is neither.
    """
    where = "manufacturer = ?"
    params: list = [manufacturer]
    if model_number:
        where += " AND model_number = ?"
        params.append(model_number)

    with db.connect() as c:
        row = c.execute(
            f"""SELECT returns, blamed_on_machine, reasons
                FROM model_returns WHERE {where}""", params).fetchone()
        units = c.execute(
            f"""SELECT COALESCE(SUM(units),0) n FROM model_supplied
                WHERE {where}""", params).fetchone()["n"]
        words = [r["said"] for r in c.execute(
            f"""SELECT said FROM returns
                WHERE {where} AND kind='machine' AND said IS NOT NULL
                ORDER BY opened_at DESC LIMIT 4""", params)]

    if row is None:
        return {"manufacturer": manufacturer, "model": model_number,
                "returns": 0, "units_supplied": units,
                "say": "Nobody has given one of those back to us."}

    return {
        "manufacturer": manufacturer, "model": model_number,
        "returns": row["returns"],
        "blamed_on_the_machine": row["blamed_on_machine"],
        "units_supplied": units,
        "reasons": row["reasons"],
        "in_their_words": words,
        "say": (f"{row['blamed_on_machine']} of {units} we supplied came back "
                f"with the machine blamed."
                if row["blamed_on_machine"] else
                f"{row['returns']} came back, none of them blamed on the "
                f"machine itself."),
    }
