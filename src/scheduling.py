"""When a technician can genuinely be on site, and holding the slot.

Split out of ops.py, which had grown to 1,346 lines holding six unrelated
jobs. Nothing here changed; it moved. A promise the model invented is not a
promise, so every window returned here comes from real working hours, a real
diary and a real drive time.
"""


from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from . import db
from .cover import can_work_on, suits_customer
from .roads import legs_to
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


# ==========================================================================
# WHEN CAN SOMEONE ACTUALLY COME
# ==========================================================================

def next_available_slot(asset_id: str = "", urgency: str = "normal") -> dict:
    """Find the soonest a qualified technician can genuinely be on site.

    Checks three things a guessed answer skips: whether the technician is
    qualified on that equipment family, whether they are already booked, and
    how far they have to drive. Returns real windows or an honest nothing.

    WHY asset_id IS OPTIONAL, WHICH IS NOT A DETAIL.

    It used to be required. The scheduling agent is a sub-agent, invoked with
    a sentence like "tomorrow" and no ids, so it could not satisfy a required
    argument and therefore COULD NOT CALL THIS AT ALL. Unable to call the one
    tool that answers the question, it did the only other thing available and
    asked the customer -- for a brand and model, then for an address -- all of
    which we hold, and all of which its own instruction forbids in capitals.

    It was not ignoring the rule. It had no legal move that obeyed it.

    Left blank, the guard fills it from the job opened on this call, so the
    right move is now: call this with nothing and let the desk supply the id.

    Args:
        asset_id: the machine that needs attention. Leave it blank on a live
            call: it is filled in from the job already opened.
        urgency: "emergency" searches today only, "normal" searches a week.

    Returns:
        Up to three offerable windows, nearest technician first, with the
        drive time that justifies the order.
    """
    if not (asset_id or "").strip():
        # The guard fills this in on a live call. Reaching here with it still
        # empty means there is no job open yet, and the answer is to say so
        # rather than to send the agent back to the customer for a database
        # key they have never seen.
        return {"ok": False,
                "slots": [],
                "why": "no machine given and no job open on this call yet",
                "say": "Do NOT ask them for a brand, a model, an address or "
                       "any kind of id. Open the work order first with what "
                       "they told you was wrong, then ask again: the machine "
                       "and the address come from their account."}

    with db.connect() as c:
        asset = c.execute(
            """SELECT ast.id, ast.family, ast.manufacturer, ast.model_number,
                      s.id site_id, s.label site, s.lat, s.lon
               FROM assets ast JOIN sites s ON s.id = ast.site_id
               WHERE ast.id = ?""", (asset_id,)).fetchone()
        if asset is None:
            return {"ok": False, "why": "unknown machine"}
        if asset["lat"] is None:
            return {"ok": False, "why": "site has no location on file"}

        techs = c.execute(
            """SELECT t.id, t.name, t.lat, t.lon, t.home_base
               FROM technicians t JOIN technician_skills k ON k.technician_id = t.id
               WHERE t.active = 1 AND k.family = ?""", (asset["family"],)).fetchall()
        if not techs:
            return {"ok": False, "why": f"nobody is qualified on {asset['family']}",
                    "advice": "Do NOT offer a slot you cannot staff. Call "
                              "escalate.raise_it so a named person picks it up "
                              "with a time on it. Saying a supervisor will "
                              "call back, with no name and nothing recorded, "
                              "is a shrug with a job title on it."}

        horizon = 1 if urgency == "emergency" else 7
        now = datetime.now()
        offers: list[dict] = []
        # Slots that existed and were not offerable, kept so the desk can say
        # WHY nobody is free rather than only that nobody is.
        declined: list[dict] = []

        # ROAD DISTANCE, IN ONE MATRIX FOR THE WHOLE SHORTLIST.
        #
        # Travel time here is not a display field: it sets the earliest slot
        # that can be offered. Understating it by the width of a river books
        # somebody an appointment their engineer cannot reach.
        #
        # One call for every technician rather than one call each, because
        # Compute Route Matrix bills origins times destinations. The list is
        # already filtered to people qualified for the job, so nobody
        # ineligible is measured.
        legs = legs_to((asset["lat"], asset["lon"]),
                       [(t["lat"], t["lon"]) for t in techs])

        for t, leg in zip(techs, legs):
            d = leg["miles"]
            travel = leg["minutes"]

            hours = {r["dow"]: (r["start_min"], r["end_min"]) for r in c.execute(
                "SELECT dow, start_min, end_min FROM technician_hours WHERE technician_id=?",
                (t["id"],))}
            booked = [(datetime.fromisoformat(r["starts_at"]),
                       datetime.fromisoformat(r["ends_at"]))
                      for r in c.execute(
                          """SELECT starts_at, ends_at FROM appointments
                             WHERE technician_id=? AND ends_at >= ?""",
                          (t["id"], now.isoformat(timespec="minutes")))]

            found = None
            turned_away = ""
            for day in range(horizon):
                d0 = (now + timedelta(days=day)).date()
                window = hours.get(d0.weekday())
                if not window:
                    continue
                start_min, end_min = window

                cursor = datetime.combine(d0, datetime.min.time()) + timedelta(minutes=start_min)
                if day == 0:
                    earliest = now + timedelta(minutes=travel + 15)
                    if cursor < earliest:
                        cursor = earliest.replace(second=0, microsecond=0)
                day_end = datetime.combine(d0, datetime.min.time()) + timedelta(minutes=end_min)

                while cursor + timedelta(minutes=VISIT_MINUTES) <= day_end:
                    fin = cursor + timedelta(minutes=VISIT_MINUTES)
                    clash = next(
                        (b for b in booked
                         if cursor < b[1] + timedelta(minutes=BUFFER_MINUTES)
                         and fin + timedelta(minutes=BUFFER_MINUTES) > b[0]), None)
                    if clash is None:
                        # ASK THE CUSTOMER'S OWN HOURS HERE, NOT AFTERWARDS.
                        #
                        # This check used to sit outside the whole search: the
                        # first free window was taken, the loop broken, and
                        # only then was the customer asked whether they could
                        # take somebody. If they could not, the window was
                        # dropped and NOTHING ELSE WAS TRIED -- not later that
                        # day, not tomorrow.
                        #
                        # So a customer who told us they cannot take visits in
                        # the afternoon could never be booked at all. Five
                        # engineers, all free, all qualified, all certified,
                        # and the desk reported that nobody had a slot.
                        #
                        # Asking inside the loop turns their hours into a
                        # constraint the search works WITHIN, which is what a
                        # constraint is for.
                        if suits_customer(asset["site_id"], cursor):
                            found = (cursor, fin)
                            break
                        turned_away = turned_away or _spoken_window(cursor, fin)
                        cursor = fin
                        cursor = cursor.replace(
                            minute=0 if cursor.minute < 30 else 30,
                            second=0, microsecond=0)
                        continue
                    cursor = clash[1] + timedelta(minutes=BUFFER_MINUTES)
                    cursor = cursor.replace(minute=0 if cursor.minute < 30 else 30,
                                            second=0, microsecond=0)
                if found:
                    break

            # Two things the diary alone cannot answer.
            #
            # Whether the customer can take somebody then, which nothing ever
            # asked: a window across a lunch service gets refused, or worse
            # accepted and missed, which spends the truck roll and the
            # relationship at once.
            #
            # And whether this technician is legally permitted to do the job.
            # Skill and certification are different questions and only the
            # first was being asked. EPA 608 is what allows a sealed system to
            # be opened at all, and sending somebody without it is an offence
            # rather than an inefficiency.
            if not found and turned_away:
                # They had time and the customer's own hours ruled it out.
                # Worth saying, because "nobody is free" and "not when you can
                # take us" are different problems with different answers.
                declined.append({"technician": t["name"],
                                 "window": turned_away,
                                 "why": "outside the hours they told us"})

            if found:
                permitted = can_work_on(t["id"], asset["family"],
                                        on=found[0].date().isoformat())
                if not permitted["allowed"]:
                    declined.append({"technician": t["name"],
                                     "why": permitted["why"]})
                    found = None

            if found:
                offers.append({
                    "technician_id": t["id"], "technician": t["name"],
                    "distance_mi": d, "drive_minutes": travel,
                    "starts_at": found[0].isoformat(timespec="minutes"),
                    "ends_at": found[1].isoformat(timespec="minutes"),
                    "window": _spoken_window(found[0], found[1]),
                })

    if not offers:
        return {"ok": False,
                "why": "no qualified technician has a free slot in that window",
                "ruled_out": declined,
                "advice": ("Tell them honestly and offer the next thing you can "
                           "check. If a slot was ruled out because it fell "
                           "outside the hours they gave us, say so and ask "
                           "whether that window has changed.")}

    offers.sort(key=lambda o: (o["starts_at"], o["drive_minutes"]))
    return {"ok": True, "site": asset["site"],
            "machine": f"{asset['manufacturer']} {asset['model_number']}",
            "family": asset["family"], "offers": offers[:3],
            "advice": "Offer the first one. These are real gaps in a real diary, "
                      "so do not adjust the times to sound better."}


