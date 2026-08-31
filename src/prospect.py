"""Finding businesses that need us and are not customers yet.

THE IDEA, AND WHY IT IS NOT A CALL LIST

The salesman's version of this is a list of every restaurant within twenty
miles and a dialler. That is illegal for this desk and it is also the weaker
idea. What makes a good approach is knowing WHY you are ringing: a business
that has just told the public its ice machine is broken is a different call
from a business picked off a map, and only one of them is worth anybody's
afternoon.

So the unit of work here is not a phone number. It is a number attached to a
piece of public evidence, quoted verbatim, that this business has the problem
we fix. No evidence, no call.

WHAT THE LAW ALLOWS, EXACTLY

The Telemarketing Sales Rule broadly exempts marketer-to-business calls and
the national Do Not Call registry does not reach them, which is what makes
approaching a stranger lawful at all. Three things narrow it hard:

  THE HANDSET. An AI-generated voice is an artificial voice under the TCPA,
  and the TCPA treats every wireless number as residential regardless of whose
  desk it sits on. No business carve-out exists for a mobile. Most small
  restaurants run on a mobile, so most prospects are simply unreachable by
  this desk and get left alone. See linetype.py.

  THE CLOCK. Local time at THEIR address, not ours.

  THE LIST. Our own do-not-call list is a separate obligation from the federal
  registry, survives the end of any relationship, and is kept four years.

Messaging is not a way round any of it. WhatsApp requires explicit prior
opt-in and treats an imported list as grounds for shutting the sender down,
and a Telegram bot cannot open a conversation at all: the user has to message
it first. Both platforms are opt-in by construction, so there is no cold
channel here in any medium.

WHERE THE WORDS COME FROM

The vocabulary that marks a public review as a distress signal is not invented
here. It is taken from `complaints`, which is what OUR OWN customers said when
their equipment was failing, in their own words rather than an engineer's:

    "we are defrosting it by hand more often than we used to"
    "The fan rattles constantly, staff have started unplugging it"
    "You can hear it through the wall in the dining room"

A stranger writing a review about a cafe is writing in that register, not in
the register of a fault code. Deriving the terms from the corpus keeps this
honest, and it means the signal improves as the corpus grows rather than as
somebody edits a list.
"""

from __future__ import annotations

import collections
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, linetype

PLACES_API = "https://google.serper.dev/places"
SEARCH_API = "https://google.serper.dev/search"

# Local time at the business, which is the boundary that applies to
# telemarketing calls. Narrower than the law allows on both ends, because a
# 8:01am sales call to a restaurant is legal and stupid.
EARLIEST_HOUR = 9
LATEST_HOUR = 19

# Ordinary English that carries no information about a fault. This is the one
# hand-written list in the file and therefore the weak point, so it is kept to
# grammar words only.
#
# "down" was in here at first and that was a mistake worth recording: it is the
# commonest fault word this trade has, 83 uses across the reported symptoms,
# because "the unit is down" and "temp down overnight" is how people say it.
# Filtering it as a preposition lost the plainest signal there is. Anything
# that could be describing the equipment stays out of this set.
NOISE = {
    "took", "back", "last", "have", "year", "used", "when", "there", "does",
    "then", "sort", "inside", "that", "like", "never", "every", "three",
    "with", "this", "they", "them", "from", "been", "were", "would",
    "about", "after", "before", "still", "just", "more", "than", "some",
    "what", "which", "your", "ours", "will", "much", "very", "even", "only",
    "into", "made", "make", "said", "told", "asked", "customer",
    "manufacturer", "warranty", "quoted", "cheaper", "replace", "whole",
}

# How many separate distress terms a public mention needs before it counts.
# One word is a coincidence: "cold" appears in a review of a cold beer. Two
# terms co-occurring in the same sentence is a description of a fault.
ENOUGH_TERMS = 2


# --------------------------------------------------------------------------
# the vocabulary, derived rather than written
# --------------------------------------------------------------------------

