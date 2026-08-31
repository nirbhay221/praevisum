"""Whether a number is a landline, and therefore whether we may ring it.

WHY THIS DECIDES A WHOLE FEATURE

The Telemarketing Sales Rule broadly exempts calls from a marketer to a
business, and the national Do Not Call registry does not reach them. That is
what makes approaching a business we have never met lawful at all.

The exemption stops at the handset. The FCC's position is that an
AI-generated voice is an artificial or prerecorded voice under the TCPA, and
the TCPA treats every WIRELESS number as residential regardless of whose desk
it sits on. There is no business carve-out for a mobile. So this desk may ring
a published business landline and may not ring a mobile, and the difference is
not visible in the number itself: US numbering has not separated mobile from
landline since portability.

The only way to know is to ask a carrier database. That is what this does.

FAIL CLOSED, ALWAYS

Every failure path here returns "mobile". A lookup that times out, a missing
credential, a number the carrier will not identify, a malformed response: all
of them mean we do not know, and not knowing is treated as the answer that
forbids the call. This is the opposite of how a sales tool wants to behave,
which is exactly why it is written down.

WHAT IT COSTS

Twilio charges per lookup, so a result is cached and a number is only paid for
once. Line type does change when a business ports a number, and
RECHECK_AFTER_DAYS is the compromise: long enough not to be a bill, short
enough that a number which became a mobile stops being rung within the quarter.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import db

LOOKUP_API = "https://lookups.twilio.com/v2/PhoneNumbers/"

# Only this one may be rung by an artificial voice under the business
# exemption. Everything else, including VoIP, is refused: a VoIP number can
# terminate on a mobile handset and the carrier data cannot tell us whether it
# does, so it is not a landline for this purpose.
MAY_RING = ("landline",)

RECHECK_AFTER_DAYS = 90

# What we say when we do not know. Deliberately not a third value: an unknown
# that reads as "unknown" invites somebody downstream to treat it as maybe.
UNKNOWN = "mobile"


def _credentials() -> tuple[str, str] | None:
    """The account to look numbers up with.

    THROUGH settings, NOT os.getenv, AND THE DIFFERENCE WAS INVISIBLE.

    This read os.getenv directly. Nothing in this module imports config, and
    config is what calls load_dotenv(), so on any code path that had not
    already imported it the environment was empty, the lookup was skipped, and
    every number came back "mobile" from the fail-closed default.

    That reads as the guard working. It is the guard not running. A real
    published landline was refused exactly like a mobile, prospecting would
    have reached nobody, and no lookup was ever paid for, so the bill gave no
    hint either. Found by asking Twilio directly on the same box and getting a
    clean answer the module could not get.

    Fail-closed hid it. A gate that is safe when broken still has to be
    detectable when broken, which is what `source` in the result is for.
    """
    from .config import settings

    sid = (settings.twilio_account_sid or "").strip()
    token = (settings.twilio_auth_token or "").strip()
    if not sid or not token:
        return None
    return sid, token


def configured() -> bool:
    return _credentials() is not None


def _cached(e164: str) -> dict | None:
    with db.connect() as c:
        row = c.execute(
            "SELECT line_type, carrier, checked_on, source "
            "FROM line_type_cache WHERE e164 = ?", (e164,)).fetchone()
    if row is None:
        return None

    try:
        when = datetime.fromisoformat(row["checked_on"])
    except (TypeError, ValueError):
        return None

    if datetime.now() - when > timedelta(days=RECHECK_AFTER_DAYS):
        return None

    return {"line_type": row["line_type"], "carrier": row["carrier"],
            "source": row["source"], "cached": True}


def _store(e164: str, kind: str, carrier: str, source: str) -> None:
    with db.txn() as c:
        c.execute(
            """INSERT INTO line_type_cache
                 (e164,line_type,carrier,checked_on,source)
               VALUES (?,?,?,?,?)
               ON CONFLICT(e164) DO UPDATE SET
                 line_type=excluded.line_type, carrier=excluded.carrier,
                 checked_on=excluded.checked_on, source=excluded.source""",
            (e164, kind, carrier,
             datetime.now().isoformat(timespec="seconds"), source))


def line_type(e164: str, allow_lookup: bool = True) -> dict:
    """What kind of line this is, according to the carrier database.

    Args:
        e164: the number, in +1XXXXXXXXXX form.
        allow_lookup: false to answer only from cache and never spend money.
    """
    num = (e164 or "").strip()
    if not num.startswith("+"):
        return {"line_type": UNKNOWN, "why": "not an E.164 number",
                "source": "none"}

    hit = _cached(num)
    if hit:
        return hit

    if not allow_lookup:
        return {"line_type": UNKNOWN, "why": "not looked up yet",
                "source": "cache"}

    creds = _credentials()
    if creds is None:
        return {"line_type": UNKNOWN,
                "why": "no lookup credentials, so we cannot tell a landline "
                       "from a mobile and must assume mobile",
                "source": "none"}

    sid, token = creds
    url = (LOOKUP_API + urllib.parse.quote(num)
           + "?Fields=line_type_intelligence")
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()

    try:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            TimeoutError, OSError) as e:
        print(f"[linetype] lookup failed for {num}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"line_type": UNKNOWN, "why": "the lookup did not answer",
                "source": "none"}

    lti = data.get("line_type_intelligence") or {}
    kind = (lti.get("type") or "").strip().lower() or UNKNOWN
    carrier = (lti.get("carrier_name") or "").strip()

    _store(num, kind, carrier, "twilio")
    return {"line_type": kind, "carrier": carrier, "source": "twilio",
            "cached": False}


def on_our_do_not_call(e164: str) -> dict:
    """Whether somebody has told US, specifically, to stop.

    A separate obligation from the federal registry, and it outlives any
    business relationship. Rows are never deleted, because the record of the
    request is the evidence that it was honoured.
    """
    with db.connect() as c:
        row = c.execute(
            "SELECT asked_on, note FROM do_not_call WHERE e164 = ?",
            ((e164 or "").strip(),)).fetchone()
    if row is None:
        return {"listed": False}
    return {"listed": True, "asked_on": row["asked_on"], "note": row["note"]}


def stop_calling(e164: str, asked_by: str = "", heard_on: str = "",
                 note: str = "") -> dict:
    """Record that somebody asked not to be called again.

    Kept for four years, which is the retention the rules require, and never
    removed. A request to stop is the one instruction in this system that
    nothing else may override.
    """
    num = (e164 or "").strip()
    if not num:
        return {"ok": False, "why": "no number"}

    now = datetime.now()
    with db.txn() as c:
        c.execute(
            """INSERT INTO do_not_call
                 (e164,asked_on,asked_by,heard_on,keep_until,note)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(e164) DO NOTHING""",
            (num, now.date().isoformat(), asked_by, heard_on,
             (now + timedelta(days=365 * 4)).date().isoformat(), note))

    return {"ok": True, "e164": num,
            "say": "They are off the list from now on. Do not ring this "
                   "number again for anything that is not a safety matter."}
