"""FastAPI entrypoint: the phone line, and the console the dealer watches it on."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from datetime import datetime
import secrets
import time

from pathlib import Path

from xml.sax.saxutils import escape

from fastapi import FastAPI, Form, Request, Response, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .config import settings
from .telephony.twilio_bridge import handle_call

app = FastAPI(title="Praevisum")


@app.on_event("startup")
def _load_corpus() -> None:
    """The searchable view of the repairs table, built once at startup.

    Semantic first, because callers and technicians do not share words. Falls
    back to word overlap if Vertex is unreachable, since a degraded briefing
    is better than a dead phone line.
    """
    import src.memory as memory

    try:
        from .embed import build_index
        idx, n, fresh = build_index()
        memory.INDEX = idx
        print(f"[startup] corpus: {n} repairs, semantic search "
              f"({fresh} newly embedded)", flush=True)
    except Exception as e:
        n = memory.load_from_db()
        print(f"[startup] corpus: {n} repairs, WORD MATCHING only "
              f"({type(e).__name__}: {str(e)[:80]})", flush=True)


# How often the queue is swept, in seconds. Nothing on this box ran the
# sender: no cron, no systemd timer. So every queued message -- the delivery
# check-in, the dropped call worth ringing back, the after-visit question, and
# the offers consent text that the whole marketing half depends on -- sat at
# status 'queued' with sent_at NULL, forever. The console showed a question
# that had in truth never been asked.
#
# A button was the honest stopgap and it is not a system. This is the timer.
SWEEP_EVERY = float(os.getenv("PRAEVISUM_SWEEP_SECONDS", "300") or 300)


@app.on_event("startup")
async def _sweep_the_queue() -> None:
    """Deliver what is due, on a timer, for every company.

    Deliberately respects due_after: this is the machine sending, and the gap
    before a message is exactly the judgement the queue was built to hold. The
    console button is the one thing allowed to send ahead of it, because a
    person is making that decision.

    Never raises out of the loop. A follow-up that could not be sent is worth
    a log line and is not worth taking the phone line down for.
    """
    if SWEEP_EVERY <= 0:
        print("[sweep] disabled", flush=True)
        return

    async def loop() -> None:
        from . import db, sender

        while True:
            await asyncio.sleep(SWEEP_EVERY)
            try:
                with db.connect() as c:
                    dealers = [r["id"] for r in c.execute(
                        "SELECT id FROM dealers")]
                for dealer in dealers:
                    out = await run_in_threadpool(
                        sender.send_followups, dealer)
                    if out.get("sent") or out.get("failed"):
                        print(f"[sweep] {dealer}: {out['sent']} sent, "
                              f"{out['failed']} could not be", flush=True)
            except Exception as e:
                print(f"[sweep] pass failed, will try again: "
                      f"{type(e).__name__}: {e}", flush=True)

    asyncio.create_task(loop())
    print(f"[sweep] follow-up queue every {SWEEP_EVERY:.0f}s", flush=True)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# How long a stream ticket stays good. Twilio connects the socket within a
# second or two of fetching the TwiML, so this is already generous. It only
# needs to outlast a slow handoff, not a call: the ticket is checked when the
# socket opens and never again, so a long call is unaffected.
TICKET_TTL = 120


_KEY_FILE = Path(__file__).resolve().parents[1] / ".stream_secret"
_key_cache: bytes | None = None


def _stream_key() -> bytes:
    """The signing key, invented on first use if nobody configured one.

    The first version of this keyed on the Twilio auth token, on the reasoning
    that we already hold it and already guard it. On the live machine that
    value is an empty string, so every ticket verified against the same empty
    key and the check passed for anything at all. The endpoint was wide open
    and the tests all passed, because the tests supplied a token.

    That is the failure mode worth designing out: a security check whose
    default is "allow". So the key is generated and persisted here rather than
    depending on configuration that may not exist, and there is no path where
    a missing secret means an open socket.
    """
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    configured = os.getenv("PRAEVISUM_STREAM_SECRET") or settings.twilio_auth_token
    if configured:
        _key_cache = configured.encode()
        return _key_cache

    try:
        if _KEY_FILE.exists():
            _key_cache = _KEY_FILE.read_bytes().strip()
        if not _key_cache:
            _key_cache = secrets.token_hex(32).encode()
            _KEY_FILE.write_bytes(_key_cache)
            _KEY_FILE.chmod(0o600)
    except OSError:
        # Read-only filesystem. A per-process key still closes the socket to
        # everyone outside; it only means tickets do not survive a restart,
        # and a ticket is good for two minutes anyway.
        _key_cache = secrets.token_hex(32).encode()
    return _key_cache


def _issue_ticket() -> str:
    expires = int(time.time()) + TICKET_TTL
    sig = hmac.new(_stream_key(), str(expires).encode(), hashlib.sha256)
    return f"{expires}.{sig.hexdigest()[:32]}"


def _ticket_ok(ticket: str) -> bool:
    """Constant-time check of a stream ticket.

    Returns False rather than raising on anything malformed. A bad ticket is
    the ordinary case here, not an exceptional one: most of what arrives at a
    public websocket is a scanner.
    """
    if os.getenv("PRAEVISUM_OPEN_STREAM") == "1":
        # A deliberate, explicit opt-out for poking at the socket by hand. It
        # has to be asked for. The version of this check that inferred "must
        # be development" from absent configuration left production open.
        return True
    try:
        stamp, sig = ticket.split(".", 1)
        if int(stamp) < time.time():
            return False
        want = hmac.new(_stream_key(), stamp.encode(),
                        hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, want)
    except Exception:
        return False


@app.post("/voice", response_class=PlainTextResponse)
async def voice(request: Request) -> str:
    """Twilio hits this when a call arrives. We hand it straight to the socket.

    Two numbers ride along as custom parameters. The caller's, so the agent
    knows who it is speaking to before the first word. And the number they
    dialled, because one service answers several businesses' phones and that
    is what decides whose customers, technicians and repair corpus apply.

    SIGNED, AND THIS WAS THE HOLE. Every other webhook here was signed and
    this one was not, which made the stream ticket pointless: the socket guard
    exists to stop a stranger opening a live model session on our billing, and
    the front door was handing tickets to anybody who asked. Proven with one
    curl against the live host.

    Worse than the billing: `From` is taken from the request body and decides
    who we think is calling. Unsigned, anyone could claim to be any customer's
    number and be greeted by name, told their equipment, and read their own
    account back to them.
    """
    from . import whatsapp

    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if not whatsapp.signature_ok(_public_url(request), form,
                                 request.headers.get("X-Twilio-Signature", "")):
        return PlainTextResponse("forbidden", status_code=403)

    From = form.get("From", "unknown")
    To = form.get("To", "")
    ws_url = f"{settings.public_ws_base}/stream/{_issue_ticket()}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="caller" value="{From}" />
      <Parameter name="dialled" value="{To}" />
    </Stream>
  </Connect>
</Response>"""


