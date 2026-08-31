"""Keep blocking tools off the event loop.

THE BUG THIS FIXES, WHICH KILLED TWO LIVE CALLS

ADK invokes a synchronous tool like this:

    return target(**args_to_call)          # FunctionTool._invoke_callable

Directly. On the event loop. So any tool that does blocking work stops the
whole process for as long as it takes, and this codebase has plenty:

    geo.locate        time.sleep, then urlopen with a 12 second timeout
    reviews           urlopen, 6 and 8 second timeouts
    textback          urlopen, 90 seconds
    telegram/whatsapp media downloads

While the loop is blocked, uvicorn cannot answer Twilio's websocket keepalive
ping. Twilio waits, gets nothing, and hangs up. Both dropped calls ended the
same way:

    sent 1011 (internal error) keepalive ping timeout; no close frame received

The caller hears silence and then the line goes dead. Nothing in the log says
"a tool blocked the loop", because from the outside it looks exactly like a
network problem.

THE FIX

ADK exposes a contextvar for precisely this: bind a runner and every sync tool
goes through it instead. `asyncio.to_thread` puts them on the default executor,
so the loop stays free to answer pings and to keep pumping audio.

This is bound once, around the whole call, rather than being something each
tool has to remember. A rule that every future tool must remember to be
non-blocking is a rule that gets forgotten, and the forgetting is invisible
until somebody's phone call drops.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Callable, Iterator


async def _in_a_thread(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Run one sync tool off the loop."""
    return await asyncio.to_thread(lambda: fn(**kwargs))


@contextmanager
def tools_off_the_loop() -> Iterator[bool]:
    """Bind ADK's sync-callable runner for the duration of a call.

    Yields whether the binding actually took. The hook is private to ADK, so
    if a future version renames it this degrades to the old behaviour rather
    than crashing a phone call, and says so once in the log instead of
    failing silently.
    """
    try:
        from google.adk.tools.function_tool import _use_sync_callable_runner
    except Exception as e:
        print(f"[offloop] ADK sync-runner hook is gone ({type(e).__name__}), "
              "blocking tools will run on the event loop and a slow one can "
              "drop a call", flush=True)
        yield False
        return

    with _use_sync_callable_runner(_in_a_thread):
        yield True
