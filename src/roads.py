"""Road distance, because this dealer's territory is split by a river.

WHY STRAIGHT-LINE IS NOT GOOD ENOUGH HERE

domain/geo.py measures great-circle distance and turns it into minutes at a
flat 32 mph. That is honest about being crude and it is right often enough in
open country.

It is wrong in the Quad Cities specifically, and this dealer is in the Quad
Cities. The Mississippi runs through the middle of the territory. Engineers
are based on both sides of it (Davenport and Bettendorf in Iowa, Moline, Rock
Island, Silvis, East Moline and Geneseo in Illinois) and so are the customers.

Measured on the live book, a site at 514 River Dr, Rock Island:

    Ben Kalita       Rock Island IL    2.3 mi straight-line, said 10 min
    Marisol Vance    Davenport IA      2.7 mi straight-line, said 11 min

Those two numbers are a minute apart and the real difference is much larger,
because Marisol has to drive to a bridge and back. The ordering happens to
come out right in that example; the MARGIN is fiction, and a fictional margin
is what makes the ordering wrong somewhere else. Dispatch is the one place in
this system where a wrong answer puts a van on the wrong road.

WHAT THIS COSTS, WHICH IS THE REASON IT IS SHAPED THIS WAY

Google's Routes API bills Compute Route Matrix per ELEMENT, where elements are
origins times destinations. Route Matrix Essentials includes 10,000 free
elements a month, then $5 per 1,000.

Eight active engineers against one site is 8 elements per dispatch decision,
so the free tier is about 1,200 decisions a month. A hazard sweep across 26
owners is 208 in one go. That fits, but only because of the cache below: the
same engineer-to-site pair is asked for over and over as jobs repeat at the
same addresses, and paying for it twice is paying for a road that has not
moved.

TRAFFIC_UNAWARE is deliberate. Traffic-aware routing is a more expensive SKU
and its answer expires in minutes, which is useless for a cached leg and for a
visit being booked for Thursday.

AND IT WORKS WITH NO KEY AT ALL

If GOOGLE_MAPS_API_KEY is unset, every function here falls back to the
haversine estimate and says so in `source`. Nothing breaks, no call is made,
and the system behaves exactly as it did before. That is what makes this safe
to ship without a billing account attached.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime

from . import db
from .domain.geo import drive_minutes, miles

ENDPOINT = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

# Roughly 11 metres. Two calls about the same building must hit the same cache
# row, and a technician's home base does not move between jobs.
PRECISION = 4

# How long a cached leg is trusted. Roads change, but not weekly, and this is
# ordering rather than turn-by-turn navigation.
CACHE_DAYS = 90

# Above this many elements the call is refused rather than made. A bug that
# builds a matrix of every technician against every site would spend the whole
# month's free tier in one request, and it would look like a slow function
# rather than a bill.
MAX_ELEMENTS = 400

METRES_PER_MILE = 1609.344


def configured() -> bool:
    """Whether a key is present. Everything degrades without one."""
    return bool(os.getenv("GOOGLE_MAPS_API_KEY", "").strip())


def _round(v: float) -> float:
    return round(float(v), PRECISION)


def _cached(c, o: tuple, d: tuple):
    row = c.execute(
        """SELECT road_miles, road_minutes FROM road_legs
           WHERE from_lat=? AND from_lon=? AND to_lat=? AND to_lon=?
             AND seen_at >= ?""",
        (_round(o[0]), _round(o[1]), _round(d[0]), _round(d[1]),
         _stale_before())).fetchone()
    return (row["road_miles"], row["road_minutes"]) if row else None


def _stale_before() -> str:
    from datetime import timedelta
    return (datetime.now() - timedelta(days=CACHE_DAYS)).isoformat()


def _remember(o: tuple, d: tuple, mi: float, mins: int) -> None:
    try:
        with db.txn() as c:
            c.execute(
                """INSERT OR REPLACE INTO road_legs
                   (from_lat,from_lon,to_lat,to_lon,road_miles,road_minutes,
                    seen_at) VALUES (?,?,?,?,?,?,?)""",
                (_round(o[0]), _round(o[1]), _round(d[0]), _round(d[1]),
                 round(mi, 2), int(mins), datetime.now().isoformat()))
    except Exception as e:
        print(f"[roads] could not cache a leg: {type(e).__name__}: {e}",
              flush=True)


def _straight(o: tuple, d: tuple) -> dict:
    mi = miles(o[0], o[1], d[0], d[1])
    return {"miles": mi, "minutes": drive_minutes(mi), "source": "straight-line"}


def _ask_google(origins: list[tuple], destinations: list[tuple]) -> dict:
    """One matrix call. Returns {(oi, di): (miles, minutes)}, or {} on failure.

    Never raises. A routing service being down must not stop a van being
    sent, it must only make the ordering less precise.
    """
    def waypoint(p):
        return {"waypoint": {"location": {"latLng": {"latitude": p[0],
                                                     "longitude": p[1]}}}}

    body = {
        "origins": [waypoint(p) for p in origins],
        "destinations": [waypoint(p) for p in destinations],
        "travelMode": "DRIVE",
        # Cheapest SKU, and the right one: a cached leg cannot carry live
        # traffic, and a visit being booked for Thursday should not pretend to.
        "routingPreference": "TRAFFIC_UNAWARE",
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": os.getenv("GOOGLE_MAPS_API_KEY", "").strip(),
            "X-Goog-FieldMask": ("originIndex,destinationIndex,duration,"
                                 "distanceMeters,condition"),
        })

    try:
        raw = urllib.request.urlopen(req, timeout=20).read()
        rows = json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        print(f"[roads] routes API refused: {e.code} {detail}", flush=True)
        return {}
    except Exception as e:
        print(f"[roads] routes API unreachable: {type(e).__name__}: {e}",
              flush=True)
        return {}

    out = {}
    for r in rows if isinstance(rows, list) else []:
        if r.get("condition") != "ROUTE_EXISTS":
            continue
        oi, di = r.get("originIndex", 0), r.get("destinationIndex", 0)
        metres = r.get("distanceMeters")
        secs = str(r.get("duration", "0s")).rstrip("s")
        try:
            mi = round(float(metres) / METRES_PER_MILE, 1)
            mins = max(1, int(round(float(secs) / 60)))
        except (TypeError, ValueError):
            continue
        out[(oi, di)] = (mi, mins)
    return out


def legs_to(destination: tuple, origins: list[tuple]) -> list[dict]:
    """Road distance from each origin to one destination, cheapest way possible.

    Cached pairs cost nothing. Only the pairs we have never asked about go in
    the matrix, so a second job at the same restaurant is free.

    Args:
        destination: (lat, lon) of the site.
        origins: (lat, lon) for each technician.
    """
    answers: list[dict | None] = [None] * len(origins)
    unknown: list[int] = []

    if destination and destination[0] is not None:
        with db.connect() as c:
            for i, o in enumerate(origins):
                if o is None or o[0] is None:
                    continue
                hit = _cached(c, o, destination)
                if hit:
                    answers[i] = {"miles": hit[0], "minutes": hit[1],
                                  "source": "road (cached)"}
                else:
                    unknown.append(i)

    if unknown and configured() and len(unknown) <= MAX_ELEMENTS:
        pts = [origins[i] for i in unknown]
        got = _ask_google(pts, [destination])
        for n, i in enumerate(unknown):
            if (n, 0) in got:
                mi, mins = got[(n, 0)]
                answers[i] = {"miles": mi, "minutes": mins, "source": "road"}
                _remember(origins[i], destination, mi, mins)

    # Anything still unanswered falls back, including every origin when there
    # is no key at all.
    for i, o in enumerate(origins):
        if answers[i] is None:
            if o is None or o[0] is None or not destination \
                    or destination[0] is None:
                answers[i] = {"miles": None, "minutes": 999,
                              "source": "no position on file"}
            else:
                answers[i] = _straight(o, destination)

    return answers


def compare(destination: tuple, origins: list[tuple]) -> list[dict]:
    """Both numbers side by side, for showing somebody why this exists.

    Not used in a decision. It is here so the difference between a straight
    line and a road can be put on a screen rather than asserted.
    """
    road = legs_to(destination, origins)
    out = []
    for o, r in zip(origins, road):
        s = _straight(o, destination) if o and o[0] is not None else {
            "miles": None, "minutes": 999}
        out.append({
            "straight_miles": s["miles"], "straight_minutes": s["minutes"],
            "road_miles": r["miles"], "road_minutes": r["minutes"],
            "source": r["source"],
            "minutes_understated_by": (
                r["minutes"] - s["minutes"]
                if r["source"].startswith("road") else None),
        })
    return out
