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
import uuid
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect
from google.adk.agents import LiveRequestQueue, RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ..agents import root_agent
from ..config import APP_NAME
from .. import db, events
from ..caller import resolve
from ..recall import MEMORY
from .audio import make_inbound, make_outbound
from .comfort import Comfort

_session_service = InMemorySessionService()

_runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=_session_service,
    memory_service=MEMORY,
)


def _run_config() -> RunConfig:
    return RunConfig(
        response_modalities=[types.Modality.AUDIO],
        # transcripts on both sides: needed for the work-order record, and for
        # the eval harness later
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


def _opening_brief(who: dict) -> str:
    """What the agent knows the instant the line opens.

    Written as prose rather than a JSON dump because it is read by a model
    that is about to speak, and a model that has just read a dict tends to
    read one back out.
    """
    if not who.get("known"):
        if who.get("registered"):
            return ("[Call connected. This number has never called before. A "
                    "record has already been created for them, so open a work "
                    "order normally when you get to it. Greet them, and get "
                    "their name and what business they are calling from early "
                    "on. Do not ask for an account number.]")
        return "[Call connected. No caller ID. Greet them and ask who is calling.]"

    # A residential account is named after the person, so "Sarah Ortega at
    # Sarah Ortega" has to not happen.
    residential = (
        who.get("account_kind") == "person"
        or who.get("account_name", "").strip().lower()
        == who.get("contact_name", "").strip().lower()
    )
    if residential:
        opening = f"[Call connected. This is {who['contact_name']}, a residential customer."
    else:
        opening = (f"[Call connected. This is {who['contact_name']}"
                   + (f", {who['contact_role']}" if who.get("contact_role") else "")
                   + f" at {who['account_name']}.")
    lines = [opening]

    assets = who.get("assets") or []
    if assets:
        if who.get("single_site"):
            listed = ", ".join(
                f"{a['manufacturer']} {a['model_number']} ({a['family']}, {a['location_note']})"
                for a in assets[:4])
            lines.append(f"Their equipment: {listed}.")
        else:
            by_site: dict[str, list] = {}
            for a in assets:
                by_site.setdefault(a["site_label"], []).append(
                    f"{a['manufacturer']} {a['model_number']}")
            lines.append("They have several sites: " + "; ".join(
                f"{s} has {', '.join(m)}" for s, m in by_site.items()) + ".")
            lines.append("Ask which site before anything else.")

    lj = who.get("last_job")
    if lj:
        lines.append(
            f"Last time, {lj['when']}, they reported '{lj['symptom']}'"
            + (f" on the {lj['family']}" if lj.get("family") else "") + ".")
        if lj.get("found_cause"):
            lines.append(f"It turned out to be: {lj['found_cause']}.")
        if lj.get("took_two_trips"):
            lines.append("That one took two visits, so be careful not to "
                         "repeat it.")

    lines.append("Greet them by name. Do not read any of this back as a list.]")
    return " ".join(lines)


# Tools worth covering. assess_job fans out to three agents and an embedding
# call; build_briefing does the expected-value work over the whole corpus.
# Everything else returns fast enough that the lead-in swallows it.
SLOW_TOOLS = {"assessment", "assess_job", "build_briefing"}


async def handle_call(ws: WebSocket) -> None:
    await ws.accept()

    to_gemini = make_inbound()
    to_twilio = make_outbound()
    queue = LiveRequestQueue()

    stream_sid: str | None = None
    caller: str = "unknown"
    session = None
    call_id = f"CALL-{uuid.uuid4().hex[:8].upper()}"
    dealer_id = "D-REF"
    transcript: list[tuple[str, str]] = []

    # Reads stream_sid through a callable rather than taking its value, because
    # it is still None here and only arrives with the start message.
    comfort = Comfort(ws, lambda: stream_sid)

    async def pump_from_twilio() -> None:
        nonlocal stream_sid, caller, session, dealer_id
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                event = msg.get("event")

                if event == "start":
                    start = msg["start"]
                    stream_sid = start["streamSid"]
                    caller = start.get("customParameters", {}).get("caller", "unknown")
                    # Who is this? Resolved from the database before a word is
                    # spoken, and registered if we have never heard from them.
                    # Doing this here rather than as a tool is the difference
                    # between greeting someone by name and greeting a stranger.
                    # which business was dialled decides everything downstream:
                    # whose customers, whose technicians, whose repair corpus.
                    dialled = start.get("customParameters", {}).get("dialled", "")
                    with db.connect() as _c:
                        row = _c.execute(
                            "SELECT id, name FROM dealers WHERE phone_e164=?",
                            (dialled,)).fetchone()
                        if row:
                            dealer_id = row["id"]
                    who = resolve(caller)
                    events.publish(dealer_id, "call_start",
                                   text=f"call from {caller}"
                                        + (f" - {who['contact_name']} at {who['account_name']}"
                                           if who.get("known") else " - new caller"))

                    # The call itself is a row. Previously the thing this
                    # whole product is about was not recorded anywhere.
                    # Twilio's own id for this call, kept so the status
                    # callback can tell a call that reached us from one that
                    # never did. Without it, a missed call and a served call
                    # are indistinguishable at the point the status arrives,
                    # and matching on number plus a time window is guesswork
                    # the moment two people ring at once.
                    with db.txn() as c:
                        c.execute(
                            "INSERT INTO calls (id,from_e164,contact_id,started_at) "
                            "VALUES (?,?,?,?)",
                            (call_id, caller, who.get("contact_id"),
                             datetime.now().isoformat(timespec="seconds")))
                        c.execute(
                            "UPDATE calls SET dealer_id=?, twilio_sid=?, "
                            "connected=1 WHERE id=?",
                            (dealer_id, start.get("callSid"), call_id))

                    session = await _session_service.create_session(
                        app_name=APP_NAME,
                        user_id=caller,
                        # call_id and dealer_id ride along so a tool can write
                        # against the call it is part of. Without call_id,
                        # set_intent could only record into session state,
                        # which dies with the process.
                        state={"caller_phone": caller, "caller": who,
                               "call_id": call_id, "dealer_id": dealer_id},
                    )
                    started.set()

                    # Whoever answers a phone speaks first. Gemini Live waits
                    # for input by default, so without this nudge the agent
                    # sits in silence while the caller wonders if it connected.
                    # None of this text is spoken; it is what the agent knows
                    # as it opens its mouth.
                    queue.send_content(types.Content(
                        role="user",
                        parts=[types.Part(text=_opening_brief(who))],
                    ))

                elif event == "media":
                    queue.send_realtime(
                        types.Blob(
                            data=to_gemini(msg["media"]["payload"]),
                            mime_type="audio/pcm;rate=16000",
                        )
                    )

                elif event == "dtmf":
                    # Nobody is told to press anything. Touch-tone menus lose
                    # 67% of callers inside 90 seconds, so this is an escape
                    # hatch, not a front door: a commercial kitchen at 6pm is
                    # loud, and sometimes speech simply will not get through.
                    digit = msg.get("dtmf", {}).get("digit", "")
                    meaning = {"1": "service", "2": "order",
                               "3": "product", "4": "supplier"}.get(digit)
                    if meaning:
                        queue.send_content(types.Content(
                            role="user",
                            parts=[types.Part(text=(
                                f"[The caller pressed {digit}. They want: {meaning}. "
                                f"Acknowledge briefly and continue in that flow.]"
                            ))],
                        ))

                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            queue.close()
            # Close the call row with everything that was said.
            try:
                with db.txn() as c:
                    c.execute(
                        "UPDATE calls SET ended_at=?, transcript=? WHERE id=?",
                        (datetime.now().isoformat(timespec="seconds"),
                         "\n".join(f"{w}: {x}" for w, x in transcript) or None,
                         call_id))
            except Exception:
                pass

            # What became of it, read back out of the tables the call wrote.
            # Runs after the transcript is saved, because it counts turns and
            # repeated lines from it. Never allowed to raise: the call is over
            # either way and losing the recording is not worth an exception in
            # a socket teardown.
            try:
                from ..review import settle

                out = settle(call_id)
                print(f"[review] {out.get('outcome')} "
                      f"(intent={out.get('intent')}, "
                      f"resolved={out.get('resolved')})", flush=True)
                events.publish(dealer_id, "call_end",
                               text=f"call ended: {out.get('outcome')}")
            except Exception as e:
                print(f"[review] settle failed: {type(e).__name__}: {e}",
                      flush=True)
                events.publish(dealer_id, "call_end", text="call ended")
            # The call is over. Nothing should still be playing into a socket
            # that is closing.
            comfort.stop()

            # What they said becomes retrievable next time.
            if session is not None:
                try:
                    fresh = await _session_service.get_session(
                        app_name=APP_NAME, user_id=caller, session_id=session.id
                    )
                    await MEMORY.add_session_to_memory(fresh or session)
                except Exception:
                    pass

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
                # Kill the lookup music too. Being talked over by reassurance
                # noise is worse than the silence it was covering.
                comfort.stop()
                await ws.send_text(json.dumps(
                    {"event": "clear", "streamSid": stream_sid}
                ))
                continue

            # Both sides of the conversation, kept. The caller's words are how
            # the NEXT caller will describe the same fault, which is what the
            # corpus is searched with, so throwing them away would be throwing
            # away the thing that makes retrieval work.
            for attr, who in (("input_transcription", "caller"),
                              ("output_transcription", "agent")):
                tr = getattr(event, attr, None)
                text = (getattr(tr, "text", "") or "").strip() if tr else ""
                if text:
                    transcript.append((who, text))
                    print(f"[{who}] {text}", flush=True)
                    events.publish(dealer_id, who, text=text)

            for part in (getattr(getattr(event, "content", None), "parts", None) or []):
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    print(f"[tool] {fc.name}({dict(fc.args or {})})", flush=True)
                    events.publish(dealer_id, "tool", text=f"{fc.name}()")
                    # The slow ones. assess_job is three model calls plus an
                    # embedding call, and the caller hears nothing at all while
                    # it runs. Anything quick finishes inside the lead-in and
                    # never makes a sound.
                    if fc.name in SLOW_TOOLS:
                        comfort.start()

                blob = getattr(part, "inline_data", None)
                if blob is None or not blob.data:
                    continue
                # The agent has an answer, so the gap is over.
                comfort.stop()
                await ws.send_text(json.dumps({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": to_twilio(blob.data)},
                }))

    started = asyncio.Event()
    await asyncio.gather(pump_from_twilio(), pump_to_twilio())
