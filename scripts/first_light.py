"""First light: get Gemini Live talking through ADK, with no phone involved.

This is the one experiment that decides the project. If this does not produce
audio, nothing downstream is reachable, and it is worth knowing on day one
rather than day nine.

It deliberately does three jobs at once:

  1. Proves auth, region and model access are correct.
  2. Proves the Runner's live loop yields audio through our real front agent,
     tools and all - so a tool call during a live turn is exercised too.
  3. **Prints the actual shape of the events.** The Twilio bridge currently
     guesses at `event.interrupted` via getattr. This tells us what ADK really
     sends, which closes the highest-risk unknown in the architecture.

Usage
-----
    .venv/Scripts/python.exe scripts/first_light.py
    .venv/Scripts/python.exe scripts/first_light.py --inspect
    .venv/Scripts/python.exe scripts/first_light.py --say "my walk-in is warm"

Output lands in recordings/first_light.wav - play it.

Cost note: this sends TEXT in and gets AUDIO out, which is the cheap direction.
Audio output is ~$12/1M tokens at ~25 tokens/sec, so a short exchange is
fractions of a cent. Do not loop this in a while-true.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.adk.agents import LiveRequestQueue, RunConfig  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from src.agents import root_agent  # noqa: E402
from src.config import APP_NAME, GEMINI_OUT_RATE, settings  # noqa: E402

OUT = ROOT / "recordings" / "first_light.wav"
CALLER = "+13095550101"          # Pearl Street Kitchen, so identify_caller hits
DEFAULT_SAY = (
    "Hi, this is Pearl Street Kitchen. The reach-in freezer isn't holding "
    "temperature overnight. The display is showing dEF."
)


def preflight() -> None:
    """Fail loudly and usefully rather than 40 lines into a stack trace."""
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in {"1", "TRUE"}
    problems: list[str] = []

    if use_vertex:
        if not os.getenv("GOOGLE_CLOUD_PROJECT"):
            problems.append("GOOGLE_CLOUD_PROJECT is not set")
        loc = os.getenv("GOOGLE_CLOUD_LOCATION", "")
        if loc != "us-central1":
            problems.append(
                f"GOOGLE_CLOUD_LOCATION is {loc or 'unset'}; the Live native-audio "
                "model is us-central1 in the US. Wrong region fails silently."
            )
    elif not os.getenv("GOOGLE_API_KEY"):
        problems.append(
            "Neither GOOGLE_GENAI_USE_VERTEXAI=TRUE (+ project/location) nor "
            "GOOGLE_API_KEY is set"
        )

    if problems:
        print("Preflight failed:\n")
        for p in problems:
            print(f" - {p}")
        print("\nSee docs/setup.md section 1.")
        raise SystemExit(1)


def describe(event, seen: set[str]) -> None:
    """Print any event field we have not seen before.

    The point is to discover the real event surface, especially whatever
    signals interruption, without guessing.
    """
    interesting = [
        a for a in dir(event)
        if not a.startswith("_") and not callable(getattr(event, a, None))
    ]
    new = [a for a in interesting if a not in seen]
    for a in new:
        seen.add(a)
        val = getattr(event, a, None)
        if val is None or val == [] or val == {}:
            continue
        text = repr(val)
        print(f"    [event.{a}] {text[:120]}")


async def main(say: str, inspect: bool) -> None:
    preflight()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    session_service = InMemorySessionService()
    runner = Runner(
        app_name=APP_NAME,
        agent=root_agent,
        session_service=session_service,
    )
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=CALLER,
        state={"caller_phone": CALLER},
    )

    run_config = RunConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    queue = LiveRequestQueue()
    queue.send_content(
        types.Content(role="user", parts=[types.Part(text=say)])
    )

    print(f"model   : {settings.live_model}")
    print(f"agent   : {root_agent.name}")
    print(f"caller  : {CALLER}")
    print(f"said    : {say}\n")
    print("--- events ---")

    audio = bytearray()
    seen: set[str] = set()
    turns = 0

    async def pump() -> None:
        nonlocal turns
        async for event in runner.run_live(
            user_id=CALLER,
            session_id=session.id,
            live_request_queue=queue,
            run_config=run_config,
        ):
            if inspect:
                describe(event, seen)

            if getattr(event, "interrupted", None):
                print("  [INTERRUPTED] <- this is the field the bridge relies on")

            content = getattr(event, "content", None)
            for part in (getattr(content, "parts", None) or []):
                if getattr(part, "text", None):
                    print(f"  [text] {part.text.strip()[:160]}")
                blob = getattr(part, "inline_data", None)
                if blob is not None and getattr(blob, "data", None):
                    audio.extend(blob.data)
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    print(f"  [tool call] {fc.name}({dict(fc.args or {})})")
                fr = getattr(part, "function_response", None)
                if fr is not None:
                    print(f"  [tool result] {fr.name} -> {str(fr.response)[:120]}")

            for attr in ("input_transcription", "output_transcription"):
                tr = getattr(event, attr, None)
                if tr is not None and getattr(tr, "text", None):
                    print(f"  [{attr}] {tr.text}")

            if getattr(event, "turn_complete", False):
                turns += 1
                print(f"  [turn complete] #{turns}  (audio so far: {len(audio):,} bytes)")
                # A tool round trip completes a turn without the model having
                # spoken yet. Keep listening until it actually says something.
                if audio or turns >= 4:
                    break

    try:
        await asyncio.wait_for(pump(), timeout=90)
    except asyncio.TimeoutError:
        print("\n  timed out after 90s with no turn_complete")
    finally:
        queue.close()

    print("\n--- result ---")
    if not audio:
        print("NO AUDIO RECEIVED. The pipe is not working; nothing else matters yet.")
        raise SystemExit(2)

    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(GEMINI_OUT_RATE)
        w.writeframes(bytes(audio))

    seconds = len(audio) / (GEMINI_OUT_RATE * 2)
    print(f"wrote {OUT}  ({len(audio):,} bytes, {seconds:.1f}s at {GEMINI_OUT_RATE} Hz)")
    print("play it. If it speaks, the hard part of this project works.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--say", default=DEFAULT_SAY)
    ap.add_argument("--inspect", action="store_true",
                    help="dump every event field, to learn the real event shape")
    args = ap.parse_args()
    asyncio.run(main(args.say, args.inspect))
