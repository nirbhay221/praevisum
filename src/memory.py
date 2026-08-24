"""The corpus of what this company has actually learned by fixing things.

Why this is not just a database query
-------------------------------------
A caller says "it's warm in the morning but fine by lunch".
The technician who fixed it last year wrote "coil iced solid, termination
thermostat had drifted out of spec".

Those two sentences share no useful words. Keyword matching returns nothing,
which is exactly the moment the briefing is worth the most. So retrieval has
to be about meaning, not tokens.

`RepairIndex` is the seam. `LocalRepairIndex` runs offline with no credentials
so the whole loop is testable today; `VertexRagRepairIndex` swaps in behind the
same three methods when we want real embeddings. Nothing above this file knows
which one it is talking to.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .domain.models import Repair

_WORD = re.compile(r"[a-z0-9]+")

# Words that appear in nearly every refrigeration complaint carry no signal.
_STOP = {
    "the", "and", "was", "were", "has", "had", "not", "but", "for", "with",
    "this", "that", "unit", "it", "is", "on", "in", "of", "to", "a", "at",
    "customer", "reported", "said", "again", "still",
}


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOP]


@dataclass
class Hit:
    repair: Repair
    score: float
    why: str


class RepairIndex(Protocol):
    def add(self, repair: Repair) -> None: ...
    def search(self, query: str, *, manufacturer: str | None = None,
               model: str | None = None, limit: int = 5) -> list[Hit]: ...
    def size(self) -> int: ...


class LocalRepairIndex:
    """TF-IDF cosine over the repair narratives. No dependencies, no network.

    Not as good as real embeddings. Much better than substring matching, and
    good enough to prove the loop closes.
    """

    def __init__(self) -> None:
        self._repairs: list[Repair] = []
        self._docs: list[Counter] = []
        self._df: Counter = Counter()

    # -- text that represents a repair for retrieval purposes ---------------
    @staticmethod
    def _document(r: Repair) -> str:
        # The customer's words and the technician's words both matter: the next
        # caller will sound like the former, the truth is in the latter.
        return " ".join(filter(None, [r.reported_symptom, r.found_cause, r.error_code or ""]))

    def add(self, repair: Repair) -> None:
        tf = Counter(_tokens(self._document(repair)))
        self._repairs.append(repair)
        self._docs.append(tf)
        for term in tf:
            self._df[term] += 1

    def size(self) -> int:
        return len(self._repairs)

    def _idf(self, term: str) -> float:
        n = len(self._repairs)
        return math.log((n + 1) / (self._df.get(term, 0) + 1)) + 1.0

    def _vector(self, tf: Counter) -> dict[str, float]:
        return {t: c * self._idf(t) for t, c in tf.items()}

    def search(self, query: str, *, manufacturer: str | None = None,
               model: str | None = None, limit: int = 5) -> list[Hit]:
        if not self._repairs:
            return []

        qv = self._vector(Counter(_tokens(query)))
        qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0

        hits: list[Hit] = []
        for repair, tf in zip(self._repairs, self._docs):
            if manufacturer and repair.manufacturer.lower() != manufacturer.lower():
                continue
            if model and repair.model.lower() != model.lower():
                continue

            dv = self._vector(tf)
            dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
            shared = set(qv) & set(dv)
            if not shared:
                continue

            dot = sum(qv[t] * dv[t] for t in shared)
            score = dot / (qn * dn)
            overlap = sorted(shared, key=lambda t: -qv[t])[:3]
            hits.append(Hit(repair, round(score, 3), "matched on " + ", ".join(overlap)))

        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


class VertexRagRepairIndex:
    """Drop-in for real semantic retrieval.

    Same three methods. `add` writes the narrative into a Vertex AI RAG corpus
    on work-order close; `search` runs semantic retrieval and returns the same
    `Hit` objects. Nothing above this file changes when we switch.
    """

    def __init__(self, corpus: str) -> None:
        self.corpus = corpus
        raise NotImplementedError(
            "Vertex RAG index not wired yet. See README honesty table."
        )


# One index per dealer. Not a nicety: a refrigeration company and an IT
# warranty provider are separate businesses, and the first test of a shared
# index had a caller with a warm walk-in being told about Dell LCD panels at
# 0.449 confidence. What a company has learned in its own vans is the single
# thing it would never share, so the separation is structural rather than a
# filter somebody could forget to apply.
INDEXES: dict[str, RepairIndex] = {}

# Kept for the single-dealer path and for scripts. Points at whichever dealer
# was loaded last when only one exists.
INDEX: RepairIndex = LocalRepairIndex()


def index_for(dealer_id: str | None) -> RepairIndex:
    """The corpus belonging to one dealer, and only that dealer."""
    if dealer_id and dealer_id in INDEXES:
        return INDEXES[dealer_id]
    return INDEX


def load_from_db() -> int:
    """Fill one index per dealer from the repairs table.

    Rebuilt at startup. Every closed visit adds a row there and a document to
    exactly one dealer's index, so the two never drift and never mix.
    """
    from .domain.models import Repair
    from . import db

    global INDEX, INDEXES
    INDEXES = {}
    total = 0

    with db.connect() as c:
        rows = c.execute(
            """SELECT id, asset_id, manufacturer, model_number, reported_symptom,
                      error_code, found_cause, tech_note, parts_consumed,
                      labor_hours, closed_on, technician_id, dealer_id
               FROM repairs ORDER BY closed_on""").fetchall()

    for r in rows:
        narrative = r["found_cause"]
        if r["tech_note"]:
            narrative = f"{narrative}. {r['tech_note']}"
        dealer = r["dealer_id"] or "unassigned"
        INDEXES.setdefault(dealer, LocalRepairIndex()).add(Repair(
            id=r["id"],
            serial=r["asset_id"] or "",
            manufacturer=r["manufacturer"],
            model=r["model_number"],
            reported_symptom=r["reported_symptom"] or "",
            error_code=r["error_code"],
            found_cause=narrative,
            parts_consumed=tuple(
                s for s in (r["parts_consumed"] or "").split(",") if s),
            labor_hours=r["labor_hours"] or 0.0,
            closed_on=r["closed_on"],
            technician_id=r["technician_id"] or "",
        ))
        total += 1

    if INDEXES:
        INDEX = max(INDEXES.values(), key=lambda i: i.size())
    return total
