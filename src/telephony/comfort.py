"""Proof that the line is still alive while the desk is looking something up.

THE PROBLEM

`assess_job` is three model calls, several database reads and an embedding
call. It is the most valuable thing the system does and it takes seconds. While
it runs, nothing goes down the wire, so the caller gets dead air and assumes
the call dropped.

The agent is already instructed not to go silent there. It goes silent anyway.
Asking a model to remember something at exactly the wrong moment is not a
mechanism, so this fills the gap from code instead.

WHY MUSIC AND NOT A SPOKEN LINE

A spoken filler ("let me pull that up") is what a human would do, and it is the
better answer for a two second gap. It needs a text-to-speech voice recorded
ahead of time, which is a cost and an asset we do not have yet.

What we do have is the hold track, which until now only played on the failure
path, when the agent could not be reached at all. It never played on a working
call. It is already 8 kHz mono, which is exactly what the phone line wants, so
it needs no resampling to be useful here.

THE LEAD-IN IS THE IMPORTANT PART

Music that starts instantly is worse than silence. It says "you have been
parked, go and make a coffee", which is a promise of a long wait, and then the
agent comes back three seconds later and talks over the top of it.

So nothing happens for the first stretch of a pause. A short lookup stays
silent and the caller never notices. Only a pause long enough to be genuinely
uncomfortable gets anything, and by then the caller does need telling that
somebody is still there.
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import wave
from pathlib import Path

from ..config import TWILIO_RATE

_SAMPLE_WIDTH = 2
FRAME_MS = 20

# Samples in one Twilio frame. 8000 Hz at 20 ms is 160.
FRAME_SAMPLES = TWILIO_RATE * FRAME_MS // 1000

# How long a pause has to run before anything is played. Long enough that an
# ordinary lookup finishes in silence unnoticed, short enough that a caller
# has not yet decided the call is dead.
LEAD_IN = 1.6

# Quieter than the agent's voice. This is background, and a caller talking over
# it must still be understood by the model, so it cannot be loud enough to
# swamp them on a speakerphone in a commercial kitchen.
GAIN = 0.28

HOLD_WAV = Path(__file__).resolve().parents[2] / "assets" / "hold.wav"

_frames: list[str] | None = None


def _load() -> list[str]:
    """The hold track as ready-to-send Twilio frames, converted once.

    Done at first use rather than per call. Converting 32 seconds of audio on
    every pause would put real work on the exact code path that exists because
    the caller is already waiting.
    """
    global _frames
    if _frames is not None:
        return _frames

    try:
        with wave.open(str(HOLD_WAV), "rb") as w:
            if w.getframerate() != TWILIO_RATE or w.getnchannels() != 1:
                # Not worth resampling here. If the asset stops being 8 kHz
                # mono, the fix belongs in whatever produced it.
                _frames = []
                return _frames
            pcm = w.readframes(w.getnframes())
    except (OSError, wave.Error):
        _frames = []
        return _frames

    pcm = audioop.mul(pcm, _SAMPLE_WIDTH, GAIN)

    step = FRAME_SAMPLES * _SAMPLE_WIDTH
    out: list[str] = []
    for i in range(0, len(pcm) - step + 1, step):
        mulaw = audioop.lin2ulaw(pcm[i:i + step], _SAMPLE_WIDTH)
        out.append(base64.b64encode(mulaw).decode("ascii"))
    _frames = out
    return _frames


class Comfort:
    """Plays into the gap, and gets out of the way the moment anyone speaks.

    Every path that produces sound has to stop this first: the agent starting
    its answer, and the caller interrupting. Two things talking at once on a
    phone line is worse than either of the problems this solves.
    """

    def __init__(self, ws, stream_sid_getter) -> None:
        self._ws = ws
        self._sid = stream_sid_getter
        self._task: asyncio.Task | None = None

    @property
    def playing(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.playing:
            return
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            await asyncio.sleep(LEAD_IN)
            frames = _load()
            if not frames:
                return
            i = 0
            while True:
                sid = self._sid()
                if sid:
                    await self._ws.send_text(json.dumps({
                        "event": "media",
                        "streamSid": sid,
                        "media": {"payload": frames[i % len(frames)]},
                    }))
                i += 1
                # Real time. Sending faster than the line plays would pile up
                # in Twilio's buffer and keep playing long after the answer is
                # ready, which is the failure this is supposed to prevent.
                await asyncio.sleep(FRAME_MS / 1000)
        except asyncio.CancelledError:
            pass
        except Exception:
            # Never let the reassurance noise be the thing that ends a call.
            pass
