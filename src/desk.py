"""The desk, reachable from any channel. One brain, several doors.

WHY THIS EXISTS SEPARATELY FROM THE CHANNELS

Customers pick the channel, not us. Somebody who lives in WhatsApp will send a
photo of a leaking cooler at midnight, somebody else will type into whatever
their phone opens first, and neither is going to install an app because a
refrigeration dealer would prefer it.

That means the number of doors is a business question and cheap. What must NOT
be cheap is what happens behind them. Two channels that answer the same
question differently is worse than one channel, because now the desk has been
caught contradicting itself and a customer cannot tell which answer was true.

So everything that decides anything lives here, and a channel adapter is only
allowed to do three things:

    prove the request really came from its platform
    turn that platform's shapes into (identity, text, media)
    send back the string this module returns

WHAT ROUTES THE CONVERSATION

Not a classifier, and it does not need to be. The sender is in the technicians
table or they are not, and that single fact separates two conversations that
have nothing to do with each other:

    a technician   is closing a job. Their words go to close_by_text, which
                   already checks every part against what was reserved and
                   what fits, and refuses to write in anything it cannot tie
                   to a real SKU.

    anyone else    is a customer, and gets the whole desk: service, orders,
                   quotes, complaints, returns. Not a reduced version of it.

THE REDUCED VERSION WAS A REAL BUG

The first WhatsApp build sent customers to the advice agent, because the front
agent runs on the Live audio model and cannot serve text. Advice can talk about
what to buy and nothing else. So on that channel a customer could not register
a complaint, could not get a delivery quote, could not place an order and could
not book a visit, and nothing anywhere said so. They would simply have been
answered vaguely by an agent holding none of the tools for what they asked.

`desk_agent` is the text twin of the phone agent with the same tool list, and
that is the fix.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from . import db

# A conversation that has been quiet this long is a new one. Somebody messaging
# about a freezer on Tuesday and a fryer on Friday is not continuing anything,
# and stapling the two together would have the desk answer the second with the
# first one's context.
CONVERSATION_HOURS = 4

# Channels that carry a real phone number identify the person the same way the
# phone line does. Telegram does not, and an unlinked chat id resolves to
# nobody, which is correct rather than a gap: guessing would be worse.
E164 = re.compile(r"^\+?[1-9]\d{6,14}$")

# What a channel is allowed to hand us as a photograph. A voice note, a PDF
# invoice or a video of a kitchen is not a rating plate.
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic")

# Long enough for a real answer, short enough to arrive as a message rather
# than a document. Channels have their own hard caps well above this.
MAX_REPLY = 1500


def _context(identity: str, channel: str) -> tuple[dict, str, str]:
    """Who this is, whose desk they reached, and which conversation it belongs to.

    The phone line does all three before the caller says a word, and the text
    channels did none of them. That was not a cosmetic gap:

      - the desk greeted a customer of eight years as a stranger, with none of
        their sites, machines or last visit loaded
      - `dealer_id` was the string "D-REF", hardcoded, so an IT customer
        messaging in was answered out of the refrigeration book. The same
        tenancy leak the recall fallback had, through a different door
      - no `calls` row existed, so review.py could not settle a text
        conversation and patterns.py could not see one. Every message thread
        was invisible to the measurement layer

    Returns the resolved caller, the dealer, and the call id.
    """
    from .caller import resolve

    who = resolve(identity) if E164.match(identity or "") else {
        "known": False, "registered": False, "phone": identity,
        "why": "this channel does not carry a phone number"}

    dealer = _dealer_for(who)
    return who, dealer, _conversation(identity, channel, who, dealer)


def _dealer_for(who: dict) -> str:
    """Whose book this customer belongs to.

    On the phone this comes from the number they dialled. A message has no
    equivalent, so it is resolved through the account instead, which is the
    same answer by a different route for anybody we already know.
    """
    account_id = who.get("account_id")
    if not account_id:
        return "D-REF"
    try:
        with db.connect() as c:
            row = c.execute("SELECT dealer_id FROM accounts WHERE id = ?",
                            (account_id,)).fetchone()
        return (row["dealer_id"] if row and row["dealer_id"] else "D-REF")
    except Exception as e:
        print(f"[desk] could not resolve the dealer for {account_id}: "
              f"{type(e).__name__}: {e}", flush=True)
        return "D-REF"


def _conversation(identity: str, channel: str, who: dict, dealer: str) -> str:
    """The call row a message thread belongs to, opened or continued.

    A message thread is a conversation and deserves the same record a phone
    call gets. Without one, none of review.py, patterns.py or followup.py can
    see it, and the desk measures only half of what it does.
    """
    cutoff = (datetime.now() - timedelta(hours=CONVERSATION_HOURS)).isoformat()
    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT id FROM calls WHERE from_e164 = ? AND dealer_id = ?
                   AND started_at >= ? ORDER BY started_at DESC LIMIT 1""",
                (identity, dealer, cutoff)).fetchone()
        if row:
            return row["id"]

        cid = f"MSG-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now().isoformat(timespec="seconds")
        with db.txn() as c:
            c.execute(
                """INSERT INTO calls (id,from_e164,contact_id,started_at,
                                      ended_at,dealer_id,connected)
                   VALUES (?,?,?,?,?,?,1)""",
                (cid, identity, who.get("contact_id"), now, now, dealer))
        return cid
    except Exception as e:
        print(f"[desk] could not open a conversation record: "
              f"{type(e).__name__}: {e}", flush=True)
        return ""


