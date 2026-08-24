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


class PraevisumMemory(BaseMemoryService):
    """Institutional memory first, personal memory second."""

    def __init__(self) -> None:
        # caller phone -> list of things they have said to us before
        self._said: dict[str, list[MemoryEntry]] = {}

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
        except Exception:
            pass
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

        # 2. what this particular caller has told us before
        out.extend(self._said.get(user_id, [])[-4:])

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

        when = datetime.now().isoformat(timespec="seconds")
        self._said.setdefault(phone, []).append(_entry(
            "On a previous call they said: " + " ".join(spoken)[:600],
            author="caller",
            when=when,
            kind="personal",
        ))

    async def add_memory(self, *, app_name: str, user_id: str,
                         memories: Sequence[MemoryEntry],
                         custom_metadata: Mapping[str, object] | None = None) -> None:
        self._said.setdefault(user_id, []).extend(memories)

    async def add_events_to_memory(self, *, app_name: str, user_id: str,
                                   events: Sequence[Event],
                                   session_id: str | None = None,
                                   custom_metadata: Mapping[str, object] | None = None) -> None:
        for e in events:
            content = getattr(e, "content", None)
            for p in (getattr(content, "parts", None) or []):
                if getattr(p, "text", None):
                    self._said.setdefault(user_id, []).append(
                        _entry(p.text, author=e.author or "unknown",
                               when=datetime.now().isoformat(timespec="seconds"),
                               kind="personal")
                    )


MEMORY = PraevisumMemory()
