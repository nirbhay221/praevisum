"""Memory, in the ADK sense: what this company knows, retrievable by a caller.

Most agent memory remembers *a person*. That is the obvious design and it is
half the problem. A restaurant owner ringing about a warm walk-in does not
mainly need us to remember them. They need us to remember the machine, and
every other machine like it that any technician has ever opened.

So `PraevisumMemory` searches two things at once and returns them as one
result:

  1. **Institutional memory** - the closed repair corpus. What was actually
     found, and which parts were actually fitted, across the whole installed
     base. This is the half that makes a briefing worth sending.
  2. **Personal memory** - what this caller has said to us on previous calls.
     Their words, which is how they will describe the same fault next time.

Both are keyed the same way ADK expects, so `load_memory` works unmodified and
the agent does not know or care which half an answer came from.

Sessions are written back on call end, so a conversation that happens today is
retrievable tomorrow. That is the loop closing on the conversational side, the
way `close_work_order` closes it on the repair side.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from google.adk.events.event import Event
from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.genai import types

from . import db
import src.memory as _mem


def _entry(text: str, author: str, when: str, **meta: Any) -> MemoryEntry:
    return MemoryEntry(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        author=author,
        timestamp=when,
        custom_metadata=dict(meta),
    )


# How many of a caller's own past sentences are worth putting in front of the
# agent. More than this and the opening stops being context and becomes a
# transcript the model reads back out loud.
REMEMBER = 4


def _remember(phone: str, said: str, dealer: str = "", from_call: str = "") -> None:
    """Write down what a caller told us. Never raises.

    Their words rather than a summary, for the same reason the repair corpus
    keeps a technician's phrasing: it is how they will describe the same thing
    next time, and it is what retrieval is searched with.
    """
    said = (said or "").strip()
    if not phone or not said:
        return
    try:
        with db.txn() as c:
            c.execute(
                """INSERT INTO caller_memory (phone,dealer_id,from_call,said,at)
                   VALUES (?,?,?,?,?)""",
                (phone, dealer or None, from_call or None, said[:600],
                 datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        # Losing a memory must not end a call, and it must not be silent
        # either. A quiet failure here is how the loop appears to close for a
        # second time without actually closing.
        print(f"[recall] could not remember what {phone} said: "
              f"{type(e).__name__}: {e}", flush=True)


def _remembered(phone: str) -> list[MemoryEntry]:
    """What this caller has told us before, most recent last."""
    try:
        with db.connect() as c:
            rows = c.execute(
                # id breaks the tie, because `at` has second resolution and
                # several things get remembered inside one call. Ordering on
                # the timestamp alone returned them in arbitrary order, so the
                # agent could be handed the first thing a caller said as
                # though it were the most recent.
                """SELECT said, at FROM caller_memory
                   WHERE phone = ? ORDER BY at DESC, id DESC LIMIT ?""",
                (phone, REMEMBER)).fetchall()
    except Exception as e:
        print(f"[recall] could not read what {phone} told us: "
              f"{type(e).__name__}: {e}", flush=True)
        return []

    return [_entry("On a previous call they said: " + r["said"],
                   author="caller", when=r["at"], kind="personal")
            for r in reversed(rows)]


class PraevisumMemory(BaseMemoryService):
    """Institutional memory first, personal memory second."""

    def __init__(self) -> None:
        # Nothing is held here any more.
        #
        # This was `self._said: dict[str, list[MemoryEntry]]`, and the class
        # docstring above claimed a conversation today was retrievable
        # tomorrow. It was retrievable until the next restart, which on this
        # deployment means the next deploy, so the claim had never been true.
        #
        # The institutional half was always in the database. The personal half
        # is now too, which is the only reason the sentence above is honest.
        pass

    @staticmethod
    def _dealer_for(user_id: str) -> str:
        """Which business this caller belongs to, from the number they rang.

        A number can exist under two dealers - a restaurant owner rings the
        refrigeration company about the walk-in and the IT company about the
        laptops - so this resolves through the call record rather than by
        assuming there is only one answer.
        """
        try:
            with db.connect() as c:
                row = c.execute(
                    """SELECT dealer_id FROM calls WHERE from_e164=?
                       AND dealer_id IS NOT NULL
                       ORDER BY started_at DESC LIMIT 1""", (user_id,)).fetchone()
                if row:
                    return row["dealer_id"]
        except Exception as e:
            # This one is NOT harmless and must never be silent.
            #
            # Falling back to D-REF on a database error means an IT caller can
            # be answered out of the refrigeration corpus. That is the exact
            # leak test_isolation exists to prevent, arriving through the back
            # door as a swallowed exception rather than a bad query.
            #
            # The fallback stays, because a caller with no answer is worse than
            # a caller with the wrong dealer's default. But it says so.
            print(f"[recall] could not resolve the dealer for {user_id}, "
                  f"falling back to D-REF: {type(e).__name__}: {e}", flush=True)
        return "D-REF"

    # ---- reading ------------------------------------------------------

    async def search_memory(self, *, app_name: str, user_id: str,
                            query: str) -> SearchMemoryResponse:
        out: list[MemoryEntry] = []

        # 1. what this dealer has learned, from anyone's visits. Scoped by
        # dealer: a refrigeration company and an IT warranty provider share
        # the public catalogue and nothing else.
        dealer = self._dealer_for(user_id)
        with db.connect() as c:
            names = {r["sku"]: r["name"] for r in c.execute(
                "SELECT sku, name FROM parts")}

        for hit in _mem.index_for(dealer).search(query, limit=4):
            r = hit.repair
            parts = ", ".join(names.get(s, s) for s in r.parts_consumed) or "no parts"
            out.append(_entry(
                f"On {r.closed_on} a {r.manufacturer} {r.model} reported as "
                f'"{r.reported_symptom}" turned out to be: {r.found_cause}. '
                f"Fitted: {parts}.",
                author="service history",
                when=r.closed_on,
                kind="institutional",
                serial=r.serial,
                score=hit.score,
            ))

        # 2. what this particular caller has told us before, read back out of
        # the database rather than a dict that empties on restart
        out.extend(_remembered(user_id))

        return SearchMemoryResponse(memories=out)

    # ---- writing ------------------------------------------------------

    async def add_session_to_memory(self, session: Session) -> None:
        """Called when a call ends. Their words become tomorrow's retrieval."""
        phone = str(session.state.get("caller_phone") or session.user_id or "")
        if not phone:
            return

        spoken = [
            p.text.strip()
            for e in (session.events or [])
            if getattr(e, "content", None) and getattr(e.content, "role", "") == "user"
            for p in (e.content.parts or [])
            if getattr(p, "text", None) and p.text.strip()
            and not p.text.strip().startswith("[")   # skip our own stage directions
        ]
        if not spoken:
            return

        _remember(phone, " ".join(spoken)[:600],
                  dealer=self._dealer_for(phone),
                  from_call=str(session.state.get("call_id") or ""))

    async def add_memory(self, *, app_name: str, user_id: str,
                         memories: Sequence[MemoryEntry],
                         custom_metadata: Mapping[str, object] | None = None) -> None:
        for m in memories:
            for part in (getattr(m.content, "parts", None) or []):
                if getattr(part, "text", None):
                    _remember(user_id, part.text,
                              dealer=self._dealer_for(user_id))

    async def add_events_to_memory(self, *, app_name: str, user_id: str,
                                   events: Sequence[Event],
                                   session_id: str | None = None,
                                   custom_metadata: Mapping[str, object] | None = None) -> None:
        for e in events:
            content = getattr(e, "content", None)
            for p in (getattr(content, "parts", None) or []):
                if getattr(p, "text", None):
                    _remember(user_id, p.text,
                              dealer=self._dealer_for(user_id),
                              from_call=session_id or "")


MEMORY = PraevisumMemory()
