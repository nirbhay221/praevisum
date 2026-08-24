"""What customers tell us about the things they bought.

Split out of ops.py. A service call only captures what breaks badly enough
to send a van. Everything else a customer says about a machine used to be
heard on a phone call and thrown away.
"""


from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from . import db
from .domain.geo import drive_minutes, miles
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

# WHAT CUSTOMERS TELL US ABOUT WHAT THEY BOUGHT
# ==========================================================================

# Below this many units in service we do not have an opinion worth having.
# The old ranking called a machine "recommended" on the strength of one
# install with no faults, which is not evidence, it is a sample of one wearing
# a confident sentence.







def register_complaint(manufacturer: str, what: str, model_number: str = "",
                       account_id: str = "", asset_id: str = "",
                       category: str = "", severity: str = "minor",
                       call_id: str = "", dealer_id: str = "D-REF") -> dict:
    """Write down a customer's complaint about a machine, in their own words.

    Not every gripe is a service call. "It is deafening", "the door seal is
    flimsy", "the parts cost a fortune" never generate a van, so until now they
    were said out loud on a call and then lost. They are also exactly what the
    next customer weighing that machine wants to hear.

    Args:
        manufacturer: the make.
        what: what they actually said. Their phrasing, not a summary.
        model_number: the model if they have it.
        account_id: which customer.
        asset_id: the specific machine, if it is one of theirs.
        category: reliability, noise, design, running_cost, parts_cost,
            support or install.
        severity: minor, major, or unusable.
        call_id: the call it came from.
        dealer_id: whose book this belongs to.
    """
    if not (manufacturer or "").strip() or not (what or "").strip():
        return {"ok": False, "why": "need a make and what they said"}

    severity = (severity or "minor").strip().lower()
    if severity not in {"minor", "major", "unusable"}:
        severity = "minor"

    model = (model_number or "").strip()
    family = None
    with db.txn() as c:
        if asset_id:
            row = c.execute(
                "SELECT manufacturer, model_number, family FROM assets WHERE id=?",
                (asset_id,)).fetchone()
            if row:
                manufacturer = manufacturer or row["manufacturer"]
                model = model or row["model_number"]
                family = row["family"]

        cid = _nid("CMP")
        c.execute(
            """INSERT INTO complaints
               (id,dealer_id,account_id,asset_id,manufacturer,model_number,
                family,from_call,what,category,severity,raised_at,status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
            (cid, dealer_id, account_id or None, asset_id or None,
             manufacturer.strip(), model or "unknown", family,
             call_id or None, what.strip(), (category or "").strip() or None,
             severity, datetime.now().isoformat(timespec="seconds")))

    return {
        "ok": True, "complaint_id": cid,
        "recorded": f"{manufacturer} {model or ''}".strip(),
        "severity": severity,
        "told_caller": "Say it is on the record against that model, and that it "
                       "will be taken into account when we advise other "
                       "customers. Do not promise a refund, a replacement or a "
                       "callback unless a tool said so.",
    }


def complaints_about(manufacturer: str, model_number: str = "") -> dict:
    """What our customers have said about a machine, with the sample size.

    Reported next to how many we have in service, because a count on its own
    misleads in both directions. Three complaints out of forty is reassuring.
    Three out of four is a warning. The bare number three is neither.
    """
    where = "manufacturer = ?"
    params: list = [manufacturer]
    if model_number:
        where += " AND model_number = ?"
        params.append(model_number)

    with db.connect() as c:
        rows = c.execute(
            f"""SELECT what, category, severity, raised_at
                FROM complaints
                WHERE {where} AND status <> 'withdrawn'
                ORDER BY CASE severity WHEN 'unusable' THEN 0
                                       WHEN 'major' THEN 1 ELSE 2 END,
                         raised_at DESC
                LIMIT 8""", params).fetchall()
        units = c.execute(
            f"""SELECT COALESCE(SUM(units),0) n FROM model_supplied
                WHERE {where}""", params).fetchone()["n"]

    if not rows:
        return {
            "manufacturer": manufacturer, "model": model_number,
            "complaints": 0, "units_in_service": units,
            "say": (f"Nobody has complained to us about that one, and we have "
                    f"{units} of them in service."
                    if units >= MIN_SAMPLE else
                    "No complaints on record, but we have too few of them in "
                    "service for that to mean much."),
        }

    return {
        "manufacturer": manufacturer, "model": model_number,
        "complaints": len(rows), "units_in_service": units,
        "in_their_words": [
            {"said": r["what"], "about": r["category"], "severity": r["severity"]}
            for r in rows],
        "say": (f"{len(rows)} of our customers have raised something about that "
                f"model, out of {units} in service." if units else
                f"{len(rows)} complaints on record."),
    }


# ==========================================================================
