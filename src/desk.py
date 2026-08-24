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

from . import db

# What a channel is allowed to hand us as a photograph. A voice note, a PDF
# invoice or a video of a kitchen is not a rating plate.
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic")

# Long enough for a real answer, short enough to arrive as a message rather
# than a document. Channels have their own hard caps well above this.
MAX_REPLY = 1500


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
        from .plate import read_plate

        return _machine_reply(read_plate(photo[0], photo[1].split(";")[0]))

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

    return _converse(f"{channel}:{identity}", text)


# One session per person per channel, held in this process. Each message
# carries enough of its own context that losing this on a restart costs a
# sentence rather than a conversation, which is why it is not in the database
# yet. It is the first thing to move if this ever runs on two instances.
_sessions: dict = {}


def _converse(key: str, text: str) -> str:
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
            session = await runner.session_service.create_session(
                app_name="desk", user_id=key, state={"dealer_id": "D-REF"})
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
