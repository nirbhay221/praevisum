"""The day's calling list, for a person rather than for the desk.

WHY A LIST AND NOT A DIALLER

The desk may only ring a published business landline, because an AI-generated
voice is an artificial voice under the TCPA and there is no business carve-out
for a wireless number. Most small restaurants publish a mobile, so most
prospects this system finds are ones it must leave alone.

A PERSON has no such restriction. A salesperson ringing a business mobile is
an ordinary B2B call: the TSR exempts it, the DNC registry does not reach it,
and there is no artificial voice to trigger anything. So the three prospects
in five that the desk refuses are three a human can pick up the phone to.

The right division of labour follows from that. The system does the part it is
good at, which is knowing WHO is worth a call and WHY, and never touches the
handset.

PROFILE BEFORE SCORING

"Profiling before scoring: a two-stage predictive model for B2B lead
prioritization" (Journal of Personal Selling and Sales Management, 2026) argues
for two stages rather than one number: establish what KIND of lead this is,
then rank within the kind. The problem it names is the one this solves:

    sales reps often lack initial information to determine lead viability,
    leading to late contacts, arbitrary decisions based on intuition, waste
    of resources, inaccurate sales forecasts and lost sales

So stage one asks what fault the public text is describing, matched against
this company's own repair history. Stage two ranks by how good the evidence is
and how big the job would be. A single blended score would hide the first
question, and the first question is the one a salesperson actually needs
answered before they dial.

WHAT MAKES THIS DIFFERENT FROM A LEAD SCRAPER

The category is crowded. Plenty of open-source tools scrape local businesses,
read reviews for signals and have a general-purpose model write an opener.
None of them own a repair corpus, and that is the whole difference.

A scraper says: this business mentioned a broken fridge.

This says: this phrasing preceded a defrost heater failure on three of our own
jobs, the part is on the shelf at $148, there is 10% off defrost components
until the 30th, and they qualify for it. Every line is a row somebody can go
and check.
"""

from __future__ import annotations

from . import db

# Enough of a match to claim we have seen this fault before. Below it the
# briefing says what they wrote and makes no claim about the cause, which is
# more useful than a confident guess a technician would laugh at.
ENOUGH_TO_CLAIM_A_PATTERN = 2

# What a job on this kind of machine has been worth to us, used to rank rather
# than to quote. Never shown to the customer.
DEFAULT_JOB_VALUE = 0.0