def distress_words(dealer_id: str, limit: int = 40) -> list[str]:
    """The words our own customers use when something has failed.

    TWO SOURCES, BOTH IN THE CUSTOMER'S VOICE

    `work_orders.reported_symptom` is what somebody said when they rang us,
    which is the larger and better half: 433 of them for the refrigeration
    desk against 85 complaints, and phrased the way a fault gets described out
    loud rather than the way an engineer writes it up.

        "not cold enough, food spoiling on the shelf"
        "water pooling underneath it"
        "frost building on the coil, temp climbing at night"

    `visits.found_cause` is deliberately NOT used. "Evaporator fan motor open
    circuit" is the truth about a fault and nobody has ever written it in a
    review, so training the matcher on it would find nothing in public text.

    HOW WIDE THE VOCABULARY SHOULD BE, MEASURED

    Against five reviews describing a real fault and six ordinary good reviews:

        top 40 terms    caught 3 of 5     0 false alarms
        top 60          caught 4 of 5     1 false alarm
        top 80          caught 4 of 5     2 false alarms
        top 120         caught 5 of 5     3 false alarms

    Forty, therefore. The costs are not symmetric: missing a prospect costs a
    call we never make, while a false alarm means opening with "I gather
    you are having trouble with your refrigeration" to a business that is not,
    which is the confident nonsense this whole system is built to avoid. A
    quiet miss beats a loud wrong guess every time.

    A dealer with too little of either source gets nothing back, and the sweep
    then refuses to run rather than falling back on words somebody made up.
    """
    with db.connect() as c:
        rows = [r["what"] for r in c.execute(
            "SELECT what FROM complaints WHERE dealer_id=? AND what IS NOT NULL",
            (dealer_id,))]
        rows += [r["reported_symptom"] for r in c.execute(
            """SELECT reported_symptom FROM work_orders
               WHERE dealer_id=? AND reported_symptom IS NOT NULL""",
            (dealer_id,))]

    counts: collections.Counter = collections.Counter()
    for text in rows:
        for w in re.findall(r"[a-z]{4,}", (text or "").lower()):
            if w not in NOISE:
                counts[w] += 1

    return [w for w, _ in counts.most_common(limit)]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?\n]+", text or "") if s.strip()]


def read_the_signal(text: str, words: list[str]) -> dict:
    """Whether a piece of public text describes a fault we fix, and which part.

    Returns the sentence verbatim rather than a score alone, because the whole
    point is being able to say "you posted this" instead of "our model says
    you need us".
    """
    vocab = set(words)
    best: tuple[int, str, list[str]] = (0, "", [])

    for s in _sentences(text):
        hits = sorted({w for w in re.findall(r"[a-z]{4,}", s.lower())
                       if w in vocab})
        if len(hits) > best[0]:
            best = (len(hits), s, hits)

    n, sentence, hits = best
    if n < ENOUGH_TERMS:
        return {"signal": False, "terms": hits,
                "why": f"{n} matching term(s), which is not a description of "
                       "a fault"}

    return {"signal": True, "terms": hits, "quote": sentence[:280],
            "score": round(min(1.0, n / 5.0), 2)}


# --------------------------------------------------------------------------
# the outside world
# --------------------------------------------------------------------------

def configured() -> bool:
    return bool(os.getenv("SERPER_API_KEY"))


def _post(url: str, body: dict) -> dict | None:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"X-API-KEY": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            TimeoutError, OSError) as e:
        print(f"[prospect] search failed: {type(e).__name__}: {e}", flush=True)
        return None


def find_businesses(kind: str, near: str, limit: int = 10) -> list[dict]:
    """Businesses of a kind, near a place, from the public listings.

    One paid search per call. Nothing here decides to ring anybody.
    """
    data = _post(PLACES_API, {"q": f"{kind} in {near}", "num": limit})
    if not data:
        return []

    out = []
    for p in (data.get("places") or [])[:limit]:
        out.append({
            "name": p.get("title") or "",
            "address": p.get("address") or "",
            "phone": p.get("phoneNumber") or "",
            "lat": p.get("latitude"), "lon": p.get("longitude"),
            "rating": p.get("rating"),
            "reviews": p.get("ratingCount"),
            "kind": kind,
        })
    return [b for b in out if b["name"]]