@app.post("/call-status", response_class=PlainTextResponse)
async def call_status(request: Request) -> str:
    """Twilio's verdict on a call, including the ones that never reached us.

    The only way this system can see a missed call. Every other path learns
    about a call from the media stream, and a caller who hangs up before the
    stream connects produces no row anywhere: for a service desk that is the
    most expensive event there is, and it was invisible.

    Signed with the same scheme as the WhatsApp webhook, and refused when
    unsigned for the same reason: this one creates call records.
    """
    from . import followup, whatsapp

    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if not whatsapp.signature_ok(_public_url(request), form,
                                 request.headers.get("X-Twilio-Signature", "")):
        return PlainTextResponse("forbidden", status_code=403)

    try:
        duration = int(form.get("CallDuration") or 0)
    except ValueError:
        duration = 0

    await run_in_threadpool(
        followup.record_call_status,
        form.get("CallSid", ""), form.get("CallStatus", ""),
        form.get("From", ""), duration)
    return ""


@app.post("/sms")
async def sms_webhook(request: Request):
    """Inbound SMS, which is how a technician actually closes a job.

    `close_by_text` was built for this and had no route to reach it, so the
    loop that grows the corpus only worked if the technician happened to be on
    WhatsApp. Signed and refused when unsigned, like every other webhook here.
    """
    from . import whatsapp

    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if not whatsapp.signature_ok(_public_url(request), form,
                                 request.headers.get("X-Twilio-Signature", "")):
        return PlainTextResponse("forbidden", status_code=403)

    reply = await run_in_threadpool(_sms_reply, form)

    # AS XML, NOT AS TEXT.
    #
    # These routes were declared PlainTextResponse, so the TwiML went out
    # as text/plain, Twilio never parsed it, and the whole document was
    # delivered to the customer as the body of the message. Twilio's own
    # log shows it, sent to a real phone:
    #
    #     <?xml version="1.0" encoding="UTF-8"?> <Response><Message>Hi!
    #
    # The voice routes happen to get away with it. Messaging does not.
    return Response(
        content=('<?xml version="1.0" encoding="UTF-8"?>\n'
                 f"<Response><Message>{escape(reply)}</Message></Response>"),
        media_type="text/xml")


def _sms_reply(form: dict) -> str:
    """The same desk every other channel reaches. Blocking, run in a thread."""
    from . import desk

    # `To` is the number they messaged, which is the same thing the voice
    # path uses to decide whose desk this is. It was being discarded.
    return desk.answer(form.get("From", ""), form.get("Body", ""),
                       channel="sms", dialled=form.get("To", ""))


@app.post("/outbound-voice", response_class=PlainTextResponse)
async def outbound_voice(request: Request, outreach: str = "") -> str:
    """What Twilio fetches when a call WE placed connects.

    Signed, for the same reason /voice is: it hands out a stream ticket, and
    an unsigned endpoint that hands out tickets makes the socket guard
    decorative. This one was missed when /voice was fixed and a test that
    walks every public POST route found it a minute later, which is the whole
    argument for walking the routes rather than listing them.

    The mirror of /voice. The queued reason rides along as a parameter so the
    agent knows why it is ringing somebody who did not ring us, and the
    disclosure is in the opening line the queue wrote rather than anything a
    model composes on the spot.
    """
    from . import whatsapp

    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if not whatsapp.signature_ok(_public_url(request), form,
                                 request.headers.get("X-Twilio-Signature", "")):
        return PlainTextResponse("forbidden", status_code=403)

    ws_url = f"{settings.public_ws_base}/stream/{_issue_ticket()}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="outreach" value="{escape(outreach)}" />
      <Parameter name="direction" value="outbound" />
    </Stream>
  </Connect>
</Response>"""


HOLD_WAV = Path(__file__).resolve().parents[1] / "assets" / "hold.wav"


@app.get("/hold.wav")
def hold_audio() -> FileResponse:
    """The hold music. Generated by Lyria, 8 kHz mono for a phone line.

    There is more than one track now and which plays is a function of the
    date, so it turns over every few days. One 32 second loop meant anybody
    held for two minutes heard it four times. See station.py for why the
    choice is the date rather than random or per call.
    """
    from .station import path_for

    return FileResponse(path_for(), media_type="audio/wav")


@app.post("/fallback", response_class=PlainTextResponse)
async def fallback(request: Request) -> str:
    """Twilio comes here when the main handler fails.

    Signed like the rest. It costs nothing to leave open and it is still a
    public endpoint that speaks and plays audio on our account, and an
    endpoint nobody can name a good reason to leave unsigned should be signed.

    This is the one place hold music honestly belongs. If the agent cannot be
    reached, the caller currently gets silence and hangs up on a business that
    looks broken. Instead they get a human sentence and something to listen to
    while a person is found. The music is generated rather than licensed, which
    is the whole reason it can exist on a service line nobody would buy a
    music licence for.
    """
    from . import whatsapp

    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if not whatsapp.signature_ok(_public_url(request), form,
                                 request.headers.get("X-Twilio-Signature", "")):
        return PlainTextResponse("forbidden", status_code=403)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">Sorry, our service line is having trouble right now.
  Please hold and someone will be with you.</Say>
  <Play loop="0">{settings.public_ws_base.replace("wss://", "https://")}/hold.wav</Play>
</Response>"""


# At most this many attachments are pulled per message. A rating plate is one
# photograph, and the cap is what stops a stranger who finds the webhook from
# making the server fetch a hundred files.
MAX_ATTACHMENTS = 2


def _public_url(request: Request) -> str:
    """The URL Twilio actually called, which is what they signed.

    Behind a proxy the app sees http on an internal host while Twilio signed
    https on the public one, and the signature fails for that reason alone.
    The forwarded headers are trusted here only to reconstruct the string for
    the signature check, which is itself the thing being verified.
    """
    url = str(request.url)
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https" and url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """Inbound WhatsApp, whether it carries words or a photograph.

    Refuses anything unsigned. This endpoint can close another dealer's jobs
    and reads their equipment history, so an open version of it is worse than
    no version, and the stream socket already taught this project what happens
    when a security check defaults to allow.
    """
    from . import whatsapp

    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    if not whatsapp.signature_ok(_public_url(request), form,
                                 request.headers.get("X-Twilio-Signature", "")):
        return PlainTextResponse("forbidden", status_code=403)

    # Off the event loop, for two reasons. The desk runs a model turn with
    # asyncio.run, which raises outright if a loop is already running here, and
    # every customer message failed on exactly that. And this process also
    # holds the live audio socket, so a slow model call on the loop would stall
    # a phone call that is already in progress.
    # ANSWER OUT OF BAND, BECAUSE TWILIO WILL NOT WAIT.
    #
    # The reply used to ride back in this response as TwiML. Twilio allows a
    # webhook roughly fifteen seconds; this desk runs a full model turn with
    # tool calls behind it, and on a live message the order guards were still
    # firing well past that. So the answer was computed correctly, arrived
    # after Twilio had given up, and the customer's phone stayed silent while
    # the log showed 200 OK.
    #
    # An empty TwiML acknowledges the message immediately. The real answer
    # goes out through the REST API the moment it exists, which Meta charges
    # nothing for inside the twenty-four hours a customer message opens.
    async def answer_when_ready() -> None:
        try:
            from . import whatsapp

            reply = await run_in_threadpool(_whatsapp_reply, form)
            if not (reply or "").strip():
                return
            frm = (form.get("From", "") or "").replace("whatsapp:", "")
            out = await run_in_threadpool(whatsapp.send, frm, reply)
            print(f"[whatsapp] answered {frm}: {out.get('ok')} "
                  f"({len(reply)} chars)", flush=True)
        except Exception as e:
            print(f"[whatsapp] could not answer: {type(e).__name__}: {e}",
                  flush=True)

    asyncio.create_task(answer_when_ready())
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?>\n<Response></Response>',
        media_type="text/xml")


def _whatsapp_reply(form: dict) -> str:
    """Fetch any attachments and answer. Blocking, run in a worker thread."""
    from . import whatsapp

    media = []
    for i in range(min(int(form.get("NumMedia") or 0), MAX_ATTACHMENTS)):
        blob, mime = whatsapp.fetch_media(form.get(f"MediaUrl{i}", ""))
        if blob:
            media.append((blob, form.get(f"MediaContentType{i}") or mime))

    return whatsapp.handle(form.get("From", ""), form.get("Body", ""), media,
                           to_number=form.get("To", ""))


@app.post("/telegram")
async def telegram_webhook(request: Request) -> dict:
    """Inbound Telegram, verified by the secret Telegram echoes back.

    Replies are sent through the Bot API rather than returned in this response.
    Telegram does accept a reply in the webhook body, but only one, and the
    desk sometimes has nothing to say to an update at all.
    """
    from . import telegram

    if not telegram.secret_ok(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")):
        return {"ok": False}

    # Same reason as the WhatsApp webhook: the desk runs a model turn with
    # asyncio.run, and the photo download is blocking.
    out = await run_in_threadpool(telegram.handle, await request.json())
    if out is None:
        return {"ok": True}

    chat_id, reply = out
    await run_in_threadpool(telegram.send, chat_id, reply)
    return {"ok": True}


WEB = Path(__file__).resolve().parent / "web"


@app.get("/")
@app.get("/console")
def dealer_console() -> FileResponse:
    """The owner's side: parts, prices, offers, and calls as they happen."""
    return FileResponse(WEB / "console.html", media_type="text/html")


