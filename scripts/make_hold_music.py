"""Generate the hold music, several tracks of it, so it changes.

WHY MORE THAN ONE TRACK

There was exactly one, 32.8 seconds long, looping forever. Anybody held for
two minutes heard it four times, which is how hold music stops being neutral
and starts being irritating.

The source was already generated rather than licensed, which is the whole
reason it can exist on a service line nobody would buy a music licence for.
Generating four instead of one costs nothing extra in rights and takes a few
minutes.

WHY IT CHANGES EVERY THREE DAYS AND NOT EVERY CALL

Rotating per call sounds clever and is worse: a customer who rings twice in an
afternoon about the same freezer hears a different track and wonders whether
they reached the same company. Three days is long enough to feel like a
station and short enough that a regular caller is not stuck with one loop.

The choice is made from the DATE rather than at random, so every line of this
system answers the same on the same day, and a bug is reproducible.

FORMAT

8 kHz, mono, 16-bit, because that is what a phone line carries and what
comfort.py expects to inject frame by frame. Lyria returns 48 kHz stereo, so
it is downsampled here rather than at play time: doing it once at build is
free and doing it per call is a blocked event loop.

    python -m scripts.make_hold_music            # what it would do
    python -m scripts.make_hold_music --write    # generate them
"""

from __future__ import annotations

import audioop
import base64
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MODEL = "lyria-002"
PHONE_RATE = 8000

# Four moods rather than four variations of one. A station that always sounds
# the same has not changed, whatever the file name says.
TRACKS = {
    "hold_1.wav": ("calm warm instrumental, soft electric piano and light "
                   "pads, unobtrusive, no vocals, steady and reassuring"),
    "hold_2.wav": ("gentle acoustic guitar instrumental, unhurried, no "
                   "vocals, quiet and friendly, small business waiting music"),
    "hold_3.wav": ("soft jazz instrumental with brushed drums and upright "
                   "bass, relaxed, no vocals, low key"),
    "hold_4.wav": ("light ambient instrumental with warm strings, slow, no "
                   "vocals, calm and neutral"),
}


def _token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"],
                          capture_output=True, text=True,
                          shell=True).stdout.strip()


def _generate(prompt: str, tries: int = 4) -> bytes | None:
    """One track, with room between attempts.

    Four generations back to back returned 403 on every one while the same
    call made singly succeeded, so the limit is on rate rather than on
    access. Waiting is the fix; retrying immediately just spends the quota
    again.
    """
    for attempt in range(tries):
        got = _once(prompt)
        if got:
            return got
        wait = 20 * (attempt + 1)
        print(f"    refused, waiting {wait}s before trying again", flush=True)
        time.sleep(wait)
    return None


def _once(prompt: str) -> bytes | None:
    proj = os.getenv("GOOGLE_CLOUD_PROJECT")
    url = (f"https://us-central1-aiplatform.googleapis.com/v1/projects/{proj}"
           f"/locations/us-central1/publishers/google/models/{MODEL}:predict")
    body = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_token()}",
                 "Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=300).read())
        pred = (d.get("predictions") or [{}])[0]
        for key in ("bytesBase64Encoded", "audioContent", "audio"):
            if key in pred:
                return base64.b64decode(pred[key])
    except Exception as e:
        print(f"  could not generate: {type(e).__name__}: {str(e)[:120]}",
              flush=True)
    return None


def _to_phone(raw: bytes) -> bytes:
    """48 kHz stereo down to 8 kHz mono 16-bit.

    Done once at build. comfort.py injects this frame by frame during a live
    call, and resampling there would block the event loop, which is the exact
    bug that dropped two calls before offloop.py existed.
    """
    with wave.open(io.BytesIO(raw), "rb") as w:
        frames = w.readframes(w.getnframes())
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()

    if channels == 2:
        frames = audioop.tomono(frames, width, 0.5, 0.5)
    if rate != PHONE_RATE:
        frames, _ = audioop.ratecv(frames, width, 1, rate, PHONE_RATE, None)

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(PHONE_RATE)
        w.writeframes(frames)
    return out.getvalue()


def load(write: bool = False) -> dict:
    made, skipped = [], []
    for name, prompt in TRACKS.items():
        target = ASSETS / name
        if target.exists():
            skipped.append((name, "already there"))
            continue
        if not write:
            skipped.append((name, "would generate"))
            continue

        raw = _generate(prompt)
        if not raw:
            skipped.append((name, "generation failed"))
            continue

        phone = _to_phone(raw)
        target.write_bytes(phone)
        time.sleep(15)          # be a good citizen between generations

        with wave.open(str(target)) as w:
            secs = w.getnframes() / w.getframerate()
        made.append((name, round(secs, 1), len(phone)))

    return {"made": made, "skipped": skipped, "written": write}


if __name__ == "__main__":
    out = load("--write" in sys.argv)
    for name, secs, size in out["made"]:
        print(f"  {name}  {secs}s  {size // 1024} kB  8 kHz mono")
    for name, why in out["skipped"]:
        print(f"  {name}  {why}")
    if not out["written"]:
        print("  nothing written. Re-run with --write.")
