"""Twilio Media Stream <-> ADK LiveRequestQueue.

This is the only genuinely fiddly part of the project, so it is deliberately
small and does exactly two things:

    Twilio  --audio-->  LiveRequestQueue.send_realtime()
    run_live() --audio-->  Twilio

Barge-in matters: when the caller interrupts, Gemini stops generating and we
must tell Twilio to drop whatever it has already buffered, otherwise the agent
keeps talking over the customer for a second or two and the illusion dies.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents import LiveRequestQueue, RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ..agents import root_agent
from ..config import APP_NAME
from .audio import make_inbound, make_outbound

_session_service = InMemorySessionService()

_runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=_session_service,
)


def _run_config() -> RunConfig:
    return RunConfig(
        response_modalities=[types.Modality.AUDIO],
        # transcripts on both sides: needed for the work-order record, and for
        # the eval harness later
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


async def handle_call(ws: WebSocket) -> None:
    await ws.accept()

    to_gemini = make_inbound()
    to_twilio = make_outbound()
    queue = LiveRequestQueue()

    stream_sid: str | None = None
    caller: str = "unknown"
    session = None

    async def pump_from_twilio() -> None:
        nonlocal stream_sid, caller, session
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                event = msg.get("event")

                if event == "start":
                    start = msg["start"]
                    stream_sid = start["streamSid"]
                    caller = start.get("customParameters", {}).get("caller", "unknown")
                    session = await _session_service.create_session(
                        app_name=APP_NAME,
                        user_id=caller,
                        state={"caller_phone": caller},
                    )
                    started.set()

                elif event == "media":
                    queue.send_realtime(
                        types.Blob(
                            data=to_gemini(msg["media"]["payload"]),
                            mime_type="audio/pcm;rate=16000",
                        )
                    )

                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            queue.close()

    async def pump_to_twilio() -> None:
        await started.wait()
        async for event in _runner.run_live(
            user_id=caller,
            session_id=session.id,
            live_request_queue=queue,
            run_config=_run_config(),
        ):
            # caller started speaking: stop our audio immediately
            if getattr(event, "interrupted", False) and stream_sid:
                await ws.send_text(json.dumps(
                    {"event": "clear", "streamSid": stream_sid}
                ))
                continue

            content = getattr(event, "content", None)
            if not content or not getattr(content, "parts", None):
                continue

            for part in content.parts:
                blob = getattr(part, "inline_data", None)
                if blob is None or not blob.data:
                    continue
                await ws.send_text(json.dumps({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": to_twilio(blob.data)},
                }))

    started = asyncio.Event()
    await asyncio.gather(pump_from_twilio(), pump_to_twilio())
