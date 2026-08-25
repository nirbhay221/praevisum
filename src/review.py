"""How the desk actually did, read back out of what the calls wrote.

WHY NOTHING HERE IS A MODEL'S OPINION

An agent grading its own conversation is not measurement, it is the agent
writing its own report card. So every field is derived from tables the call
genuinely wrote: a work order exists or it does not, a slot was promised or it
was not, a remote fix resolved or it did not, a purchase order reached
'confirmed' or it stopped at 'draft'.

The transcript is used only for things that are countable rather than
interpretable: how many turns, whether the caller said the same sentence
twice. Nothing here decides how anybody FELT.

WHY NOT SENTIMENT

The obvious version of this feature scores each call angry-to-happy and rings
back the angry ones. That is a verdict with nothing behind it, and a sales
rep handed "sentiment 0.82 negative" has nothing to open the conversation
with.

The structural signals are facts a person can check against the transcript:

    the caller repeated themselves      the desk did not understand them
    the desk asked the same thing twice it lost the thread
    a flow was entered and never closed they rang for something and got nothing

"They read the model number out three times and no job was opened" is worth a
callback. "Sounded annoyed" is not.

THE CASE EVERY BOUGHT TOOL WOULD GET WRONG

Containment metrics count a service call that ends with no work order as a
failure. Here it is often the best possible outcome: remote.py exists to end
calls without sending anybody, and the industry's own figure is that 14% of
truck rolls are unnecessary at $200 to $300 each.

So `avoided_visit` is counted apart from everything else and counted as a WIN.
A generic dashboard would show this product getting worse as it got better.

RESOLUTION MEANS SOMETHING DIFFERENT PER FLOW

    service    a visit booked, a counter slot booked, or a fix that worked
    order      a purchase order confirmed
    product    an answer given, which is the only flow with no row to point at
    supplier   an offer logged

A buying call that ends with no order because the customer was talked out of a
machine we have repaired four times is a good call. That is not detectable
from the row alone, which is why `outcome` is specific rather than a verdict:
'quoted_not_taken' and 'nothing' are different facts and are kept apart.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import db, trace

# A call that never got classified and produced nothing. Kept separate from a
# classified call that produced nothing, because they fail for different
# reasons: one is the desk not understanding, the other is it understanding
# and then losing the thread.
UNCLASSIFIED = "no_intent"

# Terminal states per flow, best first. Order matters: a call that booked a
# visit AND registered a complaint is a booked visit.
RESOLVED = {
    "visit_booked", "fixed_remotely", "counter_booked", "order_confirmed",
    "offer_logged", "answered", "complaint_registered", "return_registered",
}


def record_intent(call_id: str, intent: str) -> None:
    """Write what the desk decided this call was about.

    `calls.intent` has existed since the first schema and has never held
    anything: set_intent wrote to session state and stopped there. Never
    raises, because a call must not drop because bookkeeping failed.
    """
    if not call_id or not intent:
        return
    try:
        with db.txn() as c:
            c.execute("UPDATE calls SET intent=? WHERE id=?", (intent, call_id))
    except Exception as e:
        print(f"[review] could not record intent: {type(e).__name__}: {e}",
              flush=True)


# How alike two lines have to be to count as the same thing said twice.
#
# Not exact matching, which was the first version and caught almost nothing
# real. Somebody reading a model number out again does not repeat themselves
# word for word: "H R P 2 H C one S" and "HRP2HC-1S" are the same event and
# normalise to different strings, because a spoken "one" is not the digit 1.
# 0.7 catches those and still separates two different sentences.
SAME_THING = 0.7

# Below this, a line carries no content worth matching.
#
# Deliberately short. The first version used 12, which threw away the single
# line this feature exists to catch: a bare "HRP2HC-1S" is eight characters,
# so a caller reading their model number out three times counted as zero
# repeats. A model number is short by nature and is exactly what gets repeated.
ENOUGH_TO_MATCH = 6

# Which is safe only because acknowledgements are dropped by meaning rather
# than by length. These repeat harmlessly all the way through a perfectly
# good conversation and must never look like somebody struggling.
ACKNOWLEDGEMENTS = {
    "yes", "yeah", "yep", "no", "nope", "ok", "okay", "right", "sure",
    "thanks", "thankyou", "hello", "hi", "hey", "mhm", "uhhuh", "correct",
    "please", "sorry", "goodbye", "bye", "alright", "gotit", "perfect",
}

_SPOKEN = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
           "six": "6", "seven": "7", "eight": "8", "nine": "9", "zero": "0",
           "oh": "0", "dash": "", "hyphen": ""}


def _norm(line: str) -> str:
    """Flatten a spoken line towards what it would look like written down.

    Spoken digits become digits, since a caller reading a model number says
    "one" the first time and the desk writes "1" the second.
    """
    words = [_SPOKEN.get(w, w) for w in re.findall(r"[a-z0-9]+", line.lower())]
    return "".join(words)


def _repeats(lines: list[str]) -> int:
    """How many times somebody had to say the same thing again.

    A count of events, not a judgment about mood. Each line is compared with
    the ones before it, and a line only counts once however many times it was
    repeated after that, so three attempts at a model number is two repeats.
    """
    from difflib import SequenceMatcher

    normed = [n for n in (_norm(x) for x in lines)
              if len(n) >= ENOUGH_TO_MATCH and n not in ACKNOWLEDGEMENTS]
    repeats, matched = 0, set()
    for i, a in enumerate(normed):
        if i in matched:
            continue
        for j in range(i + 1, len(normed)):
            if j in matched:
                continue
            if SequenceMatcher(None, a, normed[j]).ratio() >= SAME_THING:
                repeats += 1
                matched.add(j)
    return repeats


def _sides(transcript: str) -> tuple[list[str], list[str]]:
    caller, agent = [], []
    for line in (transcript or "").splitlines():
        who, _, text = line.partition(": ")
        (caller if who == "caller" else agent).append(text)
    return caller, agent


def _what_happened(c, call_id: str) -> tuple[str, bool, str | None]:
    """Read the call's consequences out of the tables it wrote.

    Returns (outcome, avoided_visit, note). Checked best-first, so a call that
    did several things is described by the furthest it got.
    """
    def one(sql: str, *params):
        return c.execute(sql, params).fetchone()

    # A visit actually promised, not merely a job opened.
    booked = one(
        """SELECT w.id FROM work_orders w
           JOIN visits v ON v.work_order_id = w.id
           WHERE w.opened_from_call = ?""", call_id)
    if booked:
        return "visit_booked", False, booked["id"]

    # No van, because something documented worked. The industry counts this as
    # a failed call. It is the opposite, and it is why this column exists.
    fixed = one(
        """SELECT id FROM remote_attempts
           WHERE from_call = ? AND outcome = 'resolved'""", call_id)
    if fixed:
        return "fixed_remotely", True, "no visit needed"

    counter = one("SELECT id FROM counter_bookings WHERE from_call = ?", call_id)
    if counter:
        return "counter_booked", False, counter["id"]

    opened = one("SELECT id FROM work_orders WHERE opened_from_call = ?", call_id)
    if opened:
        # A job on the books that nobody was ever promised for. Half a call.
        return "opened_no_slot", False, opened["id"]

    po = one(
        """SELECT id, status FROM purchase_orders WHERE from_call = ?
           ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END LIMIT 1""",
        call_id)
    if po:
        return (("order_confirmed", False, po["id"])
                if po["status"] != "draft" else
                ("order_drafted", False, po["id"]))

    ret = one("SELECT id FROM returns WHERE from_call = ?", call_id)
    if ret:
        return "return_registered", False, ret["id"]

    cmp_ = one("SELECT id FROM complaints WHERE from_call = ?", call_id)
    if cmp_:
        return "complaint_registered", False, cmp_["id"]

    wish = one("SELECT id FROM wishlist WHERE from_call = ?", call_id)
    if wish:
        return "wishlist_noted", False, wish["id"]

    return "", False, None


def settle(call_id: str) -> dict:
    """Work out what became of one call, once it has ended.

    Called when the line drops. Everything it writes is read back out of the
    database rather than asked of the agent, so a call cannot be recorded as
    having gone well because the model thought so.
    """
    with db.connect() as c:
        call = c.execute(
            """SELECT id, dealer_id, intent, started_at, ended_at, transcript
               FROM calls WHERE id = ?""", (call_id,)).fetchone()
        if call is None:
            return {"ok": False, "why": "no such call"}
        outcome, avoided, note = _what_happened(c, call_id)

    intent = (call["intent"] or "").strip().lower()

    if not outcome:
        # Nothing was written. A product question legitimately writes nothing,
        # so it is the one flow where an empty result can still be an answer.
        # Every other flow reaching here produced nothing at all.
        outcome = "answered" if intent == "product" else (
            "nothing" if intent else UNCLASSIFIED)

    caller_lines, agent_lines = _sides(call["transcript"] or "")
    turns = len(caller_lines) + len(agent_lines)

    seconds = None
    if call["started_at"] and call["ended_at"]:
        try:
            seconds = (datetime.fromisoformat(call["ended_at"])
                       - datetime.fromisoformat(call["started_at"])).total_seconds()
        except ValueError:
            pass

    resolved = outcome in RESOLVED

    # Forced means the desk was engaged and broke: it knew what the call was
    # about and still finished with nothing to show. A call that was never
    # classified is a different failure and is not counted as an escalation,
    # because nothing was ever attempted to escalate FROM.
    escalation = None
    if intent and not resolved:
        escalation = "forced"

    with db.txn() as c:
        c.execute(
            """INSERT INTO call_outcomes
               (call_id,dealer_id,intent,outcome,resolved,avoided_visit,
                escalation,caller_repeats,agent_repeats,turns,seconds,note,
                settled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(call_id) DO UPDATE SET
                 intent=excluded.intent, outcome=excluded.outcome,
                 resolved=excluded.resolved, avoided_visit=excluded.avoided_visit,
                 escalation=excluded.escalation,
                 caller_repeats=excluded.caller_repeats,
                 agent_repeats=excluded.agent_repeats, turns=excluded.turns,
                 seconds=excluded.seconds, note=excluded.note,
                 settled_at=excluded.settled_at""",
            (call_id, call["dealer_id"], intent or None, outcome,
             1 if resolved else 0, 1 if avoided else 0, escalation,
             _repeats(caller_lines), _repeats(agent_lines), turns, seconds,
             note, datetime.now().isoformat(timespec="seconds")))
        c.execute("UPDATE calls SET outcome=? WHERE id=?", (outcome, call_id))

    # A conversation they started and we did not finish. Queue the resume here
    # rather than anywhere else, because this is the only place that knows the
    # call had an intent and still produced nothing. Never allowed to raise:
    # the recording is worth more than the follow-up.
    if escalation == "forced":
        try:
            from .followup import queue_dropped

            queue_dropped(call_id)
        except Exception as e:
            print(f"[review] could not queue a follow-up: "
                  f"{type(e).__name__}: {e}", flush=True)

    out = {"ok": True, "call": call_id, "intent": intent or None,
            "outcome": outcome, "resolved": resolved,
            "avoided_visit": avoided, "turns": turns, "seconds": seconds}
    trace.settled(call["dealer_id"], out)
    return out


def review(dealer_id: str = "D-REF", days: int = 30) -> dict:
    """How the desk has done, across every kind of call it takes.

    Not a score. Counts, with the calls behind each one nameable, so anything
    surprising can be read in the transcript rather than argued with.

    Args:
        dealer_id: whose desk.
        days: how far back.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    with db.connect() as c:
        rows = c.execute(
            """SELECT o.*, c.started_at, c.from_e164
               FROM call_outcomes o JOIN calls c ON c.id = o.call_id
               WHERE o.dealer_id = ? AND c.started_at >= ?
               ORDER BY c.started_at DESC""", (dealer_id, cutoff)).fetchall()

    if not rows:
        return {"calls": 0,
                "why": "no calls in this window",
                "say": "Nothing to report. This is the honest answer, not an "
                       "empty dashboard: the desk has not taken a call."}

    by_flow: dict[str, dict] = {}
    for r in rows:
        flow = r["intent"] or UNCLASSIFIED
        f = by_flow.setdefault(flow, {"calls": 0, "resolved": 0, "outcomes": {}})
        f["calls"] += 1
        f["resolved"] += r["resolved"]
        f["outcomes"][r["outcome"]] = f["outcomes"].get(r["outcome"], 0) + 1

    n = len(rows)
    resolved = sum(r["resolved"] for r in rows)
    avoided = sum(r["avoided_visit"] for r in rows)
    forced = sum(1 for r in rows if r["escalation"] == "forced")
    unclassified = sum(1 for r in rows if not r["intent"])
    secs = [r["seconds"] for r in rows if r["seconds"]]

    # Worth a person's attention, in order. Structural only: a call the desk
    # engaged with and finished with nothing, or one where somebody had to
    # keep saying the same thing.
    attention = [{
        "call": r["call_id"], "from": r["from_e164"], "at": r["started_at"][:16],
        "intent": r["intent"], "outcome": r["outcome"],
        "caller_repeated": r["caller_repeats"],
        "why": ("they repeated themselves and the call produced nothing"
                if r["caller_repeats"] and not r["resolved"] else
                "the desk knew what they wanted and produced nothing"
                if r["intent"] and not r["resolved"] else
                "we never worked out what they wanted"),
    } for r in rows
        if not r["resolved"] or r["caller_repeats"] >= 2][:15]

    return {
        "calls": n,
        "window_days": days,
        "resolved": resolved,
        "resolution_rate": round(resolved / n, 2),
        # Counted apart and counted as a win. A generic containment metric
        # scores these as failed calls, which would show this product getting
        # worse precisely as the remote-fix layer started working.
        "visits_avoided": avoided,
        "forced_escalation": forced,
        "forced_escalation_rate": round(forced / n, 2),
        "never_classified": unclassified,
        "median_seconds": round(sorted(secs)[len(secs) // 2], 1) if secs else None,
        "by_flow": by_flow,
        "needs_attention": attention,
        "read_this_as": (
            "Resolution counts a call that ended with no van as a WIN when a "
            "documented fix worked, which is the opposite of how a bought "
            "dashboard would score it. Forced escalation is the number that "
            "says the desk is not working: it knew what the call was about "
            "and still finished with nothing."),
    }