def public_mentions(name: str, near: str) -> str:
    """What the public has said about a business, as one block of text.

    Reviews are where a broken machine shows up first, because the customer
    who could not get a cold drink says so long before the owner rings anyone.
    """
    data = _post(SEARCH_API, {"q": f'"{name}" {near} reviews', "num": 10})
    if not data:
        return ""

    bits = []
    for r in (data.get("organic") or []):
        bits.append(r.get("snippet") or "")
        bits.append(r.get("title") or "")
    kg = data.get("knowledgeGraph") or {}
    if kg.get("description"):
        bits.append(kg["description"])
    return "\n".join(b for b in bits if b)


# --------------------------------------------------------------------------
# the gates
# --------------------------------------------------------------------------

def _already_ours(phone: str, name: str) -> bool:
    num = (phone or "").strip()
    with db.connect() as c:
        if num:
            hit = c.execute("SELECT 1 FROM phones WHERE e164=?", (num,)).fetchone()
            if hit:
                return True
        if name:
            hit = c.execute("SELECT 1 FROM accounts WHERE LOWER(name)=?",
                            (name.strip().lower(),)).fetchone()
            if hit:
                return True
    return False


def may_we_approach(e164: str, timezone: str = "America/Chicago",
                    at: str = "", allow_lookup: bool = True) -> dict:
    """Every reason we may not ring a business we have never met.

    Checked cheapest first, so a number on the do-not-call list never costs a
    lookup. Each refusal names itself: a gate that says only "no" cannot be
    audited, and the whole value of this feature is that it can be.
    """
    num = (e164 or "").strip()
    if not num:
        return {"may_call": False, "why": "no number"}

    listed = linetype.on_our_do_not_call(num)
    if listed["listed"]:
        return {"may_call": False,
                "why": f"they asked us to stop on {listed['asked_on']}",
                "gate": "do_not_call"}

    when = _now(timezone, at)
    if not EARLIEST_HOUR <= when.hour < LATEST_HOUR:
        return {"may_call": False,
                "why": f"it is {when:%H:%M} where they are, and we ring "
                       f"businesses between {EARLIEST_HOUR}:00 and "
                       f"{LATEST_HOUR}:00 local",
                "gate": "hours"}

    kind = linetype.line_type(num, allow_lookup=allow_lookup)
    if kind["line_type"] not in linetype.MAY_RING:
        return {"may_call": False,
                "why": f"that is a {kind['line_type']} number. An AI voice may "
                       "only ring a published business landline: there is no "
                       "business exemption for a wireless number, whoever "
                       "owns it",
                "gate": "line_type", "line_type": kind["line_type"]}

    return {"may_call": True, "line_type": kind["line_type"],
            "carrier": kind.get("carrier", ""),
            "say": "This is a business landline inside working hours and they "
                   "have not asked us to stop. Lead with the reason you are "
                   "ringing, in their own words, and stop if they are not "
                   "interested."}


def _now(timezone: str, at: str = "") -> datetime:
    if at:
        try:
            return datetime.fromisoformat(at)
        except ValueError:
            pass
    try:
        return datetime.now(ZoneInfo(timezone or "America/Chicago"))
    except Exception:
        return datetime.now()


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------