@app.get("/api/dealers")
def api_dealers() -> list:
    from .console import dealers
    return dealers()


@app.get("/api/snapshot")
def api_snapshot(dealer: str = "D-REF") -> dict:
    from .console import snapshot
    return snapshot(dealer)


@app.get("/api/crew")
def api_crew(dealer: str = "D-REF") -> dict:
    """Who can be sent, and what each of them may legally open.

    The console showed customers, jobs, parts, offers and machines and not one
    engineer, which is a strange gap in a dispatch system: stock can be
    ordered and a price changed, but the number of people holding an EPA 608
    Type II on a Tuesday cannot.
    """
    from .crew import the_crew
    return the_crew(dealer)


class CarrierUpdate(BaseModel):
    order: str
    delivered_on: str = ""
    carrier: str = "UPS"
    tracking: str = ""


@app.post("/carrier/delivered")
async def carrier_delivered_hook(update: CarrierUpdate,
                                 request: Request) -> dict:
    """A carrier reporting that a tracking number landed.

    THIS IS HOW CARRIERS ACTUALLY TELL YOU. UPS, FedEx and DHL all push a
    tracking event to a URL you register. None of them send an email to a
    person, and building a mailbox reader would be pretending to integrate
    with something we have not.

    The handler behind this was written, tested and wired to nothing. It
    already does the two things that matter: it moves the warranty clock from
    the promised date to the real one, and it queues a check-in call, because
    an order is finished when the customer says the right thing arrived, not
    when a van drives away.

    Authenticated with a shared secret, because an open endpoint here lets a
    stranger mark somebody's order delivered and start a warranty running.
    """
    from .delivery import carrier_delivered

    want = os.getenv("CARRIER_WEBHOOK_SECRET", "").strip()
    if not want:
        return {"ok": False,
                "why": "no CARRIER_WEBHOOK_SECRET is configured, so this "
                       "endpoint refuses rather than accepting anything"}
    if request.headers.get("x-carrier-secret", "") != want:
        return {"ok": False, "why": "bad or missing x-carrier-secret header"}

    out = carrier_delivered(update.order, update.delivered_on,
                            update.carrier, update.tracking)
    if out.get("ok"):
        from . import events

        # WHOSE DELIVERY. This said D-REF for every carrier update, so an
        # IT laptop landing was published as a refrigeration event on the
        # wrong company's screen.
        events.publish(out.get("dealer_id") or _dealer_of_order(update.order),
                       "delivery",
                       what=f"{update.carrier} delivered {update.order}")
    return out


class DemoAction(BaseModel):
    what: str
    ref: str = ""
    text: str = ""
    dealer: str = ""
    dealer: str = "D-REF"



def _the_order_waiting(dealer: str = "") -> str:
    """The newest confirmed order nobody has delivered yet."""
    from . import db
    from .tenancy import the_desk

    with db.connect() as c:
        row = c.execute(
            """SELECT po.id FROM purchase_orders po
               LEFT JOIN deliveries d ON d.po_id = po.id
               WHERE po.dealer_id = ? AND po.status IN ('confirmed','shipped')
                 AND d.id IS NULL
               ORDER BY po.confirmed_at DESC, po.placed_at DESC LIMIT 1""",
            (the_desk(dealer),)).fetchone()
    return row["id"] if row else ""



def _dealer_of_order(po_id: str) -> str:
    """Which company an order belongs to, for events that only have its id."""
    from . import db
    from .tenancy import FALLBACK

    with db.connect() as c:
        row = c.execute("SELECT dealer_id FROM purchase_orders WHERE id = ?",
                        (po_id,)).fetchone()
    return (row["dealer_id"] if row and row["dealer_id"] else FALLBACK)


def _demo_on() -> bool:
    return os.getenv("DEMO_CONTROLS", "").strip() in ("1", "true", "yes")


@app.post("/api/demo")
async def api_demo(action: DemoAction) -> dict:
    """Stand in for a person outside the system, for somebody being shown this.

    WHY THIS EXISTS AND WHAT IT IS NOT

    Two steps in the loop are performed by people who are not here: a carrier
    posting a tracking event, and an engineer replying to the text they were
    sent. Somebody evaluating this has neither a UPS account nor the
    engineer's phone, so those two steps are invisible to them and the chain
    looks broken.

    Each button calls THE SAME FUNCTION the real path calls. Nothing is
    faked, short-circuited or pre-baked: the carrier button runs the handler
    the real webhook runs, and the engineer button runs the one a real text
    reply runs. What is simulated is only who pressed it.

    Off unless DEMO_CONTROLS is set, because an open endpoint that marks
    orders delivered and closes jobs is exactly what the carrier webhook was
    given a shared secret to prevent.
    """
    if not _demo_on():
        return {"ok": False,
                "why": "demo controls are off. Set DEMO_CONTROLS=1 to enable "
                       "them, and leave them off anywhere real"}

    what = (action.what or "").strip()

    if what == "carrier_delivered":
        from .delivery import carrier_delivered

        # BLANK MEANS THE OBVIOUS ONE. A judge who has just listened to a call
        # watched an order being placed; they did not write the number down,
        # and asking them to type PO-20CD33 into a browser prompt is asking
        # them to have been taking notes.
        ref = (action.ref or "").strip() or _the_order_waiting(action.dealer)
        if not ref:
            return {"ok": False,
                    "why": "nothing is confirmed and waiting to be delivered "
                           "on this desk, so there is nothing for a carrier "
                           "to report"}
        out = carrier_delivered(ref, carrier="UPS",
                                carrier_ref=action.text or "1Z999AA10123456784")
        out["order"] = out.get("order") or ref
        out["stood_in_for"] = "the carrier posting a tracking event"
        return out

    if what == "engineer_closes":
        from .textback import close_by_text
        out = close_by_text(action.ref, action.text
                            or "Done. Swapped the part, running cold now.")
        out["stood_in_for"] = "the engineer replying to their job text"
        return out

    if what == "engineer_asks":
        from .askback import answer_for_technician
        out = answer_for_technician(action.ref, action.text
                                    or "what refrigerant is in this one?")
        out["stood_in_for"] = "the engineer texting a question back"
        return out

    return {"ok": False, "why": f"unknown demo action {what!r}",
            "known": ["carrier_delivered", "engineer_closes", "engineer_asks"]}


