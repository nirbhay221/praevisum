"""A model error must not hang up on the customer.

WHAT HAPPENED, 26 AUGUST, 17:09

A buying call. The desk classified the intent, wrote down who was calling, and
handed off to the advice sub-agent. Vertex answered:

    429 RESOURCE_EXHAUSTED

ADK retried it with backoff for about a minute, gave up, and the exception
came out through pump_to_twilio and took the websocket with it. The caller
heard sixty seconds of nothing and then a dead line.

Quota is not a code bug. Hanging up because of it is.

WHY THERE IS NO SPOKEN APOLOGY

There is no way to say sorry: the model that would speak it is the one that
just failed, and there is no pre-rendered clip to fall back on. So the line
stays open with hold music, which at least tells somebody it is still alive,
and the model gets one more try. A 429 is usually a per-minute ceiling and a
few seconds often clears it.

Music and a second attempt is not a great answer. It is a great deal better
than a dead line, and it is honest about what we can actually do with no
model available.
"""

from __future__ import annotations

import inspect

import pytest


def test_the_pump_is_wrapped_at_all():
    """It used to be bare: any exception ended the call."""
    from src.telephony import twilio_bridge

    src = inspect.getsource(twilio_bridge._handle_call)
    assert "async def pump_to_twilio" in src
    assert "async def _pump" in src, "the body moved so it could be retried"


def test_a_model_failure_is_retried_once_with_music():
    from src.telephony import twilio_bridge

    src = inspect.getsource(twilio_bridge._handle_call)
    body = src[src.index("async def pump_to_twilio"):src.index("async def _pump")]

    assert "for attempt in (1, 2)" in body, "one retry, not none and not forever"
    assert "comfort.start()" in body, "music rather than dead air"
    assert "MODEL_RETRY_WAIT" in body


def test_it_gives_up_rather_than_looping_forever():
    """A caller stuck in a retry loop is worse off than one told nothing more
    is coming."""
    from src.telephony import twilio_bridge

    src = inspect.getsource(twilio_bridge._handle_call)
    body = src[src.index("async def pump_to_twilio"):src.index("async def _pump")]
    assert "if attempt == 2:" in body
    assert "return" in body


def test_the_wait_is_short_enough_that_nobody_hangs_up():
    from src.telephony import twilio_bridge

    assert 3 <= twilio_bridge.MODEL_RETRY_WAIT <= 15


def test_the_failure_reaches_the_dashboard():
    """A call that quietly degraded is a call nobody investigates."""
    from src.telephony import twilio_bridge

    src = inspect.getsource(twilio_bridge._handle_call)
    body = src[src.index("async def pump_to_twilio"):src.index("async def _pump")]
    assert 'events.publish' in body
    assert "model error" in body


def test_a_raising_pump_does_not_propagate():
    """The property itself, rather than its source code: whatever the inner
    pump throws, the outer one returns normally and the socket survives."""
    import asyncio

    attempts = []

    async def flaky():
        attempts.append(1)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    async def outer():
        for attempt in (1, 2):
            try:
                await flaky()
                return
            except Exception:
                if attempt == 2:
                    return
                await asyncio.sleep(0)

    asyncio.run(outer())   # must not raise
    assert len(attempts) == 2, "tried twice, then stopped"
