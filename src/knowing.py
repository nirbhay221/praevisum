"""What we have learned about dealing with one customer, as against what they own.

WHY THIS IS SEPARATE FROM caller.resolve()

`resolve` answers who they are: their account, sites, machines and last job.
This answers something different and softer, which is how the last few
conversations with them actually went. A desk that knows both opens a call
better than one that knows only the first.

THE ONE THAT ALREADY PROVED IT WORKS

`took_two_trips` is the best line in the opening brief:

    "That one took two visits, so be careful not to repeat it."

One fact, read from the database, changing how a call opens. Everything here
is the same shape, from facts already recorded elsewhere.

WHAT IS DELIBERATELY NOT HERE

Anything about how they LIKE to be spoken to. There is no signal anywhere in
this system for whether a customer prefers a warmer or a firmer manner, so a
per-customer tone profile would be invented, and an invented preference acted
on confidently is worse than no preference at all. It would be the sentiment
score this project already refused, wearing a nicer hat.

Everything below is countable: how many times they had to repeat themselves,
whether a photograph was needed, which channel they said they preferred. A
person can check every line against the rows it came from.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import db

# How far back a habit is still a habit. A customer who struggled with a model
# number two years ago has probably replaced the machine.
LOOKBACK_DAYS = 365

# Below this many past conversations there is no habit, only an anecdote. The
# same rule as MIN_SAMPLE in the buying advice, for the same reason: one call
# is not a pattern and treating it as one is how a desk becomes confidently
# wrong about somebody.
ENOUGH_CALLS = 2


def about(phone: str, contact: dict | None = None) -> dict:
    """How the last few conversations with this customer went.

    Returns countable observations and, where there are enough of them to mean
    something, a short instruction the agent can act on. Never raises: a call
    must not fail because we could not remember how the last one felt.

    Args:
        phone: their number, which is what every channel has in common.
        contact: the row from caller.resolve, if it is already loaded.
    """
    if not phone:
        return {"known_habits": False}

    cutoff = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    try:
        with db.connect() as c:
            calls = c.execute(
                """SELECT COUNT(*) n,
                          COALESCE(SUM(o.caller_repeats),0) repeated,
                          COALESCE(SUM(o.agent_repeats),0) asked_twice,
                          COALESCE(SUM(o.resolved),0) resolved
                   FROM calls cl
                   LEFT JOIN call_outcomes o ON o.call_id = cl.id
                   WHERE cl.from_e164 = ? AND cl.started_at >= ?""",
                (phone, cutoff)).fetchone()

            plates = c.execute(
                """SELECT COUNT(*) n, COALESCE(SUM(confirmed),0) worked
                   FROM plate_reads WHERE phone = ? AND at >= ?""",
                (phone, cutoff)).fetchone()

            said = c.execute(
                "SELECT COUNT(*) n FROM caller_memory WHERE phone = ?",
                (phone,)).fetchone()
    except Exception as e:
        print(f"[knowing] could not read the history for {phone}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"known_habits": False}

    spoken_to = calls["n"] or 0
    out = {
        "known_habits": spoken_to >= ENOUGH_CALLS,
        "conversations": spoken_to,
        "times_they_repeated_themselves": calls["repeated"] or 0,
        "times_we_asked_twice": calls["asked_twice"] or 0,
        "photos_sent": plates["n"] or 0,
        "photos_that_worked": plates["worked"] or 0,
        "things_they_have_told_us": said["n"] or 0,
    }

    out["do_this"] = _instructions(out, contact or {})
    return out


def _instructions(seen: dict, contact: dict) -> list[str]:
    """The short list the agent is actually given.

    Assembled from the counts rather than narrated, and only where there are
    enough conversations behind a count to mean anything. An instruction the
    desk cannot justify from a row is an instruction it should not have.
    """
    out: list[str] = []

    # A preference recorded on the contact and, until now, read by nothing at
    # all. We knew they would rather have a message and rang them anyway.
    pref = (contact.get("channel_pref") or "").strip().lower()
    if pref and pref != "sms":
        out.append(f"They prefer {pref}. Follow up there rather than ringing.")

    if not seen["known_habits"]:
        return out

    # The model number is the single most error-prone thing a customer is ever
    # asked to do, and a photograph removes it. Somebody who has needed one
    # every time should not be asked to read a masked number out a fourth time.
    if seen["photos_sent"] >= ENOUGH_CALLS and seen["photos_that_worked"]:
        out.append("They have sent a photo of the plate before and it worked. "
                   "Ask for one straight away rather than for the model number.")
    elif seen["photos_sent"] == 0 and seen["conversations"] >= ENOUGH_CALLS \
            and not seen["times_they_repeated_themselves"]:
        out.append("They read their model numbers out cleanly. Do not offer "
                   "the photo unless they get stuck.")

    if seen["times_we_asked_twice"] >= ENOUGH_CALLS:
        out.append("We have asked this customer the same question twice on "
                   "more than one call. Keep to one question at a time and "
                   "confirm what you heard before moving on.")

    if seen["times_they_repeated_themselves"] >= ENOUGH_CALLS:
        out.append("They have had to repeat themselves before. Read anything "
                   "they give you back to them once, then move on.")

    return out


def note_plate_read(phone: str, confirmed: bool, make: str = "",
                    model: str = "", from_call: str = "") -> None:
    """Record that a photograph was sent, and whether the catalogue knew it.

    The signal behind "ask this one for a photo first". Never raises.
    """
    if not phone:
        return
    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO plate_reads (phone,from_call,confirmed,make,model,at)
                   VALUES (?,?,?,?,?,?)""",
                (phone, from_call or None, 1 if confirmed else 0,
                 make or None, model or None,
                 datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        print(f"[knowing] could not record a plate read for {phone}: "
              f"{type(e).__name__}: {e}", flush=True)
