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
import base64
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
from .speech import Gate

_session_service = InMemorySessionService()

_runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=_session_service,
    memory_service=MEMORY,
)


# Words the transcriber gets wrong on a service line, biased so it does not.
#
# OBSERVED, REPEATEDLY: a caller saying "UPS" was transcribed "USPS" and the
# desk quoted USPS Priority Mail. Said again it came back "UBC". These are not
# rare words on a desk that ships things, and the cost is a wrong carrier on a
# real order.
HEARD_WRONG = [
    "UPS", "USPS", "FedEx", "DHL", "2nd Day Air", "Ground", "Next Day Air",
    "EPA 608", "walk-in cooler", "reach-in freezer", "display cooler",
    "ice machine", "compressor", "evaporator", "condenser", "defrost",
    "thermostat", "purchase order", "work order", "warranty",
]

# WHAT THE CALLER IS ASSUMED TO BE SPEAKING.
#
# `language_auto` lets the transcriber pick per utterance, and on a live call
# one English speaker was rendered as English, then Portuguese, then Hindi,
# then German, in ninety seconds. Once it guessed Portuguese the desk called
# set_language and switched, which is the feature working correctly on a
# false premise.
#
# So the input language is STATED rather than guessed. This is not the same as
# the desk only speaking English: set_language still switches what it SAYS
# when a caller genuinely asks. It is the transcription that stops guessing.
#
# Overridable, because a dealer whose callers really do speak two languages
# should say so rather than have it inferred from an accent.
def _spoken_here() -> list[str]:
    import os

    raw = os.getenv("PRAEVISUM_CALLER_LANGUAGES", "en-US")
    return [x.strip() for x in raw.split(",") if x.strip()] or ["en-US"]


def _start_this_call_on(dealer_id: str, call_id: str = "") -> None:
    """State the vendor at the start of every call, rather than inheriting.

    The routed vendor is a ContextVar so it can reach a sub-agent. A
    ContextVar that is only ever SET is a variable that can be inherited: a
    task created while another call had routed itself to the IT company
    starts life believing it is the IT company.

    Nothing observed that in production, and the test suite proved it is
    possible: one test routed to a vendor and every test after it inherited
    the routing, which is the same mechanism.

    So each call opens by saying which vendor it belongs to. The dialled
    number decides, and route_to_vendor may change it later.
    """
    try:
        from ..tenancy import routed_to

        routed_to(dealer_id or "", call_id)
    except Exception as e:
        print(f"[live] could not set the vendor for this call: "
              f"{type(e).__name__}: {e}", flush=True)