def sweep_prospects(dealer_id: str, near: str, kind: str = "restaurant",
                    limit: int = 8, allow_search: bool = True,
                    allow_lookup: bool = False) -> dict:
    """Find businesses with a public reason to need us, and record them.

    Deliberately does NOT ring anybody, and by default does not even pay for a
    line-type lookup: it builds the list and says what each one would cost to
    qualify. Spending money and dialling are separate decisions from finding.

    Args:
        dealer_id: which of our businesses is prospecting.
        near: a town or a neighbourhood, in words.
        kind: the sort of business, as somebody would search for it.
        limit: how many to look at.
        allow_search: false to run against what is already on file only.
        allow_lookup: true to resolve line type now, which costs per number.
    """
    words = distress_words(dealer_id)
    if len(words) < 5:
        return {"ok": False,
                "why": "we have too few complaints on file for this trade to "
                       "know what a distress signal sounds like. Prospecting "
                       "on invented vocabulary is guessing."}

    if not allow_search:
        return {"ok": False, "why": "searching is switched off for this run",
                "vocabulary": words[:12]}

    if not configured():
        return {"ok": False, "why": "no search key configured"}

    found = find_businesses(kind, near, limit)
    looked, kept, skipped = len(found), [], []

    with db.connect() as c:
        tz = (c.execute("SELECT timezone FROM dealers WHERE id=?",
                        (dealer_id,)).fetchone() or {"timezone": None})["timezone"]

    for b in found:
        if _already_ours(b["phone"], b["name"]):
            skipped.append({"name": b["name"], "why": "already a customer"})
            continue

        text = public_mentions(b["name"], near)
        sig = read_the_signal(text, words)
        if not sig["signal"]:
            skipped.append({"name": b["name"],
                            "why": "nothing public suggests they need us"})
            continue

        gate = may_we_approach(b["phone"], tz or "America/Chicago",
                               allow_lookup=allow_lookup)

        pid = f"P-{uuid.uuid4().hex[:8].upper()}"
        with db.txn() as c:
            c.execute(
                """INSERT INTO prospects
                     (id,dealer_id,name,kind,address,phone_e164,line_type,
                      lat,lon,source,found_on,signal,signal_kind,
                      signal_score,signal_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(dealer_id,phone_e164) DO UPDATE SET
                     signal=excluded.signal, signal_score=excluded.signal_score,
                     signal_seen=excluded.signal_seen""",
                (pid, dealer_id, b["name"], b["kind"], b["address"],
                 b["phone"], gate.get("line_type"), b["lat"], b["lon"],
                 "serper places", datetime.now().date().isoformat(),
                 ", ".join(sig["terms"]), "public_complaint",
                 sig["score"], sig.get("quote", "")))

        kept.append({
            "id": pid, "name": b["name"], "address": b["address"],
            "phone": b["phone"], "score": sig["score"],
            "because_they_said": sig.get("quote", ""),
            "terms": sig["terms"],
            "may_call": gate["may_call"],
            "gate": gate.get("why", ""),
        })

    kept.sort(key=lambda p: p["score"], reverse=True)
    reachable = [p for p in kept if p["may_call"]]

    return {
        "ok": True, "looked_at": looked, "found": kept,
        "not_worth_it": skipped,
        "reachable_now": len(reachable),
        "vocabulary": words[:12],
        "say": ("Each of these has a public reason to want us and the reason "
                "is quoted. Open with THEIR words, not ours. Anyone whose "
                "may_call is false does not get rung at all: a mobile number "
                "is not ours to dial with an artificial voice, whoever "
                "answers it."),
    }


def worth_ringing(dealer_id: str, at: str = "", limit: int = 10) -> dict:
    """The prospects that may lawfully be rung right now, best reason first."""
    with db.connect() as c:
        tz = (c.execute("SELECT timezone FROM dealers WHERE id=?",
                        (dealer_id,)).fetchone() or {"timezone": None})["timezone"]
        rows = c.execute(
            """SELECT id,name,address,phone_e164,signal,signal_seen,signal_score
               FROM prospects
               WHERE dealer_id=? AND approached_on IS NULL
               ORDER BY signal_score DESC LIMIT ?""",
            (dealer_id, limit * 3)).fetchall()

    ready, held = [], []
    for r in rows:
        gate = may_we_approach(r["phone_e164"], tz or "America/Chicago", at,
                               allow_lookup=False)
        item = {"id": r["id"], "name": r["name"], "address": r["address"],
                "phone": r["phone_e164"], "score": r["signal_score"],
                "because_they_said": r["signal_seen"]}
        if gate["may_call"]:
            ready.append(item)
        else:
            held.append({**item, "held": gate["why"]})

    return {"ok": True, "ready": ready[:limit], "held": held[:limit],
            "say": "Ring the top of `ready` and say why you are calling in "
                   "the first sentence. Everything in `held` stays unrung."}


