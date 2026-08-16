"""Transcoding between Twilio and Gemini Live.

Twilio Media Streams:  8 kHz mu-law, base64, 20 ms frames
Gemini Live input:    16 kHz signed 16-bit PCM
Gemini Live output:   24 kHz signed 16-bit PCM

NOTE: `audioop` is deprecated and removed in Python 3.13. We are on 3.12, which
is fine, but if this ever moves to 3.13 swap in `audioop-lts` (a drop-in
backport) rather than rewriting these four functions.
"""

from __future__ import annotations

import audioop
import base64

from ..config import GEMINI_IN_RATE, GEMINI_OUT_RATE, TWILIO_RATE

_SAMPLE_WIDTH = 2  # 16-bit


class _Resampler:
    """Holds ratecv state so resampling is continuous across frames.

    Without carrying state between frames you get a click at every 20 ms
    boundary, which sounds like a bad line and makes the demo feel cheap.
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self.src = src_rate
        self.dst = dst_rate
        self._state = None

    def __call__(self, pcm: bytes) -> bytes:
        out, self._state = audioop.ratecv(
            pcm, _SAMPLE_WIDTH, 1, self.src, self.dst, self._state
        )
        return out


def make_inbound() -> "callable[[str], bytes]":
    """Twilio base64 mu-law frame -> 16 kHz PCM bytes for Gemini."""
    resample = _Resampler(TWILIO_RATE, GEMINI_IN_RATE)

    def convert(b64_payload: str) -> bytes:
        mulaw = base64.b64decode(b64_payload)
        pcm8k = audioop.ulaw2lin(mulaw, _SAMPLE_WIDTH)
        return resample(pcm8k)

    return convert


def make_outbound() -> "callable[[bytes], str]":
    """Gemini 24 kHz PCM -> Twilio base64 mu-law frame."""
    resample = _Resampler(GEMINI_OUT_RATE, TWILIO_RATE)

    def convert(pcm24k: bytes) -> str:
        pcm8k = resample(pcm24k)
        mulaw = audioop.lin2ulaw(pcm8k, _SAMPLE_WIDTH)
        return base64.b64encode(mulaw).decode("ascii")

    return convert