def _run_config() -> RunConfig:
    # `language_auto` is an object, not a flag: setting the codes and leaving
    # it unset is how detection is turned off.
    heard = types.AudioTranscriptionConfig(
        language_codes=_spoken_here(),
        adaptation_phrases=HEARD_WRONG,
    )
    return RunConfig(
        response_modalities=[types.Modality.AUDIO],
        # transcripts on both sides: needed for the work-order record, and for
        # the eval harness later
        input_audio_transcription=heard,
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

    # What we have learned about dealing with THEM, not about their equipment.
    # Only where there were enough past conversations for it to mean anything,
    # so a first-time caller is never told how they usually behave.
    for instruction in (who.get("habits") or {}).get("do_this", []):
        lines.append(instruction)

    lines.append("Greet them by name. Do not read any of this back as a list.]")
    return " ".join(lines)


def _outbound_brief(outreach_id: str) -> str:
    """What the agent knows when WE rang THEM.

    The opening line is read from the queue rather than composed here, for the
    same reason the remote fixes are read from a row: an unattended call to
    somebody about a safety recall is the least forgiving thing this system
    does, and the first sentence is not a place for improvisation.
    """
    from .. import db

    try:
        with db.connect() as c:
            row = c.execute(
                """SELECT q.kind, q.reason, q.evidence, a.name account
                   FROM outreach_queue q JOIN accounts a ON a.id = q.account_id
                   WHERE q.id = ?""", (outreach_id,)).fetchone()
    except Exception as e:
        print(f"[outbound] could not read {outreach_id}: "
              f"{type(e).__name__}: {e}", flush=True)
        row = None

    if row is None:
        return ("[Outbound call connected and we cannot see why it was placed. "
                "Say you are an automated assistant, apologise, and end the "
                "call. Do not invent a reason for ringing them.]")

    from ..outreach import _opening_line

    return (f"[OUTBOUND call to {row['account']}. They did not ring us; we "
            f"rang them. Reason on file: {row['reason']}. "
            f"Open with exactly this and nothing before it: "
            f"\"{_opening_line(row)}\" "
            "Say you are an automated assistant in that first sentence. If "
            "they ask to be taken off the list, agree immediately and without "
            "argument.]")


# Tools worth covering. assess_job fans out to three agents and an embedding
# call; build_briefing does the expected-value work over the whole corpus.
# Everything else returns fast enough that the lead-in swallows it.
# There is no list of slow tools any more, and that is the fix.
#
# It used to be {"assessment", "assess_job", "build_briefing"}. On two real
# calls not one of those ever fired: the tools that actually made the caller
# wait were `scheduling` (twenty-five seconds at worst), `quote_visit` and
# `load_memory`. So the hold music never played once, on any call, ever, while
# 32.8 seconds of loaded audio sat in memory waiting for a name that never came.
#
# The list was redundant to begin with. LEAD_IN already does exactly this job:
# comfort waits 1.6 seconds before making a sound, so anything quick finishes
# first and is never heard. Filtering by name as well added nothing except a
# second place to be wrong, and it drifted the moment a new tool was added.
#
# Start it on every tool call and let the lead-in decide.


# How long to wait before giving the model a second chance, with music
# playing. A 429 is usually a per-minute ceiling, so a few seconds is often
# all it takes. Long enough to be worth trying, short enough that nobody
# assumes the line has gone.
MODEL_RETRY_WAIT = 6

async def handle_call(ws: WebSocket) -> None:
    # Every synchronous tool runs in a thread for the length of this call.
    #
    # ADK calls sync tools straight on the event loop, so one blocking urlopen
    # freezes uvicorn, Twilio's keepalive ping goes unanswered, and the call
    # is dropped mid-sentence. Two live calls died exactly that way. See
    # src/offloop.py.
    from ..offloop import tools_off_the_loop

    with tools_off_the_loop():
        await _handle_call(ws)


async def _handle_call(ws: WebSocket) -> None:
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

    # Tie the reasoning to this call HERE, before the two pumps are started.
    #
    # A context variable is copied into a task when the task is created, not
    # shared with it. Setting this inside pump_from_twilio, where the call
    # actually begins, left pump_to_twilio with the value it had at gather
    # time, which is empty. The tools run in pump_to_twilio, so every decision
    # on every phone call would have been recorded against no call at all and
    # /api/why would have had nothing to show for any of them.
    #
    # It is also what keeps two simultaneous calls apart: each handle_call is
    # its own task, so each gets its own copy.
    from ..trace import call_context

    call_context(call_id)

    # Reads stream_sid through a callable rather than taking its value, because
    # it is still None here and only arrives with the start message.
    comfort = Comfort(ws, lambda: stream_sid)
    gate = Gate()

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
                    # A call WE placed carries the queued reason instead of a
                    # dialled number. The agent must know it rang somebody who
                    # did not ring us, because the first sentence of an
                    # outbound call decides whether they listen or hang up.
                    outreach_id = start.get("customParameters", {}).get("outreach", "")
                    dialled = start.get("customParameters", {}).get("dialled", "")
                    with db.connect() as _c:
                        row = _c.execute(
                            "SELECT id, name FROM dealers WHERE phone_e164=?",
                            (dialled,)).fetchone()
                        if row:
                            dealer_id = row["id"]
                    # Scoped to the business they rang. Without it an IT
                    # customer ringing the refrigeration line was recognised
                    # and had their printers read out to a refrigeration desk.
                    who = resolve(caller, dealer_id)
                    events.publish(dealer_id, "call_start",
                                   text=f"call from {caller}"
                                        + (f" - {who['contact_name']} at {who['account_name']}"
                                           if who.get("known") else " - new caller"))

                    # WHICH VENDOR THIS CALL BELONGS TO, stated rather than
                    # inherited from whatever the last call routed itself to.
                    _start_this_call_on(dealer_id, call_id)

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
                        parts=[types.Part(text=_outbound_brief(outreach_id)
                                          if outreach_id
                                          else _opening_brief(who))],
                    ))

                elif event == "media":
                    # Silence is not forwarded. Twilio sends fifty frames a
                    # second whether anybody is speaking or not, and sending
                    # all of them is what made the live session close with
                    # "sending data too fast, review your flow control".
                    # The gate keeps a trailing silence after speech so the
                    # model can still tell when a turn has ended. See speech.py.
                    payload = msg["media"]["payload"]
                    if gate.open_for(base64.b64decode(payload)):
                        queue.send_realtime(
                            types.Blob(
                                data=to_gemini(payload),
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
            print(f"[audio] {gate.summary}", flush=True)
            queue.close()
            # OFF THE REGISTER THE MOMENT THEY HANG UP, so the next caller
            # cannot inherit this one's company from a stale entry.
            try:
                from ..guards import forget_the_machine
                from ..language import forget_what_they_said
                from ..tenancy import call_ended
                from ..trace import call_over

                call_ended(call_id)
                call_over(call_id)
                forget_the_machine(call_id)
                forget_what_they_said(call_id)
                from ..aftercare import forget_cover_quotes
                from ..quoted import forget_quotes
                from ..shortlist import (forget_shortlist, forget_the_choice,
                                        forget_the_order)

                forget_cover_quotes(call_id)
                # A price is something said to a person in a conversation.
                # It does not survive them hanging up.
                forget_quotes(call_id)
                forget_shortlist(call_id)
                forget_the_choice(call_id)
                forget_the_order(call_id)
            except Exception:
                pass
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
        """The agent's side of the call.

        Wrapped, because a model error used to end the call. Vertex returned
        429 RESOURCE_EXHAUSTED on a sub-agent, ADK retried it with backoff for
        about a minute, gave up, and the exception came out through here and
        took the websocket with it. The customer heard sixty seconds of
        nothing and then a dead line.

        A quota blip is not a reason to hang up on somebody. Say something
        true and keep the line open: they can repeat themselves, or ask for a
        person, and either is better than silence.
        """
        for attempt in (1, 2):
            try:
                await _pump()
                return
            except Exception as e:
                name = type(e).__name__
                print(f"[live] the model stopped answering "
                      f"(attempt {attempt}): {name}: {str(e)[:200]}",
                      flush=True)
                events.publish(dealer_id, "error",
                               text=f"model error: {name}")
                if attempt == 2:
                    return

                # Hold music rather than dead air. There is no way to SAY
                # anything: the model that would speak the apology is the one
                # that just failed, and there is no pre-rendered clip to fall
                # back on. Music at least tells them the line is alive, which
                # is the difference between waiting and hanging up.
                comfort.start()
                await asyncio.sleep(MODEL_RETRY_WAIT)
                comfort.stop()

    async def _pump() -> None:
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
                    # What the caller ACTUALLY said, so a claimed utterance
                    # can be checked against it. The desk fabricated Spanish
                    # to satisfy the language guard within an hour of that
                    # guard being added.
                    if who == "caller":
                        try:
                            from ..language import they_said

                            they_said(call_id, text)
                        except Exception:
                            pass
                    print(f"[{who}] {text}", flush=True)
                    events.publish(dealer_id, who, text=text)

            for part in (getattr(getattr(event, "content", None), "parts", None) or []):
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    print(f"[tool] {fc.name}({dict(fc.args or {})})", flush=True)
                    events.publish(dealer_id, "tool", text=f"{fc.name}()")
                    # Any tool call is a gap the caller is sitting in. The
                    # 1.6 second lead-in means a fast lookup finishes before a
                    # note is played, so this costs nothing on the quick ones
                    # and finally covers the slow ones.
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