@app.get("/api/waiting")
def api_waiting(dealer: str = "D-REF") -> dict:
    """Everything sitting on a person rather than on the system.

    Five lists that already existed as working, tested functions and had no
    screen: disputes between a customer and an engineer, warranty claims
    waiting on paperwork, jobs escalated to somebody by name, questions put to
    suppliers, and stock that arrived for a specific customer.

    They were unreachable in the same way for the same reason. Each is the
    SECOND half of a workflow: the first half is something an agent does on a
    call, and the second half is somebody looking at a list. Nobody built the
    list, so the first halves quietly went nowhere.
    """
    out: dict = {}

    def _try(name, fn, *a):
        try:
            out[name] = fn(*a)
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"[:160]}

    from .escalate import open_escalations
    from .recovery import how_they_are_doing, open_disputes
    from .sourcing import what_we_asked
    from .standing import open_claims

    _try("disputes", open_disputes, dealer)
    _try("claims", open_claims, dealer)
    _try("escalations", open_escalations, dealer)
    _try("supplier_questions", what_we_asked, 30)
    _try("workmanship", how_they_are_doing, "", dealer, 180)

    counts = {}
    for k, v in out.items():
        n = len(v) if isinstance(v, list) else (
            len(v.get(next(iter(v), ""), [])) if isinstance(v, dict) and v
            and isinstance(next(iter(v.values()), None), list) else 0)
        counts[k] = n
    out["counts"] = counts
    return out


@app.get("/api/followups")
def api_followups(dealer: str = "D-REF", limit: int = 25) -> dict:
    """Questions put to a customer, and whether they have answered.

    THE GAP THIS FILLS. Three things queue a message to a customer and then
    wait: a delivery check-in, a dropped call worth ringing back, and the
    after-visit question of whether the repair held. All three write a row and
    none of them had a screen, so the one part of the loop that depends on a
    HUMAN replying was the one part nobody could see.

    That matters more than it sounds. A customer who never answers "did it
    hold" is not the same as one who answered "no", and a fix that failed
    twice should stop being offered -- which cannot happen if nobody knows the
    question went unanswered.
    """
    from . import db

    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT f.id, f.kind, f.status, f.due_after, f.sent_at,
                      f.sent_via, f.reply, f.work_order_id, f.phone,
                      a.name AS customer, ct.name AS contact
               FROM followups f
               LEFT JOIN accounts a ON a.id = f.account_id
               LEFT JOIN contacts ct ON ct.id = f.contact_id
               WHERE f.dealer_id = ? OR f.dealer_id IS NULL
               ORDER BY CASE WHEN f.reply IS NULL THEN 0 ELSE 1 END,
                        f.created_at DESC
               LIMIT ?""", (dealer, limit))]

    said = {"delivery_check_in": "did it arrive in one piece",
            "after_visit": "did the repair hold",
            "dropped_call": "the call dropped, ring them back"}
    for r in rows:
        r["asking"] = said.get(r["kind"], r["kind"])
        r["answered"] = bool(r["reply"])
        r["still_waiting"] = not r["reply"] and r["status"] != "cancelled"

    return {"followups": rows,
            "waiting": sum(1 for r in rows if r["still_waiting"]),
            "answered": sum(1 for r in rows if r["answered"])}


class SendFollowups(BaseModel):
    dealer: str = "D-REF"
    now: bool = False


@app.post("/api/followups/send")
def api_followups_send(body: SendFollowups) -> dict:
    """Actually deliver the questions that have been queued for a customer.

    WHY THIS BUTTON EXISTS.

    Three things queue a message and wait: a delivery check-in, a dropped call
    worth ringing back, and the after-visit "did it hold". `send_followups`
    delivers them, and NOTHING EVER CALLED IT: there is no cron on the box and
    no systemd timer, so every one of those rows sat at status 'queued' with
    sent_at NULL, forever. The customer's phone stayed silent and the console
    showed a question that had, in truth, never been asked.

    A scheduler is the right answer for a real deployment. A button is the
    honest one here: it runs the same sender, it is visible, and it cannot
    pretend a message went out when it did not -- anything undeliverable stays
    queued and comes back in `failed`.
    """
    from . import sender

    # A person pressed this. The timer exists so a message does not land in
    # the same breath as the thing it is about, and somebody clicking send has
    # already made that call -- so the button sends what is queued rather than
    # reporting "nothing to do" at a queue that is plainly not empty.
    out = sender.send_followups(body.dealer or "D-REF", now=body.now)
    return out


@app.get("/api/visits")
def api_visits(dealer: str = "D-REF", limit: int = 15) -> dict:
    """Engineer visits, what we told them to take, and what it cost.

    THE LOOP NOBODY COULD SEE.

    A briefing works out which parts this fault usually needs and sends it to
    the engineer. The engineer texts back what they actually fitted. Both
    halves worked and neither was ever on a screen, so the one question a
    service manager asks every morning -- did the van have the right part --
    had no answer anywhere in the product.
    """
    from . import db

    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT v.id, v.work_order_id, v.completed_at, v.outcome,
                      v.labor_hours, v.found_cause,
                      w.reported_symptom, w.asset_id,
                      a.manufacturer, a.model_number, a.family,
                      ac.name AS customer,
                      t.name AS technician, t.phone AS technician_phone,
                      vc.parts_cost, vc.labour_cost
               FROM visits v
               JOIN work_orders w ON w.id = v.work_order_id
               LEFT JOIN assets a ON a.id = w.asset_id
               LEFT JOIN accounts ac ON ac.id = w.account_id
               LEFT JOIN technicians t ON t.id = v.technician_id
               LEFT JOIN visit_cost vc ON vc.visit_id = v.id
               WHERE w.dealer_id = ?
               ORDER BY v.completed_at IS NOT NULL, v.id DESC
               LIMIT ?""", (dealer, limit))]

        for r in rows:
            r["advised"] = [dict(x) for x in c.execute(
                """SELECT pr.sku, p.name, pr.likelihood
                   FROM parts_recommended pr
                   LEFT JOIN parts p ON p.sku = pr.sku
                   WHERE pr.visit_id = ? ORDER BY pr.likelihood DESC""",
                (r["id"],))]
            r["fitted"] = [dict(x) for x in c.execute(
                """SELECT u.sku, p.name, u.qty
                   FROM parts_used u LEFT JOIN parts p ON p.sku = u.sku
                   WHERE u.visit_id = ?""", (r["id"],))]
            r["open"] = r["completed_at"] is None
            r["cost"] = round((r["parts_cost"] or 0) + (r["labour_cost"] or 0), 2)

    from .service_loop import how_good_was_our_advice

    return {"visits": rows,
            "open": sum(1 for r in rows if r["open"]),
            "advice": how_good_was_our_advice(dealer_id=dealer)}


