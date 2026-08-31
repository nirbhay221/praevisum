"""What the weather is going to do to the machines.

WHY A REFRIGERATION DESK CARES ABOUT THE FORECAST

Not small talk. A condenser rejects heat into the air around it, so the hotter
that air is, the harder it works to hold the same box temperature. A machine
that is marginal at 70F -- a partly blocked condenser, a tired fan motor, a
slightly low charge -- is often fine until the first properly hot afternoon,
and then it is not. The trade has always known this; every service manager
knows the phone rings on the hot days.

That makes a forecast an operational input rather than a pleasantry. It shifts
two things:

  WHO TO RING FIRST. outreach already finds customers whose own complaint
  matches what preceded a failure elsewhere. A machine flagged as marginal is
  a different proposition on the Tuesday before a 95F weekend than it is in
  October, and the honest thing is to ring them before the weekend rather than
  after they have lost a service.

  WHAT TO EXPECT. A heat run means more calls, and a desk that knows it is
  coming can say so to the person planning the rota.

WHERE THE DATA COMES FROM

api.weather.gov, the National Weather Service. Free, no key, no rate limit
worth worrying about, and it wants a User-Agent identifying the caller, which
is their published condition of use rather than a nicety.

It is US only. That is the right trade for a desk in the Quad Cities and it is
stated here rather than discovered later: if this ever answered a phone
outside the United States, this file returns nothing and the desk carries on
without it, which is the correct failure.

WHAT THIS DELIBERATELY DOES NOT DO

It does not predict a failure from the weather. Heat is a stressor, not a
cause, and "it is hot so your freezer will break" is exactly the kind of
confident nonsense this desk exists to avoid. It raises the urgency of a
machine that is ALREADY showing symptoms. Nothing more.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

# NWS asks that callers identify themselves. This is their stated condition
# of use, not politeness.
AGENT = "Praevisum service desk (commercial refrigeration dispatch)"

# Above this, condensers are working hard enough that a marginal machine is
# meaningfully more likely to give up. Not a cliff, a threshold chosen to be
# defensible: US commercial refrigeration is generally rated to hold at 90F
# ambient, so a forecast at or above it is outside design conditions.
HARD_ON_MACHINES_F = 90
WARM_F = 84

_CACHE: dict[str, tuple[datetime, dict]] = {}
CACHE_FOR = timedelta(hours=2)


def _get(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": AGENT})
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            TimeoutError, OSError) as e:
        print(f"[weather] could not reach the forecast: "
              f"{type(e).__name__}: {e}", flush=True)
        return None


def forecast(lat: float, lon: float) -> dict:
    """The next few days where a site actually is.

    Args:
        lat: latitude of the site.
        lon: longitude of the site.
    """
    if not lat or not lon:
        return {"ok": False, "why": "we do not hold coordinates for that site"}

    key = f"{round(lat, 3)},{round(lon, 3)}"
    hit = _CACHE.get(key)
    if hit and datetime.now() - hit[0] < CACHE_FOR:
        return hit[1]

    point = _get(f"https://api.weather.gov/points/{lat},{lon}")
    if not point:
        return {"ok": False, "why": "the forecast service did not answer"}

    url = (point.get("properties") or {}).get("forecast")
    if not url:
        return {"ok": False, "why": "no forecast published for that location"}

    data = _get(url)
    if not data:
        return {"ok": False, "why": "the forecast service did not answer"}

    periods = []
    for p in (data.get("properties") or {}).get("periods", [])[:8]:
        periods.append({
            "when": p.get("name"),
            "daytime": bool(p.get("isDaytime")),
            "temp_f": p.get("temperature"),
            "summary": p.get("shortForecast"),
        })

    days = [p for p in periods if p["daytime"] and p["temp_f"] is not None]
    peak = max((p["temp_f"] for p in days), default=None)
    hot = [p for p in days if (p["temp_f"] or 0) >= HARD_ON_MACHINES_F]

    out = {
        "ok": True,
        "periods": periods,
        "peak_f": peak,
        "hard_days": [p["when"] for p in hot],
        "source": "National Weather Service, api.weather.gov",
    }
    _CACHE[key] = (datetime.now(), out)
    return out


def pressure_on_machines(lat: float, lon: float) -> dict:
    """Whether the next few days are hard on refrigeration, and how hard.

    Used to raise the urgency of a machine ALREADY showing symptoms. It never
    claims the weather will break anything.
    """
    f = forecast(lat, lon)
    if not f.get("ok"):
        return {"ok": False, "why": f.get("why")}

    peak = f.get("peak_f")
    hard = f.get("hard_days") or []

    if hard:
        level, why = "high", (
            f"{len(hard)} day(s) at or above {HARD_ON_MACHINES_F}F "
            f"({', '.join(hard)}), which is outside what commercial "
            "refrigeration is rated to hold at")
    elif peak and peak >= WARM_F:
        level, why = "raised", (
            f"peaking at {peak}F, warm enough that a condenser already "
            "struggling will struggle more")
    else:
        level, why = "normal", (
            f"peaking at {peak}F" if peak else "nothing unusual forecast")

    return {
        "ok": True, "level": level, "peak_f": peak,
        "hard_days": hard, "why": why,
        "source": f["source"],
        "say": ("Weather raises the urgency of a machine that is ALREADY "
                "showing symptoms. Never tell a customer the heat will break "
                "their machine: it is a stressor, not a cause, and saying so "
                "is a prediction we cannot support."),
    }


def where_they_are(site_id: str) -> tuple[float, float]:
    """The coordinates we hold for a site, if any."""
    from . import db

    try:
        with db.connect() as c:
            row = c.execute("SELECT lat, lon FROM sites WHERE id = ?",
                            (site_id,)).fetchone()
        if row and row["lat"] and row["lon"]:
            return float(row["lat"]), float(row["lon"])
    except Exception:
        pass
    return 0.0, 0.0