def _is_technician(identity: str) -> dict | None:
    with db.connect() as c:
        return c.execute(
            "SELECT id, name FROM technicians WHERE phone = ?",
            (identity,)).fetchone()


def _machine_reply(read: dict) -> str:
    """What to send back once a plate has been read.

    Says what was read either way. A customer who sent a photo has to be able
    to see whether the characters came through correctly, because they are the
    only person who can check them against the sticker in front of them.
    """
    if not read.get("ok"):
        seen = read.get("read") or {}
        if seen.get("model"):
            return (f"I read {seen.get('manufacturer', '')} {seen['model']} off "
                    f"that plate, but it is not in our catalogue, so I cannot "
                    f"confirm the machine. Could you check those characters, or "
                    f"send another photo straight on?").replace("  ", " ").strip()
        return ("I could not read that plate. Could you send another photo "
                "straight on with the whole sticker in frame?")

    top = (read["machine"].get("candidates") or [{}])[0]
    make = top.get("brand") or read["read"].get("manufacturer") or ""
    model = top.get("model") or read["read"].get("model") or ""

    line = f"Got it, that is a {make} {model}.".replace("  ", " ")
    if top.get("type"):
        line += f" {top['type']}."

    # The refrigerant is the reason getting the machine right matters. R-290
    # and R-600a are flammable and charge-limited, and a technician is told
    # what to expect before anybody opens a panel.
    if top.get("refrigerant"):
        line += f" Runs on {top['refrigerant']}"
        line += ", which is flammable." if top.get("flammable_refrigerant") else "."

    if read["machine"].get("confirm"):
        return line + " Is that the right one?"
    return line + " What is it doing?"


