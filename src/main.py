"""FastAPI entrypoint. Two routes: the TwiML webhook and the media socket."""

from __future__ import annotations

from fastapi import FastAPI, Form, WebSocket
from fastapi.responses import PlainTextResponse

from .config import settings
from .telephony.twilio_bridge import handle_call

app = FastAPI(title="Praevisum")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/voice", response_class=PlainTextResponse)
async def voice(From: str = Form(default="unknown")) -> str:
    """Twilio hits this when a call arrives. We hand it straight to the socket.

    The caller's number rides along as a custom parameter so the agent can
    identify them before the first word is spoken.
    """
    ws_url = f"{settings.public_ws_base}/stream"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="caller" value="{From}" />
    </Stream>
  </Connect>
</Response>"""


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    await handle_call(ws)
