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


def _context(identity: str, channel: str,
             dialled: str = "") -> tuple[dict, str, str]:
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

    # The DEALER first where we can, because resolving the caller depends on
    # it. One number can belong to a customer of either business, and looking
    # them up without knowing which line they messaged is how an IT customer
    # gets answered with a refrigeration desk's version of their history.
    #
    # Where there is no dialled number, such as Telegram, the old order is
    # the only one available: resolve, then take the dealer off the account.
    if dialled:
        dealer = _dealer_for({}, dialled)
        who = resolve(identity, dealer) if E164.match(identity or "") else {
            "known": False, "registered": False, "phone": identity,
            "why": "this channel does not carry a phone number"}
    else:
        who = resolve(identity) if E164.match(identity or "") else {
            "known": False, "registered": False, "phone": identity,
            "why": "this channel does not carry a phone number"}
        dealer = _dealer_for(who, dialled)
    return who, dealer, _conversation(identity, channel, who, dealer)


def _dealer_for(who: dict, dialled: str = "") -> str:
    """Whose book this customer belongs to.

    On the phone this comes from the number they dialled. A message HAS an
    equivalent and it was being thrown away: Twilio sends `To` on every SMS
    and WhatsApp webhook, and that is the same dialled number the voice path
    uses. Only `From` and `Body` were read.

    So the account was the only route, and a first-time IT customer texting
    the IT number fell through to the refrigeration dealer. It worked only for
    people we already knew, which is precisely the caller who needs it least.

    The dialled number comes first now, and the account is the fallback for
    channels that genuinely have none, such as Telegram.
    """
    if dialled:
        try:
            with db.connect() as c:
                row = c.execute("SELECT id FROM dealers WHERE phone_e164 = ?",
                                (dialled,)).fetchone()
            if row is not None:
                return row["id"]
        except Exception as e:
            print(f"[desk] could not resolve the dialled number: "
                  f"{type(e).__name__}: {e}", flush=True)

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
           channel: str = "message", dialled: str = "") -> str:
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
    who, dealer, call_id = _context(identity, channel, dialled)

    # Same tie as the phone path, so the reasoning behind an answer given over
    # WhatsApp is kept against the same conversation the outcome is settled on.
    from .trace import call_context

    call_context(call_id or f"{channel}:{identity}")

    tech = _is_technician(identity)
    if tech is not None:
        if not text:
            # AN ENGINEER'S PHOTOGRAPH WAS THANKED FOR AND THROWN AWAY.
            #
            # The customer path has read rating plates through a vision model
            # since the beginning. The technician path answered "got the
            # photo" and discarded it, so the person who cannot describe what
            # they are looking at had the better tool and the person who could
            # act on it had none.
            shot = next(((b, t) for b, t in media if b), None)
            if shot:
                from .seeing import read_for_the_job, reply_to_the_engineer

                return reply_to_the_engineer(
                    read_for_the_job(shot[0], shot[1].split(";")[0]))

            return (f"Thanks {tech['name'].split()[0]}, got that. Send a "
                    "line about what you found and I will close the job.")
        # A TECHNICIAN ASKING IS NOT A TECHNICIAN CLOSING.
        #
        # Every message from a known technician used to go straight into
        # close_by_text, so an engineer standing in front of an open machine
        # who texted "any idea why this keeps tripping the breaker?" had that
        # sentence parsed for a cause and a labour figure.
        #
        # The company holds 851 repairs it has actually done and a set of
        # first-line procedures, and the only route to either was the
        # customer-facing "should we send anybody" check. The person with
        # their hands on the machine could not reach any of it.
        from .askback import answer_for_technician, looks_like_a_question

        if looks_like_a_question(text):
            return answer_for_technician(identity, text)["reply"]

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

        # AND KEEP IT AGAINST THE JOB, WHICH NOTHING DID.
        #
        # The plate read identifies the machine and is thrown away as far as
        # the engineer is concerned. A customer with an open job who sends a
        # photo is almost always photographing the FAULT, and that picture is
        # the reason the desk asks for one: it decides which part goes on the
        # van. Read, answered, and dropped meant the engineer arrived knowing
        # the model number and nothing about what they were walking into.
        try:
            from . import job_photos

            got = read.get("read") or {}
            job_photos.keep(
                (who or {}).get("account_id", ""),
                got.get("what_it_shows") or read.get("say")
                or "a photograph the customer sent about their machine",
                channel=channel,
                from_number=identity,
                media_type=photo[1].split(";")[0],
                manufacturer=got.get("manufacturer", ""),
                model_number=got.get("model", ""))
        except Exception as e:
            print(f"[desk] could not keep the photo against a job: "
                  f"{type(e).__name__}: {e}", flush=True)

        return _machine_reply(read)

    if not text:
        return ("Send a photo of the rating plate, or tell me the model "
                "number and what the machine is doing.")

    # If we asked them something and this is the answer, tie it back. Without
    # this the after-visit question is rhetorical: somebody replies "yes all
    # good" and it is read as a fresh conversation, throwing away the one
    # piece of feedback the database cannot produce for itself.
    try:
        from .asking import after_they_said_it_held
        from .followup import record_reply

        record_reply(identity, text)

        # AND WHAT THAT ANSWER EARNS. "Is it holding now?" was rhetorical in
        # a second way: the answer was recorded and nothing acted on it. A
        # yes is the one moment a review is worth asking for, and a no is a
        # second failure on the same job, which matters more.
        after_they_said_it_held(identity, text)
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


