"""Turning an address somebody said out loud into a point on a map.

WHY THIS EXISTS

`next_available_slot` orders technicians by drive time, so it needs the site's
latitude and longitude, and it refuses outright when there is none:

    {'ok': False, 'why': 'site has no location on file'}

Every seeded site has coordinates because the seed script wrote them. Every
site created by a real phone call has none, because `confirm_details` stored
the address as text and nothing ever turned it into a point.

So no customer who rang for the first time could ever be given an appointment.
It was found on the first real call a new customer made: the desk asked when
they were free, called the scheduler six times over ninety seconds, got the
same refusal every time, said nothing out loud, and the caller heard silence
until the line dropped.

WHY NOMINATIM

OpenStreetMap's geocoder. Free, no key, no account, and the licence permits
this. It is the same standard the rest of this project's data is held to:
public, checkable, and not dependent on a credential somebody has to be given.

Its usage policy asks for one request a second and a real User-Agent that
identifies the application. Both are honoured here rather than being somebody
else's problem, and results are cached in the database so a repeated address
is asked once ever.

WHAT IT WILL NOT DO

It will not guess. An address that does not resolve returns nothing, and the
site keeps no coordinates rather than being given the middle of the county.
A made-up point would produce drive times that look real and are not, and the
scheduler's whole claim is that the windows it offers exist.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime

from . import db

ENDPOINT = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy asks for an identifying User-Agent. Sending a
# default urllib one gets the request refused, and rightly.
AGENT = "Praevisum service desk (commercial refrigeration dispatch)"

# One request a second, as the policy asks. Kept as a floor between calls
# rather than a sleep at the call site, so nothing has to remember.
MIN_GAP = 1.0
_last_call = 0.0

TIMEOUT = 12

# The dealer works one metro. An address that resolves to another state is a
# geocoder mistake or a mis-heard street, not a customer, and a wrong point
# produces drive times that look real. Bounds are generous enough to cover the
# Quad Cities and the country around them.
LAT_RANGE = (40.5, 42.6)
LON_RANGE = (-91.6, -89.2)


def _cached(address: str) -> tuple[float, float] | None:
    try:
        with db.connect() as c:
            row = c.execute(
                "SELECT lat, lon FROM geocodes WHERE query = ?",
                (address.strip().lower(),)).fetchone()
        if row and row["lat"] is not None:
            return row["lat"], row["lon"]
    except Exception:
        pass
    return None


def _remember(address: str, lat, lon, note: str) -> None:
    try:
        with db.txn() as c:
            c.execute(
                """INSERT OR REPLACE INTO geocodes
                   (query, lat, lon, note, looked_up_at) VALUES (?,?,?,?,?)""",
                (address.strip().lower(), lat, lon, note,
                 datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        print(f"[geo] could not cache {address!r}: {type(e).__name__}: {e}",
              flush=True)


def locate(address: str, city: str = "Davenport", state: str = "IA") -> dict:
    """Find the point for an address, or say plainly that we could not.

    Args:
        address: what the customer said, in their words.
        city: assumed when they did not say one.
        state: likewise.
    """
    global _last_call

    address = (address or "").strip()
    if not address:
        return {"ok": False, "why": "no address to look up"}

    # Only fill in a town when they did not give one. Appending the dealer's
    # own city to "1401 River Dr, Moline" produces "Moline, Davenport, IA",
    # which resolves to nothing at all. A comma means they already said where.
    query = address
    if city and "," not in address:
        query = f"{address}, {city}, {state}"

    hit = _cached(query)
    if hit:
        return {"ok": True, "lat": hit[0], "lon": hit[1], "from": "cache"}

    gap = time.time() - _last_call
    if gap < MIN_GAP:
        time.sleep(MIN_GAP - gap)
    _last_call = time.time()

    # Try what they said, then the street on its own.
    #
    # A caller who says "2200 East 53rd Street, Bettendorf" when that street is
    # in Davenport gets no match, and the desk told them their address "does
    # not seem to be a real place". It is a real place; they named the wrong
    # town, which is an ordinary thing to do about a metro that runs across
    # two states and four cities. The street alone resolves fine.
    #
    # Dropping their town and keeping the number and street is the right way
    # round: the street is the bit they are certain of.
    attempts = [query]
    street = address.split(",")[0].strip()
    if street and street != query:
        attempts.append(f"{street}, {city}, {state}" if city else street)

    rows = []
    for attempt in attempts:
        url = f"{ENDPOINT}?" + urllib.parse.urlencode(
            {"q": attempt, "format": "json", "limit": 1, "countrycodes": "us"})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                rows = json.load(r)
        except Exception as e:
            # Not cached: a network failure is not an answer, and caching it
            # would turn one bad minute into a permanently unbookable customer.
            return {"ok": False,
                    "why": f"the lookup did not answer ({type(e).__name__})"}
        if rows:
            break

    if not rows:
        _remember(query, None, None, "no match")
        return {
            "ok": False,
            "why": "we could not place that address on a map",
            "say": "Do NOT tell them their address is not a real place. Say we "
                   "could not find it on our map and read back what you have, "
                   "so they can correct it. And do not guess a town for them: "
                   "offering Moline because Bettendorf did not work is how you "
                   "end up sending a van to the wrong state.",
        }

    lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1]
            and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        # Refused rather than stored. A point in another state would order the
        # technicians by a drive nobody is going to make.
        return {"ok": False,
                "why": "that resolved to somewhere outside the area this "
                       "dealer covers, so it is more likely a mis-heard "
                       "street than a customer",
                "resolved_to": rows[0].get("display_name", "")[:80]}

    _remember(query, lat, lon, rows[0].get("display_name", "")[:200])
    return {"ok": True, "lat": lat, "lon": lon,
            "matched": rows[0].get("display_name", "")[:120],
            "from": "nominatim"}
