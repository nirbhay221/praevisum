"""Customers who come to us, and the ones we should never ask to.

Two ways a service call can end. A technician drives out, which the rest of
this system already does properly, or the customer brings the thing in. The
second one did not exist at all.

THE RULE THAT MATTERS

A restaurant with nine machines is not carrying a walk-in cooler to a trade
counter. Offering it is not merely useless, it tells them we have not looked
at their account, which is the exact opposite of what this product is for: the
whole pitch is that we know who is calling before we speak.

So walk-in is only ever offered to somebody it could actually work for. That
turns out to need no new data. It is already in the book:

    person accounts    all 14 hold exactly one machine
    business accounts  92, holding one to fourteen
    trade_terms        'net 30' or 'card on file', i.e. an account customer

A residential caller with a single machine can plausibly put it in a car. A
trade account with six sites cannot, and would be insulted by the question.

Everything here reuses what was already built: the same distance and drive
time helpers the dispatcher uses, and the existing stock tables to answer
whether the part is actually on that shelf.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from . import db
from .tenancy import the_desk
from .roads import legs_to

# Above this many machines, a customer is running an operation rather than
# owning an appliance, and carrying one to a counter stops being realistic.
MANY_MACHINES = 2

# How far somebody will plausibly drive to a trade counter. Beyond this,
# offering it wastes their time even if they only own one machine.
MAX_REASONABLE_MILES = 35.0


def _nid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


# Terms that mean somebody is invoiced, which means a trade relationship.
# "Card on file" is not one of them: it means they pay at the point of sale,
# which is the opposite signal and describes exactly the customer a counter
# exists for. Treating any trade_terms value as a trade account made the rule
# refuse everybody, because in this book every account has some payment term.
_CREDIT_TERMS = ("net", "invoice", "account", "eom", "monthly")


def _is_credit_account(terms: str | None) -> bool:
    t = (terms or "").strip().lower()
    return bool(t) and any(w in t for w in _CREDIT_TERMS)


def walk_in_suitable(account_id: str = "") -> dict:
    """Whether this customer should be offered the counter at all.

    Returns the reasoning as well as the answer, because the agent needs to
    know why in order to say the right next thing. A trade account should hear
    "we will come to you", not silence where an option used to be.
    """
    with db.connect() as c:
        acct = c.execute(
            """SELECT a.id, a.kind, a.name, a.trade_terms,
                      (SELECT COUNT(*) FROM assets ast
                       JOIN sites s ON s.id = ast.site_id
                       WHERE s.account_id = a.id AND ast.retired_on IS NULL) machines,
                      (SELECT COUNT(*) FROM sites s WHERE s.account_id = a.id) sites
               FROM accounts a WHERE a.id = ?""", (account_id,)).fetchone()

    if acct is None:
        return {"offer": False, "why": "unknown account"}

    if _is_credit_account(acct["trade_terms"]):
        return {"offer": False, "machines": acct["machines"],
                "why": f"account customer on {acct['trade_terms']}, we go to them",
                "say": "Do not mention the counter. Tell them we will come out."}

    if acct["kind"] == "business" and acct["machines"] > MANY_MACHINES:
        return {"offer": False, "machines": acct["machines"],
                "why": f"business running {acct['machines']} machines across "
                       f"{acct['sites']} site(s)",
                "say": "Do not mention the counter. Somebody with this much "
                       "equipment is not carrying it anywhere."}

    if acct["sites"] > 1:
        return {"offer": False, "machines": acct["machines"],
                "why": "more than one site",
                "say": "Do not mention the counter."}

    return {"offer": True, "machines": acct["machines"],
            "why": "single site, few machines",
            "say": "The counter is a genuine option here. Offer it alongside a "
                   "visit, do not push it, and let them choose."}


def nearest_branch(site_id: str = "", dealer_id: str = "") -> dict:
    """The closest counter to a customer, with how far it actually is.

    Uses the same distance and drive time the dispatcher uses for technicians,
    so a customer is never told a branch is close by a different measure than
    the one that decides whether we drive to them.

    Args:
        site_id: where the customer is.
        dealer_id: whose branches.
    """
    dealer_id = the_desk(dealer_id)
    with db.connect() as c:
        site = c.execute(
            "SELECT id, label, address, lat, lon, account_id FROM sites WHERE id=?",
            (site_id,)).fetchone()
        if site is None:
            return {"ok": False, "why": "unknown site"}

        rows = c.execute(
            """SELECT * FROM branches
               WHERE dealer_id = ? AND has_counter = 1""", (dealer_id,)).fetchall()

    if not rows:
        return {"ok": False, "why": "this dealer has no trade counter"}
    if site["lat"] is None:
        return {"ok": False, "why": "we have no location for that site"}

    # Measured the same way the dispatcher measures a technician, which is
    # the whole point of this function living beside that one: a customer is
    # never told a counter is close by a different yardstick than the one that
    # decides whether we drive to them.
    known = [b for b in rows if b["lat"] is not None]
    legs = legs_to((site["lat"], site["lon"]),
                   [(b["lat"], b["lon"]) for b in known])

    out = []
    for b, leg in zip(known, legs):
        d = leg["miles"]
        out.append({
            "branch_id": b["id"], "label": b["label"], "address": b["address"],
            "phone": b["phone_e164"],
            "distance_mi": d, "drive_minutes": leg["minutes"],
            "opens": _clock(b["opens_min"]), "closes": _clock(b["closes_min"]),
            # None means we could not measure it. Unknown is not near.
            "too_far": d is None or d > MAX_REASONABLE_MILES,
        })
    if not out:
        return {"ok": False, "why": "no branch has a location on file"}

    out.sort(key=lambda b: b["distance_mi"])
    nearest = out[0]
    return {
        "ok": True,
        "site": site["label"],
        "branches": out[:3],
        "nearest": nearest,
        "advice": ("That is a long way. Do not push the counter, lead with a "
                   "visit." if nearest["too_far"] else
                   "Say the distance and the drive time honestly, and let them "
                   "decide. Never imply the counter is faster than a visit "
                   "unless the diary actually says so."),
    }


def _clock(minutes: int | None) -> str:
    if minutes is None:
        return ""
    h, m = divmod(int(minutes), 60)
    suffix = "am" if h < 12 else "pm"
    hour = h % 12 or 12
    return f"{hour}{suffix}" if m == 0 else f"{hour}:{m:02d}{suffix}"


def counter_slots(branch_id: str = "", days: int = 5) -> dict:
    """When the counter is open, so a time can be agreed rather than guessed.

    A counter is not a diary. Nobody is booked out, so this is opening hours
    rather than availability: the honest thing to offer is a window they can
    turn up in, not a slot that implies somebody is waiting for them.
    """
    with db.connect() as c:
        b = c.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
    if b is None:
        return {"ok": False, "why": "unknown branch"}

    open_days = {int(d) for d in (b["open_days"] or "").split(",") if d.strip()}
    today = datetime.now()
    windows = []
    for i in range(days):
        day = today + timedelta(days=i)
        if day.weekday() not in open_days:
            continue
        windows.append({
            "date": day.date().isoformat(),
            "day": day.strftime("%A"),
            "from": _clock(b["opens_min"]),
            "to": _clock(b["closes_min"]),
        })

    return {"ok": True, "branch": b["label"], "address": b["address"],
            "open_windows": windows,
            "note": "These are opening hours, not reserved slots. Say they can "
                    "come any time in the window."}


def book_counter_slot(branch_id: str = "", account_id: str = "",
                      slot_at: str = "",
                      reason: str = "", asset_id: str = "",
                      work_order_id: str = "", contact_id: str = "",
                      call_id: str = "", dealer_id: str = "") -> dict:
    """Write down that somebody is coming in, so the counter expects them.

    Refuses if the branch is shut that day, because a booking for a closed
    counter is worse than no booking: the customer drives out and finds a
    locked door, which is the walk-in version of a failed first visit.

    Args:
        branch_id: which counter.
        account_id: the customer.
        slot_at: ISO date and time they intend to come.
        reason: what they are bringing and why, in their words.
        asset_id: the machine, if it is one of theirs.
        work_order_id: the job, if one is open.
        contact_id: who is coming.
        call_id: the call this came from.
        dealer_id: whose counter.
    """
    dealer_id = the_desk(dealer_id)
    try:
        when = datetime.fromisoformat(slot_at)
    except ValueError:
        return {"ok": False, "why": "unreadable time"}

    with db.txn() as c:
        b = c.execute("SELECT * FROM branches WHERE id=? AND dealer_id=?",
                      (branch_id, dealer_id)).fetchone()
        if b is None:
            return {"ok": False, "why": "unknown branch"}
        if not b["has_counter"]:
            return {"ok": False, "why": "that site has no trade counter",
                    "advice": "Do not send anybody there. Offer a visit."}

        open_days = {int(d) for d in (b["open_days"] or "").split(",") if d.strip()}
        if when.weekday() not in open_days:
            return {"ok": False,
                    "why": f"{b['label']} is closed on "
                           f"{when.strftime('%A')}s",
                    "advice": "Offer a day it is actually open. A customer who "
                              "drives to a locked door is worse off than one "
                              "who was told nothing."}

        mins = when.hour * 60 + when.minute
        if not (b["opens_min"] <= mins <= b["closes_min"]):
            return {"ok": False,
                    "why": f"{b['label']} is open "
                           f"{_clock(b['opens_min'])} to {_clock(b['closes_min'])}",
                    "advice": "Offer a time inside opening hours."}

        bid = _nid("CB")
        c.execute(
            """INSERT INTO counter_bookings
               (id,dealer_id,branch_id,account_id,contact_id,asset_id,
                work_order_id,from_call,slot_at,reason,booked_at,status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'booked')""",
            (bid, dealer_id, branch_id, account_id or None, contact_id or None,
             asset_id or None, work_order_id or None, call_id or None,
             when.isoformat(timespec="minutes"), reason or None,
             datetime.now().isoformat(timespec="seconds")))

    from . import events
    events.publish(dealer_id, "counter",
                   text=f"walk-in booked at {b['label']} "
                        f"{when.strftime('%A %H:%M')}")

    return {
        "ok": True, "booking_id": bid, "branch": b["label"],
        "address": b["address"], "phone": b["phone_e164"],
        "when": when.strftime("%A %d %B, %H:%M"),
        "told_caller": "Read the address back and the day. Tell them to bring "
                       "the machine and, if they have it, the model number off "
                       "the data plate.",
    }