def answer(identity: str, text: str = "",
           media: list[tuple[bytes, str]] | None = None,
           channel: str = "message") -> str:
    """One inbound message from anywhere. Returns the text to send back.

    Args:
        identity: who sent it, normalised by the adapter. A phone number where
            the channel has one, so a technician is recognised as themselves
            whichever way they reply.
        text: what they typed.
        media: attachments the adapter already downloaded, as (bytes, mime).
        channel: which door it came through, for the session key and the log.
    """
    identity = (identity or "").strip()
    text = (text or "").strip()
    media = media or []

    # Everything the phone line establishes before the caller speaks: who they
    # are, whose book they reached, and which conversation this belongs to.
    who, dealer, call_id = _context(identity, channel)

    # Same tie as the phone path, so the reasoning behind an answer given over
    # WhatsApp is kept against the same conversation the outcome is settled on.
    from .trace import call_context

    call_context(call_id or f"{channel}:{identity}")

    tech = _is_technician(identity)
    if tech is not None:
        if not text:
            return (f"Thanks {tech['name'].split()[0]}, got the photo. Send a "
                    "line about what you found and I will close the job.")
        from .textback import close_by_text

        out = close_by_text(identity, text)
        if not out.get("ok"):
            return out.get("advice") or out.get("why",
                                                "Could not match that to a job.")
        return out["reply_to_technician"]

    photo = next(((b, t) for b, t in media
                  if any(t.startswith(k) for k in IMAGE_TYPES) and b), None)
    if photo:
        from .knowing import note_plate_read
        from .plate import read_plate

        read = read_plate(photo[0], photo[1].split(";")[0])

        # The signal behind "ask this customer for a photo first". Somebody who
        # has sent one that worked should not be asked to read a masked model
        # number out loud on the next call.
        note_plate_read(identity, bool(read.get("ok")),
                        (read.get("read") or {}).get("manufacturer", ""),
                        (read.get("read") or {}).get("model", ""),
                        from_call=call_id)
        return _machine_reply(read)

    if not text:
        return ("Send a photo of the rating plate, or tell me the model "
                "number and what the machine is doing.")

    # If we asked them something and this is the answer, tie it back. Without
    # this the after-visit question is rhetorical: somebody replies "yes all
    # good" and it is read as a fresh conversation, throwing away the one
    # piece of feedback the database cannot produce for itself.
    try:
        from .followup import record_reply

        record_reply(identity, text)
    except Exception as e:
        print(f"[desk] could not tie that to a follow-up: "
              f"{type(e).__name__}: {e}", flush=True)

    reply = _converse(f"{channel}:{identity}", text, who, dealer, call_id)

    # Settle as the conversation goes rather than at some end it does not have.
    # `settle` upserts, so the outcome sharpens with each exchange instead of
    # waiting for a hang-up that never comes on a message thread.
    if call_id:
        try:
            from .review import settle

            settle(call_id)
        except Exception as e:
            print(f"[desk] could not settle {call_id}: "
                  f"{type(e).__name__}: {e}", flush=True)
    return reply


# One session per person per channel, held in this process. Each message
# carries enough of its own context that losing this on a restart costs a
# sentence rather than a conversation, which is why it is not in the database
# yet. It is the first thing to move if this ever runs on two instances.
_sessions: dict = {}


def _converse(key: str, text: str, who: dict | None = None,
              dealer: str = "D-REF", call_id: str = "") -> str:
    """Text from a customer, answered by the same desk the phone line is.

    Deliberately `desk_agent`, which carries the phone agent's whole tool list.
    A second, smaller agent written for messaging is how a channel quietly
    becomes a worse version of the product.
    """
    import asyncio

    from google.adk.runners import InMemoryRunner
    from google.genai import types as gt

    from .agents import desk_agent

    async def run() -> str:
        runner, session = _sessions.get(key, (None, None))
        if runner is None:
            runner = InMemoryRunner(agent=desk_agent, app_name="desk")
            # The same state the phone line builds. `dealer_id` was the
            # literal string "D-REF" here, which answered an IT customer out
            # of the refrigeration book, and `caller` was absent entirely, so
            # a work order opened from a message had no contact on it and the
            # after-visit check could never find a number to ask.
            session = await runner.session_service.create_session(
                app_name="desk", user_id=key,
                state={"dealer_id": dealer, "caller": who or {},
                       "caller_phone": (who or {}).get("phone", ""),
                       "call_id": call_id})
            _sessions[key] = (runner, session)

        said = []
        async for ev in runner.run_async(
                user_id=key, session_id=session.id,
                new_message=gt.Content(role="user", parts=[gt.Part(text=text)])):
            for part in (getattr(getattr(ev, "content", None), "parts", None) or []):
                if getattr(part, "text", None):
                    said.append(part.text.strip())
        return " ".join(said).strip()

    try:
        reply = asyncio.run(run())
    except Exception as e:
        # Logged, not swallowed. The first version returned the apology with no
        # trace of why, and the real cause was that asyncio.run cannot be
        # called from inside a running event loop: the webhook is async, so
        # EVERY customer message failed here and said only "ring the desk".
        # Three bugs in this project have now hidden behind a bare except.
        print(f"[desk] {type(e).__name__}: {e}", flush=True)
        return ("Sorry, I could not get to that just now. Ring the desk and "
                "somebody will pick up.")
    return reply[:MAX_REPLY] or "Could you say a bit more about the machine?"
