"""What keeps going wrong, named rather than scored.

WHY THIS EXISTS

review.py measures every call and nothing read it. The instrument was built and
nothing was wired to the dial, which is the difference between a system that
records its performance and one that improves.

WHAT IT DOES NOT DO

It does not score anything and it does not decide anything. A number like
"quality 0.72" tells a dealer nothing they can act on, and a system that grades
itself is not measuring, it is writing its own report card.

WHAT IT DOES

Groups the failures until a repeated one becomes nameable:

    "four calls last week mentioned a walk-in and none reached a work order,
     and on three of them the desk asked the same thing twice"

That is a sentence somebody can do something about. It names the machine, the
count, and the structural evidence, and every part of it can be checked against
the transcripts it came from.

WHY DETERMINISTIC

The obvious version asks a model to read the transcripts and describe the
patterns. It would produce fluent findings nobody could verify, and this
project refuses that trade everywhere else. Everything here is a GROUP BY over
rows the calls actually wrote, so a pattern that is reported exists, and one
that is not reported is genuinely not in the data.

WHAT IT IS FOR

Not an alert. A short list a person reads once a week, ordered by how many
calls it cost, with the calls behind each line nameable so the first thing
anybody does is go and listen to one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import db

# Below this a repetition is a coincidence rather than a pattern. Two calls
# that both went wrong may be two bad calls; three is a shape.
ENOUGH_TO_BE_A_PATTERN = 3

# Words that appear in every service call and identify nothing. Grouping on
# these would report that customers mention temperature, which is true and
# useless.
NOISE = {
    "the", "and", "for", "with", "that", "this", "have", "has", "was", "were",
    "not", "but", "you", "our", "your", "its", "there", "here", "just", "get",
    "got", "one", "all", "out", "now", "can", "will", "about", "could",
    "should", "from", "they", "them", "then", "when", "what", "which", "some",
    "any", "been", "over", "into", "than", "very", "call", "called", "calling",
    "please", "thanks", "hello", "sorry", "temperature", "problem", "issue",
    "machine", "unit", "thing", "would", "need", "needs", "know", "like",
    "think", "going", "still", "much", "back", "down", "come",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in NOISE}


def _caller_lines(transcript: str) -> str:
    """Only what the customer said.

    Grouping on the desk's own words would find patterns in our vocabulary,
    which are patterns we already knew about.
    """
    return " ".join(
        t for w, _, t in (ln.partition(": ")
                          for ln in (transcript or "").splitlines())
        if w == "caller")


def failing_patterns(dealer_id: str = "D-REF", days: int = 30) -> dict:
    """What the desk keeps failing at, grouped until it has a name.

    Reads only calls that were classified and still produced nothing, because
    those are the ones where the desk understood the request and did not
    deliver it. A call nobody could classify is a different failure and is
    counted separately rather than mixed in.

    Args:
        dealer_id: whose desk.
        days: how far back to look.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    with db.connect() as c:
        broke = c.execute(
            """SELECT o.call_id, o.intent, o.outcome, o.caller_repeats,
                      o.agent_repeats, c.transcript, c.started_at
               FROM call_outcomes o JOIN calls c ON c.id = o.call_id
               WHERE o.dealer_id = ? AND c.started_at >= ?
                 AND o.resolved = 0 AND o.intent IS NOT NULL
               ORDER BY c.started_at DESC""", (dealer_id, cutoff)).fetchall()

        unclassified = c.execute(
            """SELECT COUNT(*) n FROM call_outcomes o
               JOIN calls c ON c.id = o.call_id
               WHERE o.dealer_id = ? AND c.started_at >= ? AND o.intent IS NULL""",
            (dealer_id, cutoff)).fetchone()["n"]

        total = c.execute(
            """SELECT COUNT(*) n FROM call_outcomes o
               JOIN calls c ON c.id = o.call_id
               WHERE o.dealer_id = ? AND c.started_at >= ?""",
            (dealer_id, cutoff)).fetchone()["n"]

    if not total:
        return {"calls": 0, "patterns": [],
                "say": "Nothing to look at. The desk has not taken a call in "
                       "this window, which is not the same as having taken "
                       "calls that all went well."}

    by_word: dict[str, list] = {}
    for r in broke:
        for w in _words(_caller_lines(r["transcript"] or "")):
            by_word.setdefault(w, []).append(r)

    patterns = []
    for word, rows in by_word.items():
        if len(rows) < ENOUGH_TO_BE_A_PATTERN:
            continue
        repeated = sum(1 for r in rows if (r["caller_repeats"] or 0) > 0)
        asked_twice = sum(1 for r in rows if (r["agent_repeats"] or 0) > 0)
        patterns.append({
            "word": word,
            "calls": len(rows),
            "of_which_caller_repeated": repeated,
            "of_which_desk_asked_twice": asked_twice,
            "intents": sorted({r["intent"] for r in rows}),
            "call_ids": [r["call_id"] for r in rows][:8],
            "says": _sentence(word, rows, repeated, asked_twice),
        })

    # Ordered by how many calls it cost, because that is what makes one worth
    # a person's afternoon and another worth ignoring.
    patterns.sort(key=lambda p: (-p["calls"], -p["of_which_desk_asked_twice"]))

    return {
        "calls": total,
        "window_days": days,
        "failed_with_an_intent": len(broke),
        "never_classified": unclassified,
        "patterns": patterns[:8],
        "say": ("Each line is a GROUP BY over calls that were understood and "
                "still produced nothing. Read one transcript before changing "
                "anything: the count says a pattern exists, not what it is."),
    }


def _sentence(word: str, rows: list, repeated: int, asked_twice: int) -> str:
    """The finding, assembled from the counts rather than narrated.

    Same rule as the technician briefing and the follow-up messages: nothing
    that reaches a person unattended contains a clause nobody chose.
    """
    intents = sorted({r["intent"] for r in rows})
    line = (f'{len(rows)} calls mentioning "{word}" were understood as '
            f'{" and ".join(intents)} and produced nothing')
    if asked_twice:
        line += f", and on {asked_twice} the desk asked the same thing twice"
    elif repeated:
        line += f", and on {repeated} the caller had to repeat themselves"
    return line + "."


def where_the_reasoning_went(call_id: str) -> dict:
    """Every decision made during one call, in order, with its arithmetic.

    The question a dealer actually has, and the one that was unanswerable
    until decisions were written down: not "what happened on that call" but
    "why did you do that".

    Args:
        call_id: the call, or the channel conversation key for a text thread.
    """
    with db.connect() as c:
        rows = c.execute(
            """SELECT kind, subject, verdict, line, numbers, at
               FROM decisions WHERE call_id = ? ORDER BY id""",
            (call_id,)).fetchall()
        call = c.execute(
            """SELECT c.intent, c.started_at, o.outcome, o.resolved
               FROM calls c LEFT JOIN call_outcomes o ON o.call_id = c.id
               WHERE c.id = ?""", (call_id,)).fetchone()

    return {
        "call": call_id,
        "intent": call["intent"] if call else None,
        "outcome": call["outcome"] if call else None,
        "decisions": [dict(r) for r in rows],
        "say": ("Every line is a value some decision already produced. None of "
                "it was written by a model, and none of it can disagree with "
                "what the desk actually did."),
    }