class CloseVisit(BaseModel):
    visit: str
    said: str


@app.post("/api/visits/close")
def api_visits_close(body: CloseVisit) -> dict:
    """Close a visit from the console, in the engineer's own words.

    DELIBERATELY THE SAME DOOR AS THE TEXT MESSAGE. It calls `close_by_text`,
    which is the function an SMS reply goes through: the same parser, the same
    part resolution, the same corpus write, the same costing. A console path
    with its own quietly different logic is how two ways of closing a job end
    up disagreeing about what happened.
    """
    from . import db
    from .textback import close_by_text

    with db.connect() as c:
        row = c.execute(
            """SELECT t.phone FROM visits v
               LEFT JOIN technicians t ON t.id = v.technician_id
               WHERE v.id = ?""", (body.visit,)).fetchone()
    if row is None:
        return {"ok": False, "why": f"no visit {body.visit!r}"}
    if not row["phone"]:
        return {"ok": False,
                "why": "no engineer is assigned to that visit, and the closure "
                       "is recorded against whoever did the work"}

    out = close_by_text(row["phone"], body.said, visit_id=body.visit)

    # What it cost, read back, because that is the point of typing it in.
    if out.get("ok"):
        try:
            from .service_loop import what_it_cost

            out["cost"] = what_it_cost(body.visit)
        except Exception as e:
            out["cost"] = {"ok": False, "why": f"{type(e).__name__}: {e}"}
    return out


@app.get("/api/losses")
def api_losses(dealer: str = "D-REF", limit: int = 8) -> dict:
    """What each product has cost us after the sale, and what to stop buying.

    THE NUMBER A DEALER DECIDES ON AND COULD NOT SEE.

    Service cost sat in visit_cost, returns in returns, claims in
    warranty_claims, and a complaint carried the customer's words and no
    figure at all. Answering "is this model worth keeping" meant joining four
    tables that share nothing but a make and a model, and nothing did.
    """
    from .ledger import what_each_product_costs_us, worth_restocking
    from .restock import products_to_restock

    costs = what_each_product_costs_us(dealer, limit=limit)
    verdicts = worth_restocking(dealer, limit=limit)
    try:
        floor = products_to_restock(dealer)
    except Exception as e:
        floor = {"ok": False, "why": f"{type(e).__name__}: {e}",
                 "order": [], "stop_stocking": []}

    return {"by_product": costs["products"], "total": costs["total"],
            "verdicts": verdicts["products"],
            "stop_stocking": verdicts["stop_stocking"],
            "reorder": floor.get("order", [])[:limit]}


