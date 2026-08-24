"""Filling the silence during a lookup, without talking over anybody.

The failure this guards against is not "no music". It is music that keeps
playing while the agent is answering, or while the caller is talking. Two
voices at once on a phone line is worse than the dead air it replaced, and it
would be found by a human on a real call rather than by anything here, so the
stop paths get more attention than the start path.
"""

from __future__ import annotations

import asyncio
import json

import pytest


class FakeWS:
    """Collects what would have gone down the wire."""

    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    def media_frames(self):
        return [m for m in self.sent if m.get("event") == "media"]


def _comfort(sid="SID-1", lead_in=0.02):
    from src.telephony import comfort as C

    C.LEAD_IN = lead_in
    return C, C.Comfort(FakeWS(), lambda: sid)


def test_the_track_converts_to_phone_frames():
    """The asset must be usable as-is, with no resampling on the call path."""
    from src.telephony import comfort as C

    frames = C._load()
    assert frames, "the hold track produced no frames"
    # 32.8 seconds at 20 ms per frame
    assert len(frames) > 1000

    import base64
    raw = base64.b64decode(frames[0])
    assert len(raw) == C.FRAME_SAMPLES, "not a 20 ms mu-law frame"


def test_nothing_plays_during_a_short_lookup():
    """The point of the lead-in.

    A lookup that finishes quickly must make no sound at all. Music that
    starts instantly says "you have been parked", promising a long wait that
    then does not happen.
    """
    C, c = _comfort(lead_in=0.25)

    async def go():
        c.start()
        await asyncio.sleep(0.05)      # a fast tool returns
        c.stop()
        await asyncio.sleep(0.30)      # well past the lead-in

    asyncio.run(go())
    assert c._ws.media_frames() == []


def test_a_long_lookup_eventually_plays():
    C, c = _comfort(lead_in=0.02)

    async def go():
        c.start()
        await asyncio.sleep(0.20)
        c.stop()

    asyncio.run(go())
    assert c._ws.media_frames(), "the caller got dead air on a long lookup"


def test_it_stops_the_moment_the_agent_answers():
    """The gap is over the instant there is something to say."""
    C, c = _comfort(lead_in=0.02)

    async def go():
        c.start()
        await asyncio.sleep(0.15)
        c.stop()
        played = len(c._ws.media_frames())
        await asyncio.sleep(0.15)      # nothing more may arrive
        return played, len(c._ws.media_frames())

    played, after = asyncio.run(go())
    assert played > 0
    assert after == played, "kept playing over the agent's answer"


def test_stopping_twice_is_harmless():
    C, c = _comfort()

    async def go():
        c.start()
        await asyncio.sleep(0.05)
        c.stop()
        c.stop()

    asyncio.run(go())
    assert not c.playing


def test_starting_twice_does_not_double_up():
    """Two overlapping tracks would be unmistakable on the line."""
    C, c = _comfort(lead_in=0.02)

    async def go():
        c.start()
        c.start()
        await asyncio.sleep(0.12)
        c.stop()

    asyncio.run(go())
    # one player at 20 ms per frame over ~100 ms; two would roughly double it
    assert len(c._ws.media_frames()) < 12


def test_it_plays_at_real_time_not_as_fast_as_possible():
    """Frames must go out at the rate the line plays them.

    Sending as fast as the loop allows would pile up in Twilio's buffer and
    keep playing long after the answer is ready, which is a worse version of
    the problem this exists to solve.
    """
    C, c = _comfort(lead_in=0.02)

    async def go():
        c.start()
        await asyncio.sleep(0.22)
        c.stop()

    asyncio.run(go())
    n = len(c._ws.media_frames())
    # ~200 ms of playing at 20 ms a frame is about 10
    assert 3 <= n <= 20, f"sent {n} frames, expected roughly ten"


def test_nothing_is_sent_before_the_stream_exists():
    """A frame with no stream id is meaningless to Twilio."""
    from src.telephony import comfort as C

    C.LEAD_IN = 0.02
    c = C.Comfort(FakeWS(), lambda: None)

    async def go():
        c.start()
        await asyncio.sleep(0.12)
        c.stop()

    asyncio.run(go())
    assert c._ws.media_frames() == []


def test_a_send_failure_never_ends_the_call():
    """Reassurance noise must not be the thing that drops a customer."""
    from src.telephony import comfort as C

    C.LEAD_IN = 0.02

    class BrokenWS:
        async def send_text(self, text):
            raise RuntimeError("socket gone")

    c = C.Comfort(BrokenWS(), lambda: "SID-1")

    async def go():
        c.start()
        await asyncio.sleep(0.10)
        c.stop()

    asyncio.run(go())      # must not raise


def test_the_slow_tools_are_the_ones_that_stall():
    """The list must name the tools that actually take seconds.

    assess_job fans out to three agents plus an embedding call. If it ever
    stops being covered, the dead air comes straight back.
    """
    from src.telephony.twilio_bridge import SLOW_TOOLS

    assert "assessment" in SLOW_TOOLS or "assess_job" in SLOW_TOOLS
    assert "build_briefing" in SLOW_TOOLS


@pytest.mark.parametrize("name", ["check_stock", "current_deals", "set_intent"])
def test_fast_tools_are_not_covered(name):
    """Covering a fast tool would put music into every ordinary turn."""
    from src.telephony.twilio_bridge import SLOW_TOOLS

    assert name not in SLOW_TOOLS
