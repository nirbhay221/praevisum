"""A blocking tool must not be able to drop a phone call.

WHAT HAPPENED

ADK invokes a synchronous tool with `return target(**args_to_call)`. Directly,
on the event loop. So a tool that blocks stops the entire process, uvicorn
cannot answer Twilio's websocket keepalive ping, and Twilio hangs up:

    sent 1011 (internal error) keepalive ping timeout; no close frame received

Two live calls ended exactly that way, sixty seconds of silence and then a
dead line. From the outside it is indistinguishable from a network fault,
which is why it survived two rounds of debugging.

The blocking is real and there is a lot of it: geo.locate sleeps and then
waits up to twelve seconds on a geocoder, reviews waits six to eight on a
search API, textback waits ninety on a local model.
"""

from __future__ import annotations

import asyncio
import time

import pytest


def test_a_blocking_tool_does_not_stall_the_loop():
    """The property that matters, stated as the thing the caller experiences:
    while a slow tool runs, the loop must still be free to send audio and
    answer pings."""
    from src.offloop import tools_off_the_loop

    ticks = []

    async def keepalive():
        for _ in range(20):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    def blocking_tool(seconds: float = 0.0):
        time.sleep(seconds)
        return {"ok": True}

    async def go():
        with tools_off_the_loop() as bound:
            if not bound:
                pytest.skip("ADK sync-runner hook not present in this version")
            from google.adk.tools.function_tool import _SYNC_CALLABLE_RUNNER

            runner = _SYNC_CALLABLE_RUNNER.get()
            assert runner is not None, "the runner must actually be bound"

            beat = asyncio.create_task(keepalive())
            out = await runner(blocking_tool, {"seconds": 0.15})
            await beat

        assert out == {"ok": True}

    asyncio.run(go())

    # The loop kept running while the tool blocked. Called inline, every tick
    # would land after the sleep instead of during it.
    assert len(ticks) >= 10
    assert ticks[-1] - ticks[0] > 0.05


def test_the_binding_is_released_afterwards():
    """A worker thread that inherited the binding would send its own nested
    sync calls back through the executor forever."""
    from google.adk.tools.function_tool import _SYNC_CALLABLE_RUNNER

    from src.offloop import tools_off_the_loop

    with tools_off_the_loop() as bound:
        if not bound:
            pytest.skip("hook not present")
        assert _SYNC_CALLABLE_RUNNER.get() is not None

    assert _SYNC_CALLABLE_RUNNER.get() is None


def test_a_missing_hook_degrades_rather_than_crashing(monkeypatch):
    """The hook is private to ADK. If a future version renames it, a phone
    call must still connect: worse latency beats no service."""
    import builtins

    real = builtins.__import__

    def blow_up(name, *a, **k):
        if name == "google.adk.tools.function_tool":
            raise ImportError("moved")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blow_up)

    from src.offloop import tools_off_the_loop

    with tools_off_the_loop() as bound:
        assert bound is False


def test_the_call_path_actually_binds_it():
    """It is bound once around the whole call rather than left to each tool.
    A rule that every future tool must remember to be non-blocking is a rule
    that gets forgotten, and the forgetting is invisible until a call drops."""
    import inspect

    from src.telephony import twilio_bridge

    src = inspect.getsource(twilio_bridge.handle_call)
    assert "tools_off_the_loop" in src