@app.get("/api/consent")
def api_consent(dealer: str = "D-REF", limit: int = 25) -> dict:
    """Who has agreed we may send them offers, and who has not been asked.

    The permission itself and the conversation that produced it, side by side.
    An owner looking at a marketing suggestion needs to know in one glance
    whether that customer can legally be rung, and until now the only way to
    find out was to read the database.

    A refusal is shown as prominently as an agreement. "Asked and said no" is
    a real answer that has to be visible, or somebody will ask them again.
    """
    from . import db

    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT a.id, a.name AS customer,
                      k.state, k.asked_on, k.answered_on, k.answer,
                      oc.granted, oc.consent_form, oc.granted_on,
                      oc.granted_via, oc.revoked_on
               FROM accounts a
               LEFT JOIN offer_consent_asks k ON k.account_id = a.id
               LEFT JOIN outreach_consent oc ON oc.account_id = a.id
               WHERE a.dealer_id = ?
                 AND (k.id IS NOT NULL OR oc.account_id IS NOT NULL)
               ORDER BY CASE WHEN k.state = 'texted' THEN 0 ELSE 1 END,
                        a.name
               LIMIT ?""", (dealer, limit))]

    for r in rows:
        live = bool(r["granted"]) and not r["revoked_on"]
        r["may_we_offer"] = live and (r["consent_form"] or "") == "written"
        r["may_we_ring_about_their_own_kit"] = live
        r["where"] = (
            "they replied in writing" if r["may_we_offer"] else
            "spoken only, service calls but no offers" if live else
            "they said no" if r["state"] == "refused" else
            "waiting on their reply" if r["state"] == "texted" else
            "their reply was not a yes" if r["state"] == "unclear" else
            "never asked")

    return {"consent": rows,
            "may_offer": sum(1 for r in rows if r["may_we_offer"]),
            "waiting": sum(1 for r in rows if r["state"] == "texted"),
            "refused": sum(1 for r in rows if r["state"] == "refused")}


class CloseJob(BaseModel):
    work_order: str
    note: str = ""


@app.post("/api/jobs/close")
def api_jobs_close(body: CloseJob) -> dict:
    """Close a job from the screen, with a note saying who says so.

    THE THIRD WAY A JOB ENDS.

    The other two are real and neither covers everything: the engineer texts
    that it is done, or the customer answers the after-visit question. Both
    depend on somebody outside this building replying, and sometimes nobody
    does -- the engineer rings the office instead, the customer says it on a
    call, the job was cancelled and never marked.

    Without this the job stayed open forever and the console filled with work
    that had already happened, which is the fastest way to make somebody stop
    reading the console at all.

    The note is required in spirit rather than by the schema: a job closed
    with no reason is indistinguishable from one closed by mistake.
    """
    from . import db

    with db.connect() as c:
        job = c.execute(
            """SELECT w.id, w.status, a.name AS customer
               FROM work_orders w JOIN accounts a ON a.id = w.account_id
               WHERE w.id = ?""", (body.work_order,)).fetchone()
    if job is None:
        return {"ok": False, "why": f"no job {body.work_order!r}"}
    if job["status"] == "closed":
        return {"ok": True, "already": True, "work_order": job["id"],
                "note": "it was already closed; nothing changed"}

    note = (body.note or "").strip() or "closed on the console"
    with db.txn() as c:
        c.execute(
            """UPDATE work_orders
               SET status = 'closed', closed_at = ?
               WHERE id = ?""",
            (datetime.now().isoformat(timespec="seconds"), body.work_order))
        try:
            c.execute(
                """INSERT INTO work_order_notes (work_order_id, note, at)
                   VALUES (?,?,?)""",
                (body.work_order, note,
                 datetime.now().isoformat(timespec="seconds")))
        except Exception:
            # No notes table on this schema. The close still stands; a missing
            # audit line is not a reason to leave a finished job open.
            pass

    return {"ok": True, "work_order": job["id"], "customer": job["customer"],
            "closed_with": note,
            "say": "Closed. If the customer has not been asked whether it "
                   "held, that question is still worth sending."}


class RejectOrder(BaseModel):
    order: str
    why: str = ""


@app.post("/api/orders/reject")
def api_orders_reject(body: RejectOrder) -> dict:
    """Cancel an order from the screen. The other half of approve.

    THE BUTTON THAT WAS MISSING.

    The console could approve an order and could not turn one down, so every
    order raised in error -- a mis-heard model, a caller who changed their
    mind, a draft from a call that dropped -- stayed on the board forever with
    only one thing to do to it. A screen where the only action is yes is not
    a decision, it is a formality.

    CANCELLED, NOT DELETED. A customer ringing next week to ask what happened
    to their order deserves an answer, and "there is no record of it" is not
    one. It also keeps the reason, because an order cancelled with no reason
    is indistinguishable from one cancelled by mistake.

    Refuses once a machine has actually gone out. That is a return, which is
    a different thing with a different process, and pretending otherwise
    would leave a delivered machine on somebody's account with no order
    behind it.
    """
    from . import db

    with db.connect() as c:
        po = c.execute(
            """SELECT po.id, po.status, a.name AS customer
               FROM purchase_orders po JOIN accounts a ON a.id = po.account_id
               WHERE po.id = ?""", (body.order,)).fetchone()
    if po is None:
        return {"ok": False, "why": f"no order {body.order!r}"}
    if po["status"] == "cancelled":
        return {"ok": True, "already": True, "order": po["id"],
                "note": "it was already cancelled"}
    if po["status"] == "delivered":
        return {"ok": False,
                "why": "that one has been delivered",
                "say": "A delivered machine comes back as a RETURN, not a "
                       "cancellation. Raise it that way so the machine leaves "
                       "their account properly."}

    why = (body.why or "").strip() or "cancelled on the console"
    with db.txn() as c:
        c.execute("UPDATE purchase_orders SET status = 'cancelled' WHERE id = ?",
                  (body.order,))
        # Anything ordered in for this and not yet arrived should stop too.
        try:
            c.execute(
                """UPDATE supply_orders SET status = 'cancelled'
                   WHERE for_purchase_order = ? AND status = 'placed'""",
                (body.order,))
        except Exception:
            pass

    return {"ok": True, "order": po["id"], "customer": po["customer"],
            "was": po["status"], "cancelled_because": why,
            "say": "Cancelled. It stays on the record as cancelled rather "
                   "than disappearing, so anybody asking later gets an "
                   "answer."}


class ReturnOrder(BaseModel):
    order: str
    reason: str = "changed_mind"
    said: str = ""
    condition: str = "unopened"


@app.post("/api/orders/return")
def api_orders_return(body: ReturnOrder) -> dict:
    """Take a delivered machine back, and take it off their account.

    THE THING CANCELLING CANNOT DO.

    An order that has been delivered is not a mistake to be undone, it is a
    machine standing in somebody's kitchen. Cancelling it would leave that
    machine on their account with no order behind it, so the reject button
    refuses -- and until now there was nothing else to click, which meant a
    delivered order could never be unwound at all.

    `register_return` has existed the whole time and had no screen. It is the
    right function: a machine coming back is evidence against that model, and
    stronger evidence than a complaint, because somebody paid and sent it
    back.

    The asset is RETIRED rather than deleted, for the same reason the order is
    cancelled rather than erased: the work orders, quotes and cover attached
    to it are real history, and a customer asking next year what happened
    deserves an answer.
    """
    from . import db
    from .returns import register_return

    with db.connect() as c:
        po = c.execute(
            """SELECT po.id, po.status, po.account_id, po.dealer_id,
                      a.name AS customer
               FROM purchase_orders po JOIN accounts a ON a.id = po.account_id
               WHERE po.id = ?""", (body.order,)).fetchone()
        if po is None:
            return {"ok": False, "why": f"no order {body.order!r}"}
        assets = [r["id"] for r in c.execute(
            "SELECT id FROM assets WHERE from_order = ? AND retired_on IS NULL",
            (body.order,))]

    if po["status"] != "delivered":
        return {"ok": False,
                "why": f"that order is {po['status']}, not delivered",
                "say": "Nothing has gone out yet, so cancel it instead of "
                       "returning it."}

    logged = []
    for aid in assets:
        try:
            logged.append(register_return(
                "machine", body.reason, said=body.said, asset_id=aid,
                account_id=po["account_id"], condition=body.condition,
                dealer_id=po["dealer_id"] or ""))
        except Exception as e:
            logged.append({"ok": False, "asset": aid,
                           "why": f"{type(e).__name__}: {e}"})

    with db.txn() as c:
        for aid in assets:
            c.execute("UPDATE assets SET retired_on = date('now'), "
                      "location_note = 'returned' WHERE id = ?", (aid,))
        c.execute("UPDATE purchase_orders SET status = 'cancelled' WHERE id = ?",
                  (body.order,))

    return {"ok": True, "order": po["id"], "customer": po["customer"],
            "machines_taken_back": assets,
            "recorded": logged,
            "say": "Taken back and off their account. The machines are "
                   "retired rather than erased, so the history attached to "
                   "them survives."}


@app.get("/api/queue")
def api_queue(dealer: str = "D-REF", limit: int = 40) -> dict:
    """Who the desk is about to ring, and the reason for each.

    The nightly sweep has always written this and the console never showed it,
    so the monthly suggestion in particular went out without the owner ever
    having had the chance to look at it first.
    """
    from .outreach import waiting_to_ring
    return waiting_to_ring(dealer, limit)


@app.get("/api/hazards")
def api_hazards(dealer: str = "D-REF") -> dict:
    """Models drawing dangerous complaints from more than one customer.

    A dealer holds every complaint its own customers made about machines it
    sold them. One at a time those are grumbles; grouped per model and weighed
    for danger they arrive before a federal notice does.

    Read only. The sweep that actually queues the calls and assigns the swaps
    runs at night, so opening this screen cannot ring anybody.
    """
    from .hazard import stop_using_it, sweep_hazards

    out = sweep_hazards(dealer)
    for pat in out["patterns"]:
        pat["accounts"] = list(pat["accounts"])
        pat["script"] = stop_using_it(pat)["say"]
    return out


@app.get("/api/book")
def api_book(dealer: str = "D-REF", limit: int = 12) -> dict:
    """The customers, the crew and the leads, all of which were read only.

    111 customers were written only when somebody rang in, 19 engineers only
    by the seed scripts, and a prospect had no way of ever becoming either.
    `wishlist`, the table holding what a customer said they wanted, had zero
    rows in it because nothing finished the chain a promotion starts.
    """
    from .book import the_book
    return the_book(dealer, limit)


def _with_the_zone(stamp):
    """A naive server stamp, labelled with the zone the server wrote it in.

    Returns it untouched if it already carries an offset, if it is a plain
    date with no time on it, or if it cannot be read at all. A stamp nobody
    can parse is better shown as it was stored than dropped.
    """
    if not stamp or not isinstance(stamp, str):
        return stamp
    text = stamp.strip()
    if "T" not in text and " " not in text:
        return text            # a plain date carries no time to place
    try:
        from datetime import datetime

        when = datetime.fromisoformat(text)
    except ValueError:
        return text
    if when.tzinfo is not None:
        return text
    return when.astimezone().isoformat(timespec="seconds")


@app.get("/api/orders")
def api_orders(dealer: str = "D-REF", limit: int = 25,
               include_cancelled: bool = False) -> dict:
    """What customers have bought, and what is still waiting on somebody.

    THE GAP THIS FILLS. The console had fifteen cards -- customers, crew,
    stock, parts, offers, hazards, open jobs -- and not one of them showed an
    ORDER. Somebody could ring up, be quoted, agree, and have a purchase order
    raised in their name, and nothing on the owner's screen would ever say so.

    That also meant a draft nobody confirmed was invisible. The desk raises an
    order as a draft on purpose, so the total can be read back before it is
    placed; if the call drops between those two moments the order simply sat
    there, and the only way to find it was to query the database by hand.

    Drafts come first because they are the ones sitting on a person.
    """
    from . import db

    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT po.id, po.status, po.placed_at, po.confirmed_at,
                      po.subtotal, a.name AS customer, a.id AS account,
                      s.address, d.delivered_on
               FROM purchase_orders po
               JOIN accounts a ON a.id = po.account_id
               LEFT JOIN sites s ON s.id = po.site_id
               LEFT JOIN deliveries d ON d.po_id = po.id
               -- PARENTHESISED, because AND binds tighter than OR. Without
               -- these brackets the cancelled filter below attached itself to
               -- the NULL-dealer branch alone, so every order that HAD a
               -- dealer id skipped it and the board stayed full of cancelled
               -- rows after the cross had plainly been pressed.
               WHERE (po.dealer_id = ?
                  -- ORDERS WITH NO COMPANY ON THEM AT ALL.
                  --
                  -- dealer_id was added to purchase_orders after some had
                  -- already been raised, so a handful carry NULL. Filtering
                  -- strictly by company made those invisible on EVERY tab:
                  -- the desk could tell a customer they had outstanding
                  -- orders and the owner's screen showed nothing, which reads
                  -- as the desk making things up.
                  --
                  -- They are shown on the company the ACCOUNT belongs to,
                  -- which is where somebody would go looking.
                  OR (po.dealer_id IS NULL AND a.dealer_id = ?))
               -- CANCELLED ORDERS COME OFF THE BOARD.
               --
               -- The cross cancels rather than deletes, deliberately: a
               -- customer ringing next week to ask what happened deserves an
               -- answer, and "there is no record of it" is not one. But the
               -- row then sat on the screen forever with no action on it, so
               -- pressing the cross appeared to do nothing at all and the
               -- board filled with things somebody had already dealt with.
               --
               -- Hidden from the working list, kept in the database. The
               -- board is what still needs doing; the record is what
               -- happened. Ask for them with include_cancelled when the
               -- question is what happened.
                 AND (po.status != 'cancelled' OR ?)
               ORDER BY CASE po.status WHEN 'draft' THEN 0 ELSE 1 END,
                        po.placed_at DESC
               LIMIT ?""", (dealer, dealer, 1 if include_cancelled else 0,
                             limit))]

        for r in rows:
            r["lines"] = [dict(x) for x in c.execute(
                """SELECT description, qty, unit_price FROM purchase_lines
                   WHERE po_id = ? ORDER BY line_no""", (r["id"],))]
            # WHETHER IT IS ACTUALLY THEIRS YET. An order becomes a machine on
            # their account when it is delivered, not when it is paid for, and
            # the difference is the whole point of the delivery step.
            r["theirs"] = bool(r["delivered_on"])
            r["waiting_on_a_person"] = r["status"] == "draft"

    # WHAT ZONE THOSE STAMPS ARE IN, WHICH NOBODY EVER SAID.
    #
    # They are written by datetime.now() on the server, and the server runs
    # UTC. The console renders them as wall clock. So an order taken at 12:58
    # in the afternoon displayed as 5:58 in the evening -- five hours into the
    # future, on a screen whose whole job is telling somebody what happened
    # when.
    #
    # Stamping the server's own offset onto them is all the browser needs to
    # put them back into the reader's time. It is done here rather than in the
    # database because the stored value is not wrong, it is just unlabelled,
    # and rewriting history to fix a display is the more expensive mistake.
    for r in rows:
        for field in ("placed_at", "confirmed_at"):
            r[field] = _with_the_zone(r.get(field))

    return {"orders": rows,
            "drafts": sum(1 for r in rows if r["status"] == "draft"),
            "confirmed": sum(1 for r in rows if r["status"] == "confirmed"),
            "delivered": sum(1 for r in rows if r["theirs"])}