def hold_slot(asset_id: str = "", technician_id: str = "",
              starts_at: str = "",
              work_order_id: str = "") -> dict:
    """Put a booking in the diary so nobody else is offered the same slot.

    Written inside one transaction, so two callers cannot be given the same
    window by two concurrent calls.
    """
    try:
        start = datetime.fromisoformat(starts_at)
    except ValueError:
        return {"ok": False, "why": "unreadable time"}
    end = start + timedelta(minutes=VISIT_MINUTES)

    with db.txn() as c:
        clash = c.execute(
            """SELECT id FROM appointments WHERE technician_id=?
               AND starts_at < ? AND ends_at > ?""",
            (technician_id, end.isoformat(timespec="minutes"),
             start.isoformat(timespec="minutes"))).fetchone()
        if clash:
            return {"ok": False, "why": "that slot was taken while we were talking",
                    "advice": "Apologise briefly, get a fresh slot, offer that."}

        site = c.execute("SELECT site_id FROM assets WHERE id=?", (asset_id,)).fetchone()
        appt = _nid("AP")
        c.execute("""INSERT INTO appointments
                     (id,technician_id,starts_at,ends_at,kind,site_id,note)
                     VALUES (?,?,?,?,?,?,?)""",
                  (appt, technician_id, start.isoformat(timespec="minutes"),
                   end.isoformat(timespec="minutes"), "visit",
                   site["site_id"] if site else None,
                   f"booked on a call for {work_order_id or 'a new job'}"))
    return {"ok": True, "appointment_id": appt,
            "confirmed": start.strftime("%A %d %B, %H:%M")}


# ==========================================================================