# WHAT PEOPLE SAY, AGAINST WHAT THE DATABASE CALLS IT.
#
# Matching was on family names alone, and families are how a catalogue is
# organised rather than how anybody speaks. On a live call:
#
#     [caller] I also wanted to buy uh furniture. Can you recommend me
#              something?
#     [tool]   route_to_vendor({'what_they_want': 'furniture'})
#     [agent]  Furniture is not something we cover.
#
# Two hundred and seventy eight furniture lines and seven hundred and eighty
# nine units on the floor at the time. "furniture" is not a family: the
# families are office chair, desk, conference table, filing cabinet and
# shelving unit, and the word appears in none of them.
#
# The same hole swallowed "electronics", "computers", "appliances", "audio",
# "a TV", and, most embarrassingly, "chairs", which missed the family "office
# chair" purely on the plural.
#
# Nobody opens a call with "conference table". They say the category, and the
# desk has to meet them there.

# The trade word each caller-facing category belongs to. Mapped to trades
# rather than families on purpose: a category is broader than any one family,
# and the vendor is what we are actually choosing.
CATEGORY_WORDS = {
    "refrigeration": ("refrigeration", "fridge", "refrigerator", "freezer",
                      "cooler", "chiller", "icebox", "ice", "kitchen",
                      "catering", "foodservice", "appliance"),
    "it": ("it", "computer", "pc", "laptop", "notebook", "computing",
           "hardware", "peripheral", "accessory", "accessories"),
    "furniture": ("furniture", "furnishing", "furnishings", "seating",
                  "chair", "desk", "table", "cabinet", "shelf", "shelving",
                  "office"),
    "av": ("av", "audio", "visual", "electronics", "electronic", "tv",
           "telly", "television", "screen", "projector", "sound", "speaker",
           "signage", "display"),
}


def _words(text: str) -> set[str]:
    """Comparable words, with plurals folded in.

    "chairs" missed "office chair" on the s alone, which is not a distinction
    anybody speaking out loud is making.
    """
    # Short words are dropped because two letter noise matches everything,
    # with a named exception for the ones that are real trade words. "it" is
    # deliberately NOT here: it is a trade name and also the commonest word in
    # English, so honouring it would route "is it broken" to the IT vendor.
    KEEP_SHORT = {"tv", "av", "pc", "ups"}

    out = set()
    for w in (text or "").lower().replace("-", " ").replace("/", " ").split():
        w = w.strip(".,?!'\"")
        if len(w) < 3 and w not in KEEP_SHORT:
            continue
        out.add(w)
        if w.endswith("ies") and len(w) > 4:
            out.add(w[:-3] + "y")
        if w.endswith("es") and len(w) > 4:
            out.add(w[:-2])
        if w.endswith("s"):
            out.add(w[:-1])
    return out