def ring_this_prospect(prospect_id: str, dealer_id: str, at: str = "",
                       allow_lookup: bool = True) -> dict:
    """Ring one prospect, but only if the gate says we may.

    THE LAST INCH, DELIBERATELY LEFT OUT UNTIL NOW.

    Finding a business and ringing it were kept apart on purpose, so a sweep
    could never start dialling by accident. This joins them, and the joining is
    written so the gate CANNOT be skipped: this file places a call in exactly
    one place, it sits after may_we_approach, and it is unreachable unless
    may_call came back true. A test asserts all three.

    THE LOOKUP IS FORCED HERE

    worth_ringing lists prospects without paying for a carrier lookup, because
    listing is free and looking up is not. Dialling is the opposite: this is
    the moment the answer has to be real. `allow_lookup` defaults to true and
    an unresolved line type refuses the call, so the expensive question gets
    asked exactly once, at the only point where being wrong costs $500 to
    $1,500 rather than a wasted row.

    That distinction was not theoretical. linetype read os.getenv directly, saw
    no credentials on most code paths, and returned "mobile" from its
    fail-closed default without ever asking anybody. It looked exactly like the
    gate working. So this records WHERE the answer came from, and treats an
    answer that never reached a carrier as no answer at all.

    Args:
        prospect_id: the row to ring.
        dealer_id: whose desk is ringing.
        at: pretend it is this time, for testing the clock.
        allow_lookup: false only in tests. A real call resolves the line type.
    """
    from . import outbound

    with db.connect() as c:
        row = c.execute(
            """SELECT id,name,phone_e164,signal_seen,approached_on
               FROM prospects WHERE id=? AND dealer_id=?""",
            (prospect_id, dealer_id)).fetchone()
        tz = (c.execute("SELECT timezone FROM dealers WHERE id=?",
                        (dealer_id,)).fetchone()
              or {"timezone": None})["timezone"]

    if row is None:
        return {"ok": False, "why": "no such prospect on this desk"}

    if row["approached_on"]:
        return {"ok": False, "called": False,
                "why": f"we already approached them on {row['approached_on']}",
                "say": "Ringing again because a list still has them on it is "
                       "how a prospecting tool becomes a nuisance."}

    gate = may_we_approach(row["phone_e164"], tz or "America/Chicago", at,
                           allow_lookup=allow_lookup)

    # THE ONLY PATH TO A DIAL. Everything above must have said yes.
    if not gate["may_call"]:
        return {"ok": True, "called": False,
                "prospect": row["name"],
                "refused_by": gate.get("gate"),
                "why": gate["why"],
                "say": "Do not ring them by another route. The reason is a "
                       "legal one, not a preference."}

    placed = outbound.place_call(row["phone_e164"], f"prospect:{row['id']}")
    if not placed.get("ok"):
        return {"ok": False, "called": False, "prospect": row["name"],
                "why": placed.get("why", "the call could not be placed")}

    with db.txn() as c:
        c.execute("UPDATE prospects SET approached_on=?, outcome=? WHERE id=?",
                  (datetime.now().date().isoformat(), "rang", row["id"]))

    return {
        "ok": True, "called": True, "prospect": row["name"],
        "sid": placed.get("sid"),
        "line_type": gate.get("line_type"),
        "open_with": row["signal_seen"],
        "say": ("Open by saying who you are and that you are an automated "
                "assistant, then read back THEIR words. You are ringing "
                "because of something they published, and saying so is the "
                "difference between this and a cold call."),
    }


def ring_the_worthwhile(dealer_id: str, at: str = "", limit: int = 3,
                        allow_lookup: bool = True) -> dict:
    """Work down the list, ringing only what the gate clears.

    Capped low on purpose. A prospecting run that dials fifty businesses in an
    afternoon is a telemarketing operation whatever the code calls it, and the
    value here was never volume: it is that every call has a quoted reason.
    """
    ready = worth_ringing(dealer_id, at, limit=limit)["ready"][:limit]
    rang, refused = [], []
    for p in ready:
        out = ring_this_prospect(p["id"], dealer_id, at, allow_lookup)
        (rang if out.get("called") else refused).append(out)

    return {"ok": True, "rang": rang, "refused": refused,
            "say": f"{len(rang)} rung, {len(refused)} refused. The refusals "
                   "are the point: each one names the rule that stopped it."}
