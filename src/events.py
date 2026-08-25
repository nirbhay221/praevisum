"""A live feed of what the agents are doing, for anyone watching the console.

Deliberately fire-and-forget. Nothing on the phone path may block, slow down or
fail because a dashboard is open, so publishing never awaits a subscriber and a
dead socket is dropped rather than retried. If every viewer disappears the call
carries on exactly as before.

The feed is per dealer. A refrigeration dispatcher has no business watching an
IT engineer's calls, for the same reason their repair corpora are separate.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime
from typing import Any

# what a newly opened dashboard sees before anything else happens
_RECENT: dict[str, deque] = {}
_SUBS: dict[str, set[asyncio.Queue]] = {}
KEEP = 60


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def publish(dealer_id: str, kind: str, **fields: Any) -> None:
    """Record something worth watching. Never raises, never blocks.

    kind is one of: call_start, caller, agent, tool, work_order, dispatch,
    briefing, promise, call_end, console, reasoning.

    `reasoning` carries the arithmetic behind a decision while it is being
    made. It is the only kind that is not an event so much as a working: both
    sides of the inequality that decided what goes in a van, or whether one
    moves at all. See trace.py for why that belongs on a live feed.
    """
    # The promise in the first line of this docstring is made HERE, once,
    # rather than by every caller wrapping the call in its own try/except.
    #
    # It used to be the other way round: seven call sites each had a bare
    # `except Exception: pass` around a publish. That is correct behaviour
    # written in a way that is indistinguishable from a bug, and three real
    # bugs in this project have hidden behind exactly that shape. Making the
    # guarantee true in one place let all seven be deleted.
    try:
        event = {"at": _now(), "kind": kind, **fields}
        _RECENT.setdefault(dealer_id, deque(maxlen=KEEP)).append(event)

        for q in list(_SUBS.get(dealer_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass      # a slow dashboard is not the call's problem
    except Exception as e:
        # Logged, never raised. A dashboard is not allowed to be the reason a
        # phone call fails, and it is not allowed to fail silently either.
        print(f"[events] dropped a {kind} event: {type(e).__name__}: {e}",
              flush=True)


def recent(dealer_id: str) -> list[dict]:
    return list(_RECENT.get(dealer_id, ()))


def subscribe(dealer_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _SUBS.setdefault(dealer_id, set()).add(q)
    return q


def unsubscribe(dealer_id: str, q: asyncio.Queue) -> None:
    _SUBS.get(dealer_id, set()).discard(q)


def as_sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