def _vendor_for(what_they_want: str) -> dict:
    """Which vendor behind this desk carries that kind of equipment.

    Internal. Nothing here reaches the caller: route_to_vendor decides what is
    said, and what is said is never a vendor's name.

    THIS USED TO BE A HAND-OFF, and what it was replaced with is the point.
    It asked "is this somebody else's trade, and what is their number",
    because the front counter was split and a caller asking the refrigeration
    line for a laptop had to be sent away. It returned a script that read
    another company's number down the phone, which was a real improvement on
    "sorry, we do not sell those" and still the wrong shape.

    There is nowhere to send anybody now. The same lookup answers the question
    that was underneath it the whole time: which of our own suppliers fills
    this.

    MATCHING IS ON WHOLE WORDS, BOTH WAYS. "laptop" matches "laptop", and
    somebody saying "cooler" matches "walk-in cooler". A substring match would
    put a "printer" call through to whoever services "sprinter vans", the same
    class of error as "Continental" matching car tyres.

    Args:
        what_they_want: what the caller asked about, in their words. A laptop,
            a printer, a walk-in cooler.
    """
    from . import db

    words = _words(what_they_want)
    if not words:
        return {"found": False, "why": "nothing to match on"}

    try:
        with db.connect() as c:
            rows = c.execute(
                "SELECT id, families, trade "
                "FROM dealers WHERE families IS NOT NULL").fetchall()
    except Exception as e:
        print(f"[desk] could not look up which vendor carries that: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"found": False, "why": "we could not check"}

    for r in rows:
        families = [f.strip().lower() for f in (r["families"] or "").split(",")]
        for fam in families:
            if not fam:
                continue
            # Whole words both ways: "laptop" matches "laptop", and "walk in
            # cooler" matches somebody saying "cooler". A substring match here
            # would put a "printer" call through to a business that services
            # "sprinter vans".
            fam_words = _words(fam)
            if fam_words & words:
                # No number is read out and no name is spoken, so a vendor
                # without a number on file is still a perfectly good answer to
                # "who carries this".
                return {
                    "found": True,
                    "dealer_id": r["id"],
                    "trade": r["trade"],
                    "handles": fam,
                }

    # A CATEGORY, NOT A FAMILY. "furniture", "electronics", "a fridge". Tried
    # only after the families, so a caller who names the exact thing still
    # gets the exact thing, and this only ever fills a hole.
    for r in rows:
        trade = (r["trade"] or "").lower()
        vocab = set(CATEGORY_WORDS.get(trade, ())) | {trade}
        if vocab & words:
            return {
                "found": True,
                "dealer_id": r["id"],
                "trade": r["trade"],
                "handles": trade,
                "matched": "category",
            }

    return {"found": False,
            "why": "no vendor behind this desk carries that"}


def route_to_vendor(what_they_want: str, tool_context) -> dict:
    """Work out which vendor behind this desk covers what they are asking for.

    ONE NUMBER, MANY VENDORS. The caller rings one desk and says what they
    need. Which supplier that belongs to is our problem, not theirs, and they
    should never hear about it.

    This replaces a hand-off that should never have existed. The desk used to
    answer as one of the vendors, so a customer asking about a laptop on the
    refrigeration number was told "we do not sell those", then given another
    number to ring, then offered a transfer. Three increasingly elaborate
    answers to a problem created by splitting the front counter.

    Underneath, nothing merges. Each vendor keeps its own stock, technicians,
    rates, warranty terms and repair history, and every query downstream is
    still scoped to exactly one of them. This only decides WHICH.

    Call it as soon as you know what kind of equipment they are talking about,
    and again if they change subject: somebody can ring about a freezer and
    buy a laptop in the same call.

    Args:
        what_they_want: the equipment in their words. A laptop, a walk-in
            cooler, an ice machine.
    """
    found = _vendor_for(what_they_want)
    if not found.get("found"):
        return {
            "ok": False,
            "why": found.get("why", "we do not know who covers that"),
            "say": "Say plainly that it is not something this desk covers, and "
                   "do not guess at a company. Do not offer to find out.",
        }

    # Both places. Session state for this agent, and a call-scoped variable
    # that a sub-agent invoked with its own context can still read.
    from .tenancy import routed_to

    # The call id goes with it, so a tool that lands on a worker thread and
    # cannot read the context variable can still find out whose call this is.
    try:
        _call = (tool_context.state.get("call_id") or "")
    except Exception:
        _call = ""
    routed_to(found["dealer_id"], _call)
    try:
        tool_context.state["dealer_id"] = found["dealer_id"]
    except Exception as e:
        print(f"[desk] could not route to {found.get('dealer_id')}: "
              f"{type(e).__name__}: {e}", flush=True)
        return {"ok": False, "why": "we could not switch to that supplier"}

    return {
        "ok": True,
        "covers": found["handles"],
        "trade": found["trade"],
        "say": (
            f"WE CARRY {found['handles']}. That is settled now, so serve them: "
            "quote it, check stock, book it, whatever they asked for. Do NOT "
            "tell them we do not sell or service it, because we do, and you "
            "have just been told so.\n"
            "Say nothing about the supplier, the routing, or that anything "
            "changed. They rang one number and are talking to one desk, and "
            "hearing about our internal arrangements is no improvement on "
            "hearing 'we do not do that here'."
        ),
    }
