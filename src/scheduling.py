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


# ==========================================================================
# WHEN CAN SOMEONE ACTUALLY COME
# ==========================================================================

def next_available_slot(asset_id: str, urgency: str = "normal") -> dict:
    """Find the soonest a qualified technician can genuinely be on site.

    Checks three things a guessed answer skips: whether the technician is
    qualified on that equipment family, whether they are already booked, and
    how far they have to drive. Returns real windows or an honest nothing.

    Args:
        asset_id: the machine that needs attention.
        urgency: "emergency" searches today only, "normal" searches a week.

    Returns:
        Up to three offerable windows, nearest technician first, with the
        drive time that justifies the order.
    """
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
                    "advice": "Say so plainly and offer to have a supervisor call back."}

        horizon = 1 if urgency == "emergency" else 7
        now = datetime.now()
        offers: list[dict] = []

        for t in techs:
            d = miles(asset["lat"], asset["lon"], t["lat"], t["lon"])
            travel = drive_minutes(d)

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
                        found = (cursor, fin)
                        break
                    cursor = clash[1] + timedelta(minutes=BUFFER_MINUTES)
                    cursor = cursor.replace(minute=0 if cursor.minute < 30 else 30,
                                            second=0, microsecond=0)
                if found:
                    break

            if found:
                offers.append({
                    "technician_id": t["id"], "technician": t["name"],
                    "distance_mi": d, "drive_minutes": travel,
                    "starts_at": found[0].isoformat(timespec="minutes"),
                    "ends_at": found[1].isoformat(timespec="minutes"),
                    "window": _spoken_window(found[0], found[1]),
                })

    if not offers:
        return {"ok": False, "why": "no qualified technician has a free slot in that window",
                "advice": "Tell them honestly and offer the next thing you can check."}

    offers.sort(key=lambda o: (o["starts_at"], o["drive_minutes"]))
    return {"ok": True, "site": asset["site"],
            "machine": f"{asset['manufacturer']} {asset['model_number']}",
            "family": asset["family"], "offers": offers[:3],
            "advice": "Offer the first one. These are real gaps in a real diary, "
                      "so do not adjust the times to sound better."}


def hold_slot(asset_id: str, technician_id: str, starts_at: str,
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