class ApproveOrder(BaseModel):
    order: str


@app.post("/api/orders/approve")
def api_orders_approve(body: ApproveOrder) -> dict:
    """Take an order all the way: confirmed, delivered, and on their account.

    WHY THIS DOES BOTH HALVES.

    It used to only confirm, on the reasoning that money changing hands is not
    the same as a machine standing in somebody's kitchen. That distinction is
    real and it is kept: `status` still moves draft -> confirmed -> delivered,
    and the warranty still runs from the delivery date rather than the sale.

    But there are only two ways an order is ever finished: a carrier reports
    it, or a person on this desk says it arrived. This button is the second
    one, and stopping it halfway left the owner with a screen that could take
    an order and could never complete it.

    So it is the same end state as the carrier webhook, reached deliberately
    by somebody who knows the thing turned up.
    """
    from .buying import confirm_purchase_order
    from .delivery import carrier_delivered

    out = confirm_purchase_order(body.order,
                                 agreed_by="approved on the console")
    if out.get("ok") is False and "already" not in str(out.get("why", "")):
        return out

    landed = carrier_delivered(body.order, carrier="collected",
                               carrier_ref="marked delivered on the console")
    return {"ok": bool(landed.get("ok")),
            "order": body.order,
            "confirmed": out.get("status") or "confirmed",
            "delivered": landed.get("delivered_on"),
            "now_theirs": landed.get("now_theirs") or [],
            "already_theirs": landed.get("already_theirs") or [],
            "check_in": landed.get("check_in"),
            "why": landed.get("why", ""),
            "note": "Confirmed and marked delivered. Anything on it that is a "
                    "machine is now on their account, and its warranty runs "
                    "from today rather than from the day they paid."}


@app.get("/api/floor")
def api_floor(dealer: str = "D-REF", family: str = "", limit: int = 40) -> dict:
    """The machines this business is holding, with our own record attached.

    The console listed 36 parts and not one of the 923 machines on the floor.
    A stock list is easy; a stock list that can say "we hold four, we have had
    two complaints, and one is under a federal recall" is the thing only this
    company can print.
    """
    from .shopfloor import families_on_the_floor, whats_on_the_floor
    out = whats_on_the_floor(dealer, family, limit)
    out["families"] = families_on_the_floor(dealer)
    return out


@app.get("/api/stopped")
def api_stopped(dealer: str = "D-REF", days: int = 30) -> dict:
    """What the enforcement layer actually did, split by whether the customer
    noticed.

    The central claim of this product is that it refuses rather than invents,
    and until the interventions table existed that claim was unfalsifiable:
    every guard printed its reasoning and threw it away. This is the counting.
    """
    from .guards import what_the_guards_did
    return what_the_guards_did(dealer, days)


