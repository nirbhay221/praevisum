"""FastAPI entrypoint: the phone line, and the console the dealer watches it on."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import time

from pathlib import Path

from xml.sax.saxutils import escape

from fastapi import FastAPI, Form, Request, WebSocket
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
async def voice(From: str = Form(default="unknown"),
                To: str = Form(default="")) -> str:
    """Twilio hits this when a call arrives. We hand it straight to the socket.

    Two numbers ride along as custom parameters. The caller's, so the agent
    knows who it is speaking to before the first word. And the number they
    dialled, because one service answers several businesses' phones and that
    is what decides whose customers, technicians and repair corpus apply.
    """
    ws_url = f"{settings.public_ws_base}/stream?t={_issue_ticket()}"
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


HOLD_WAV = Path(__file__).resolve().parents[1] / "assets" / "hold.wav"


@app.get("/hold.wav")
def hold_audio() -> FileResponse:
    """The hold music itself. Written by Lyria, 8kHz mono for a phone line."""
    return FileResponse(HOLD_WAV, media_type="audio/wav")


@app.post("/fallback", response_class=PlainTextResponse)
async def fallback() -> str:
    """Twilio comes here when the main handler fails.

    This is the one place hold music honestly belongs. If the agent cannot be
    reached, the caller currently gets silence and hangs up on a business that
    looks broken. Instead they get a human sentence and something to listen to
    while a person is found. The music is generated rather than licensed, which
    is the whole reason it can exist on a service line nobody would buy a
    music licence for.
    """
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


@app.post("/whatsapp", response_class=PlainTextResponse)
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
    reply = await run_in_threadpool(_whatsapp_reply, form)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response><Message>{escape(reply)}</Message></Response>")


def _whatsapp_reply(form: dict) -> str:
    """Fetch any attachments and answer. Blocking, run in a worker thread."""
    from . import whatsapp

    media = []
    for i in range(min(int(form.get("NumMedia") or 0), MAX_ATTACHMENTS)):
        blob, mime = whatsapp.fetch_media(form.get(f"MediaUrl{i}", ""))
        if blob:
            media.append((blob, form.get(f"MediaContentType{i}") or mime))

    return whatsapp.handle(form.get("From", ""), form.get("Body", ""), media)


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


@app.get("/api/review")
def api_review(dealer: str = "D-REF", days: int = 30) -> dict:
    """How the desk has done, across all four flows. Derived, never declared."""
    from .review import review
    return review(dealer, days)


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

    said = []
    try:
        async for ev in runner.run_async(
                user_id=cmd.dealer, session_id=session.id,
                new_message=gt.Content(role="user",
                                       parts=[gt.Part(text=cmd.text)])):
            for part in (getattr(getattr(ev, "content", None), "parts", None) or []):
                if getattr(part, "text", None):
                    said.append(part.text.strip())
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    return {"reply": " ".join(said)[-600:] or "done"}


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
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
    """
    if not _ticket_ok(ws.query_params.get("t", "")):
        await ws.close(code=1008)
        return
    await handle_call(ws)