def _fault_profile(dealer_id: str, said: str, terms: list[str]) -> dict:
    """STAGE ONE. What kind of fault the public text is describing.

    Matched against this company's own closed repairs, so the answer is "we
    have fixed this before and it was usually X" rather than a guess. Returns
    nothing rather than a weak match: telling a salesperson the cause is
    probably a compressor, on the strength of one shared word, is how they end
    up saying something a chef knows is wrong.
    """
    from .memory import index_for

    if not said:
        return {"known": False}

    try:
        hits = index_for(dealer_id).search(said, limit=5)
    except Exception as e:
        print(f"[hunting] could not search the corpus: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"known": False}

    seen: dict[str, int] = {}
    for h in hits:
        r = getattr(h, "repair", h)
        cause = (getattr(r, "found_cause", "") or "").strip()
        if cause:
            seen[cause] = seen.get(cause, 0) + 1

    if not seen:
        return {"known": False}

    cause, times = max(seen.items(), key=lambda kv: kv[1])
    if times < ENOUGH_TO_CLAIM_A_PATTERN and len(terms) < 3:
        return {"known": False}

    return {"known": True, "usually": cause, "times": times,
            "of_recent": len(hits)}


def _what_it_would_take(dealer_id: str, cause: str, tier: str) -> dict:
    """The part, its price, whether it is on the shelf, and any live offer.

    A salesperson who can say "we have one, it is $148, and there is 10% off
    this month" is having a different conversation from one who has to promise
    to find out and ring back.
    """
    from .offers import offer_on

    if not cause:
        return {}

    words = {w.strip(".,").lower() for w in cause.split() if len(w) > 3}
    if not words:
        return {}

    with db.connect() as c:
        best, score = None, 0
        for r in c.execute(
                "SELECT sku,name,unit_cost,lead_time_days FROM parts "
                "WHERE dealer_id=?", (dealer_id,)):
            overlap = len(words & {w.strip(".,").lower()
                                   for w in (r["name"] or "").split()})
            if overlap > score:
                best, score = r, overlap
        if best is None or score < 1:
            return {}

        free = c.execute(
            "SELECT COALESCE(SUM(free),0) f FROM stock_available WHERE sku=?",
            (best["sku"],)).fetchone()["f"]

    out = {"sku": best["sku"], "part": best["name"],
           "price": best["unit_cost"], "on_hand": free,
           "lead_time_days": best["lead_time_days"]}

    deal = offer_on(best["sku"], dealer_id, tier)
    if deal.get("applies"):
        out["offer"] = deal["promotion"]
        out["offer_ends"] = deal.get("ends")
        if deal.get("computed"):
            out["offer_price"] = deal["now"]
    return out


def _rank(signal_score: float, profile: dict, kit: dict) -> dict:
    """STAGE TWO. How worth a call this is, and WHAT THAT IS MADE OF.

    Deliberately readable arithmetic rather than a fitted model. There is no
    labelled outcome data here: nobody has rung any of these and reported back,
    so a learned score would be a confident number with nothing behind it. When
    outcomes exist this is the function to replace, and until then it says so.

    IT IS NOT A CONFIDENCE, AND MUST NEVER BE SHOWN AS ONE.

    A percentage beside a lead reads as "72% likely to buy". Nothing here
    supports that claim: `calibration.reliability()` on this desk still returns
    checked: 0, because no prediction has yet been followed by somebody saying
    what really happened. Dressing an ordering number up as a probability is
    the exact failure this project exists to avoid, so this returns the score
    WITH ITS PARTS and the interface shows the parts.

    "2.20" tells a salesperson nothing. "We fixed this 5 times, part in stock,
    offer live" tells them everything, and each clause is a row they can check.
    """
    score = float(signal_score or 0)
    because = []

    if signal_score:
        because.append({"points": round(float(signal_score), 2),
                        "what": "how clearly their own words describe a fault"})

    if profile.get("known"):
        times = min(profile.get("times", 0), 3)
        score += 0.4 + times * 0.1
        because.append({"points": round(0.4 + times * 0.1, 2),
                        "what": f"we have fixed this before, "
                                f"{profile.get('times', 0)} times"})

    if kit.get("sku"):
        score += 0.2
        because.append({"points": 0.2, "what": "we can name the exact part"})
        if (kit.get("on_hand") or 0) > 0:
            score += 0.2
            because.append({"points": 0.2,
                            "what": f"{kit['on_hand']} on the shelf today"})

    if kit.get("offer"):
        score += 0.1
        because.append({"points": 0.1, "what": "a live offer applies"})

    return {
        "score": round(score, 2),
        "because": because,
        "is_not": ("an ordering number, not a probability. Nothing here has "
                   "been followed up and scored, so any percentage would be "
                   "invented."),
    }


def _ranked(signal_score: float, profile: dict, kit: dict) -> dict:
    """The score flattened for a row, keeping its parts alongside it."""
    r = _rank(signal_score, profile, kit)
    return {"rank": r["score"], "rank_because": r["because"],
            "rank_is_not": r["is_not"]}


def todays_list(dealer_id: str = "D-REF", limit: int = 10,
                at: str = "") -> dict:
    """Who a salesperson should ring today, and what to say to each.

    Every prospect on file, profiled and ranked, whether or not the DESK may
    ring them. A person may call a mobile; the desk may not, and the row says
    which so nobody has to remember the rule.

    Args:
        dealer_id: whose desk is hunting.
        limit: how many to return.
        at: pretend it is this time, for the calling-hours note.
    """
    from .prospect import may_we_approach

    with db.connect() as c:
        tz = (c.execute("SELECT timezone FROM dealers WHERE id=?",
                        (dealer_id,)).fetchone()
              or {"timezone": None})["timezone"]
        rows = c.execute(
            """SELECT id,name,kind,address,phone_e164,line_type,signal,
                      signal_seen,signal_score,approached_on
               FROM prospects
               WHERE dealer_id=? AND approached_on IS NULL
               ORDER BY signal_score DESC""", (dealer_id,)).fetchall()

    out = []
    for r in rows:
        terms = [t.strip() for t in (r["signal"] or "").split(",") if t.strip()]
        profile = _fault_profile(dealer_id, r["signal_seen"] or "", terms)
        kit = _what_it_would_take(dealer_id, profile.get("usually", ""),
                                  "unknown")
        gate = may_we_approach(r["phone_e164"], tz or "America/Chicago", at,
                               allow_lookup=False)

        out.append({
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "address": r["address"],
            "phone": r["phone_e164"],
            **_ranked(r["signal_score"], profile, kit),

            # The evidence, quoted. Never paraphrased: the point is to be able
            # to say "you posted this" rather than "our system believes".
            "they_said": r["signal_seen"],
            "terms": terms,

            "profile": profile,
            "kit": kit,

            # Whether the DESK may ring them. A person always may.
            "desk_may_call": gate["may_call"],
            "desk_blocked_by": None if gate["may_call"] else gate.get("why"),

            "sticky": _sticky_note(r, profile, kit, gate),
        })

    out.sort(key=lambda p: p["rank"], reverse=True)
    return {
        "ok": True,
        "count": len(out),
        "leads": out[:limit],
        "say": ("Ranked by evidence, not by size. Every line is a row you can "
                "go and check. Where the desk is blocked, a person may still "
                "ring: the restriction is on an artificial voice, not on you."),
    }


def _sticky_note(row, profile: dict, kit: dict, gate: dict) -> list[str]:
    """The half-dozen lines a salesperson actually reads before dialling.

    Written as an opener and two questions rather than a pitch. A chef who has
    been told what their own problem is, by somebody who has clearly seen it
    before, is in a different conversation from one being sold at.
    """
    lines = []

    if row["signal_seen"]:
        lines.append(f'Open with THEIR words: "{row["signal_seen"][:150]}"')

    if profile.get("known"):
        n = profile["times"]
        lines.append(
            f'That phrasing came before "{profile["usually"]}" on '
            f'{n} of our own jobs. Do not say the cause outright, ask the '
            "question that would confirm it.")
    else:
        lines.append("Nothing in our own history matches this closely enough "
                     "to name a cause. Ask what it is doing and when it "
                     "started, and do not guess.")

    if kit.get("sku"):
        held = (f"{kit['on_hand']} on the shelf"
                if (kit.get("on_hand") or 0) > 0
                else f"{kit.get('lead_time_days', 0)} days from the supplier")
        money = (f"${kit['offer_price']:.2f} with {kit['offer']}"
                 if kit.get("offer_price")
                 else f"${kit['price']:.2f}")
        lines.append(f"If it is that, the part is {kit['part']}, {money}, "
                     f"{held}.")

    lines.append("Ask how long it has been like that and what is in the box. "
                 "A unit full of Friday's delivery is a different "
                 "conversation from an empty back-up.")

    if not gate["may_call"]:
        lines.append("THE DESK CANNOT RING THIS ONE: "
                     f"{gate.get('why', '')} You can.")

    return lines