@app.get("/api/hunting")
def api_hunting(dealer: str = "D-REF", limit: int = 8) -> dict:
    """Who a salesperson should ring today, and what to say to each.

    Deliberately a PERSON'S list. The desk may only ring a published business
    landline, because an artificial voice may not ring a wireless number; a
    salesperson has no such restriction, so the prospects the desk must leave
    alone are exactly the ones worth handing to a human.
    """
    from .hunting import todays_list
    return todays_list(dealer, limit)


@app.get("/api/review")
def api_review(dealer: str = "D-REF", days: int = 30) -> dict:
    """How the desk has done, across all four flows. Derived, never declared."""
    from .review import review
    return review(dealer, days)


@app.get("/api/supply")
def api_supply(dealer: str = "D-REF") -> dict:
    """What is on order, what is late, and what was advised and never bought."""
    from .supply import advised_but_not_ordered, on_order
    return {"on_order": on_order(dealer),
            "advised_not_ordered": advised_but_not_ordered(dealer)}


@app.get("/api/calibration")
def api_calibration(dealer: str = "D-REF", days: int = 365) -> dict:
    """What this desk's probabilities have actually been worth.

    The desk says 44% and a technician later says what it really was. Those
    two facts always existed and were never compared, because the prediction
    was never written down.
    """
    from .calibration import reliability, worst_misses
    out = reliability(dealer, days)
    out["worst_misses"] = worst_misses(dealer, days)
    return out


@app.get("/api/unloaded")
def api_unloaded(dealer: str = "D-REF") -> dict:
    """Visits due soon where nobody has confirmed the parts are on the van."""
    from .dispatch import how_often_unloaded, unconfirmed
    return {"waiting": unconfirmed(dealer), "record": how_often_unloaded(dealer)}


@app.get("/api/patterns")
def api_patterns(dealer: str = "D-REF", days: int = 30) -> dict:
    """What the desk keeps failing at, grouped until it has a name.

    The half that was missing: review.py measured every call and nothing read
    it, so the instrument existed and nothing was wired to the dial.
    """
    from .patterns import failing_patterns
    return failing_patterns(dealer, days)


@app.get("/api/why")
def api_why(call: str) -> dict:
    """Every decision made during one call, with the arithmetic behind it."""
    from .patterns import where_the_reasoning_went
    return where_the_reasoning_went(call)


@app.get("/api/events")
async def api_events(dealer: str = "D-REF") -> StreamingResponse:
    """Server-sent events. A dashboard never blocks a call: if nobody is
    watching, publishing is a no-op, and a slow viewer is dropped."""
    from . import events

    async def stream():
        for e in events.recent(dealer):
            yield events.as_sse(e)
        q = events.subscribe(dealer)
        try:
            while True:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=20)
                    yield events.as_sse(e)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(dealer, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


class Command(BaseModel):
    dealer: str = "D-REF"
    text: str


@app.post("/api/command")
async def api_command(cmd: Command) -> dict:
    """The owner types a sentence; the console agent turns it into rows."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types as gt

    from .console_agent import console_agent

    runner = InMemoryRunner(agent=console_agent, app_name="console")
    session = await runner.session_service.create_session(
        app_name="console", user_id=cmd.dealer,
        state={"dealer_id": cmd.dealer})

    said, results = [], []
    try:
        async for ev in runner.run_async(
                user_id=cmd.dealer, session_id=session.id,
                new_message=gt.Content(role="user",
                                       parts=[gt.Part(text=cmd.text)])):
            for part in (getattr(getattr(ev, "content", None), "parts", None) or []):
                if getattr(part, "text", None):
                    said.append(part.text.strip())
                fn = getattr(part, "function_response", None)
                if fn is not None and isinstance(getattr(fn, "response", None), dict):
                    results.append(fn.response)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    reply = " ".join(said)[-600:] or "done"
    return {"reply": _tell_the_truth(reply, results), "did": _did(results)}


# Words a reply uses when it is claiming something happened. Deliberately past
# tense: "closing" or "I can close" is not a claim, "closed" is.
_CLAIMS = ("added", "changed", "updated", "closed", "hired", "booked",
           "created", "started", "stopped", "retired", "set", "removed",
           "ordered", "done", "stood down")


def _did(results: list[dict]) -> list[str]:
    """What the tools actually did, for the screen. Not what was said."""
    return [str(r.get("why") or r.get("note") or "ok")
            for r in results if isinstance(r, dict)]


def _tell_the_truth(reply: str, results: list[dict]) -> str:
    """Refuse to pass on a success the tools did not perform.

    THE FAILURE THIS EXISTS FOR, observed live. Told "Corner Grocers are not
    interested", the agent called the right tool, the tool refused because it
    wanted an id and had been handed a name, and the agent answered "Closed
    Corner Grocers lead." Nothing had been closed. A console that reports work
    it did not do is worse than one that cannot do the work at all, because
    the owner stops checking.

    The root cause is fixed (leads match on a name now), but a confident false
    confirmation is the one failure that must not depend on a prompt rule
    holding. This is the same output-guard shape as saying.py, and for the
    same reason: an instruction governs what the model is asked to do, not
    what it does.

    Only fires when EVERY tool refused. A partial success is a real success
    and gets reported as the model wrote it.
    """
    if not results:
        return reply
    if any(r.get("ok") is True for r in results):
        return reply
    refused = [r for r in results if r.get("ok") is False]
    if not refused:
        return reply

    low = reply.lower()
    if not any(w in low for w in _CLAIMS):
        return reply

    why = refused[0].get("why") or "it was refused"
    which = refused[0].get("which") or refused[0].get("adding_needs")
    extra = f" ({', '.join(str(x) for x in which)})" if which else ""
    return f"Nothing was changed: {why}{extra}."


@app.websocket("/stream/{ticket}")
@app.websocket("/stream")
async def stream(ws: WebSocket, ticket: str = "") -> None:
    """The live audio socket, open only to a call we just handed out a ticket for.

    This was reachable by anyone who knew the hostname, and a scanner found the
    host within minutes of the DNS record going live. An open socket here is
    not a theoretical problem: it hands a stranger a live model session on our
    billing, and a conversation with a real dealer's customer data behind it.

    Twilio cannot send an Authorization header on a Media Streams connection,
    so the ticket travels in the URL that /voice just generated. It is an HMAC
    over an expiry stamp, keyed on the Twilio auth token we already hold, which
    means no new secret to store and nothing useful to replay: a captured URL
    stops working within minutes.

    THE TICKET IS IN THE PATH, NOT THE QUERY STRING, AND THAT IS NOT A STYLE
    CHOICE. Twilio strips the query string off a <Stream> url. It fetched the
    TwiML, got `/stream?t=...`, and connected to `/stream` with nothing on it,
    so the guard refused every call correctly and the line simply stopped
    answering. Two log lines a minute apart said it exactly:

        "WebSocket /stream" 403                      <- Twilio, no query
        "WebSocket /stream?t=1787718544..." accepted <- anything else

    The guard went in on 21 August, the last call that connected was on the
    18th, and nothing rang in between. No test caught it because every test
    reaches the socket the way a Python client does, not the way Twilio does.
    The query form is still accepted, for the console and for tests.
    """
    if not _ticket_ok(ticket or ws.query_params.get("t", "")):
        await ws.close(code=1008)
        return
    await handle_call(ws)
