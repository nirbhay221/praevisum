"""Retrieval by meaning, because callers and technicians do not share words.

Measured on this corpus before writing any of this: four callers describing
faults in ordinary language, matched against what technicians actually wrote.
Word-overlap retrieval found two of the four. The two it missed were the two
where no word was shared at all, including "screen went blank and it shut
itself off" against "control board failed, no output to compressor relay" -
a fault that already has the worst first-visit-fix rate in the book, and needs
a 386 dollar part with a nine day lead time.

So this is not a fashionable upgrade. It is the fix for a measured 50% miss
rate on the one lookup the product exists to perform.

Two details that are easy to get wrong and matter:

  TASK TYPE   documents are embedded as RETRIEVAL_DOCUMENT and queries as
              RETRIEVAL_QUERY. The same text under different task types gives
              different vectors, each tuned for its side of the search. Using
              one type for both measurably degrades retrieval.

  FALLBACK    if Vertex is unreachable the index degrades to word overlap
              rather than failing. A degraded briefing beats a dead phone line.
"""

from __future__ import annotations

import math
from collections import OrderedDict
import os
import struct

from .memory import Hit, LocalRepairIndex, RepairIndex
from .domain.models import Repair

MODEL = os.getenv("PRAEVISUM_EMBED_MODEL", "text-embedding-005")
DIMS = int(os.getenv("PRAEVISUM_EMBED_DIMS", "768"))

_client = None


def _get_client():
    """Vertex or the Gemini API, whichever this environment is configured for.

    project and location are only legal on the Vertex path; passing them to the
    Gemini API path raises, so they are set together or not at all.
    """
    global _client
    if _client is None:
        from google import genai

        from .config import settings  # noqa: F401  (loads .env)

        if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in {"1", "TRUE"}:
            _client = genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            _client = genai.Client()
    return _client


BATCH = int(os.getenv("PRAEVISUM_EMBED_BATCH", "5"))


def embed(texts: list[str], *, is_query: bool) -> list[list[float]]:
    """Embed a batch. Queries and documents get different task types.

    Batches are small and retried with backoff because a fresh Google Cloud
    project has a low default quota on embedding models, and a 429 partway
    through a corpus build would otherwise lose the whole run.
    """
    import time

    from google.genai import types

    cfg = types.EmbedContentConfig(
        task_type="RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT",
        output_dimensionality=DIMS,
    )

    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        delay = 1.0
        for attempt in range(6):
            try:
                r = _get_client().models.embed_content(
                    model=MODEL, contents=chunk, config=cfg)
                out.extend(list(e.values) for e in r.embeddings)
                break
            except Exception as e:
                if "RESOURCE_EXHAUSTED" not in str(e) and "429" not in str(e):
                    raise
                if attempt == 5:
                    raise
                time.sleep(delay)
                delay *= 2
        else:
            raise RuntimeError("embedding quota exhausted after retries")
        if not is_query:
            time.sleep(0.25)      # stay under the per-minute ceiling
    return out


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob)//4}f", blob))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


class VertexEmbeddingIndex:
    """Cosine over stored vectors. Same three methods as the word-overlap index.

    Documents are embedded once and kept in the repairs table, so a restart
    costs nothing. Only the caller's sentence is embedded at call time, which
    is one short API call on a conversation that is already waiting on a model.
    """

    def __init__(self) -> None:
        self._repairs: list[Repair] = []
        self._vecs: list[list[float]] = []
        self._norms: list[float] = []
        self._fallback = LocalRepairIndex()
        # Repairs that have no vector yet, kept separately so a partly
        # embedded corpus can still search all of itself. Without this the
        # unembedded ones are invisible to the vector path and invisible to
        # the fallback too, since the fallback only runs when nothing at all
        # is embedded.
        self._unembedded = LocalRepairIndex()
        # One caller sentence gets searched several times in a single phone
        # call: prior_repairs wants the machine's own history, what_to_load
        # wants the distribution over causes, assess_job wants both. Those are
        # different filters over the same corpus, so the results genuinely
        # differ and cannot be shared, but the query vector is identical every
        # time. Without this a briefing costs three embedding calls while the
        # caller waits.
        self._qcache: OrderedDict[str, list[float]] = OrderedDict()

    _QCACHE_MAX = 256

    def _query_vector(self, query: str) -> list[float]:
        key = " ".join((query or "").lower().split())
        hit = self._qcache.get(key)
        if hit is not None:
            self._qcache.move_to_end(key)
            return hit
        vec = embed([query], is_query=True)[0]
        self._qcache[key] = vec
        if len(self._qcache) > self._QCACHE_MAX:
            self._qcache.popitem(last=False)
        return vec

    def add(self, repair: Repair, vector: list[float] | None = None) -> None:
        self._fallback.add(repair)
        if vector is None:
            text = LocalRepairIndex._document(repair)
            try:
                vector = embed([text], is_query=False)[0]
            except Exception:
                return          # keep it searchable via fallback at least
        self._repairs.append(repair)
        self._vecs.append(vector)
        self._norms.append(_norm(vector))

    def add_unembedded(self, repair: Repair) -> None:
        """Searchable by word overlap only, with no vector and no API call.

        For repairs the quota would not stretch to. They are still real jobs
        and a caller describing one deserves to reach it, even if only by
        shared words until the backfill catches up.
        """
        self._fallback.add(repair)
        self._unembedded.add(repair)

    def size(self) -> int:
        """Every repair the index can reach, embedded or not.

        Counting only the vectors made a partly embedded corpus report itself
        as smaller than it was: 547 during a backfill that had 670 jobs in it.
        The unembedded ones are searchable, so they count.
        """
        return len(self._repairs) + self._unembedded.size()

    def search(self, query: str, *, manufacturer: str | None = None,
               model: str | None = None, limit: int = 5) -> list[Hit]:
        if not self._repairs:
            return self._fallback.search(query, manufacturer=manufacturer,
                                         model=model, limit=limit)
        try:
            qv = self._query_vector(query)
        except Exception:
            # Vertex unreachable mid-call. Degrade, do not die.
            return self._fallback.search(query, manufacturer=manufacturer,
                                         model=model, limit=limit)

        qn = _norm(qv)
        hits: list[Hit] = []
        for repair, dv, dn in zip(self._repairs, self._vecs, self._norms):
            if manufacturer and repair.manufacturer.lower() != manufacturer.lower():
                continue
            if model and repair.model.lower() != model.lower():
                continue
            score = sum(a * b for a, b in zip(qv, dv)) / (qn * dn)
            hits.append(Hit(repair, round(score, 3), "semantic match"))

        if self._unembedded.size():
            # A partly embedded corpus. Cosine and word overlap are not on the
            # same scale, so this ordering is rough where the two meet, and
            # word-overlap hits are damped to keep them from outranking real
            # semantic matches. Rough beats absent: these are closed jobs that
            # would otherwise be unreachable until the backfill finishes.
            for h in self._unembedded.search(query, manufacturer=manufacturer,
                                             model=model, limit=limit):
                hits.append(Hit(h.repair, round(h.score * 0.6, 3),
                                "word match, not embedded yet"))

        hits.sort(key=lambda h: -h.score)

        seen: set[str] = set()
        out: list[Hit] = []
        for h in hits:
            if h.repair.id in seen:
                continue
            seen.add(h.repair.id)
            out.append(h)
        return out[:limit]


def build_index() -> tuple[RepairIndex, int, int]:
    """Load the corpus, embedding anything not embedded yet.

    Returns the index, how many repairs it holds, and how many needed a fresh
    embedding call. Vectors live in the repairs table, so this is cheap on
    every run after the first.
    """
    from . import db

    idx = VertexEmbeddingIndex()
    with db.connect() as c:
        rows = c.execute(
            """SELECT id, asset_id, manufacturer, model_number, reported_symptom,
                      error_code, found_cause, tech_note, parts_consumed,
                      labor_hours, closed_on, technician_id, embedding
               FROM repairs ORDER BY closed_on""").fetchall()

    def to_repair(r) -> Repair:
        narrative = r["found_cause"]
        if r["tech_note"]:
            narrative = f"{narrative}. {r['tech_note']}"
        return Repair(
            id=r["id"], serial=r["asset_id"] or "",
            manufacturer=r["manufacturer"], model=r["model_number"],
            reported_symptom=r["reported_symptom"] or "",
            error_code=r["error_code"], found_cause=narrative,
            parts_consumed=tuple(s for s in (r["parts_consumed"] or "").split(",") if s),
            labor_hours=r["labor_hours"] or 0.0, closed_on=r["closed_on"],
            technician_id=r["technician_id"] or "")

    pending: list[tuple[str, Repair, str]] = []
    for r in rows:
        rep = to_repair(r)
        if r["embedding"]:
            idx.add(rep, unpack(r["embedding"]))
        else:
            pending.append((r["id"], rep, LocalRepairIndex._document(rep)))

    embedded = 0
    if pending:
        # The same fault recurs across the installed base, so the same sentence
        # arrives many times. Embedding it once and reusing the vector is not
        # just cheaper: 430 repairs here reduce to 15 distinct narratives, and
        # sending the identical text 430 times would exhaust a fresh project's
        # embedding quota for no benefit whatsoever.
        unique: dict[str, list[float]] = {}
        todo = sorted({text for _, _, text in pending})

        # Persist per chunk rather than once at the end. The version that
        # embedded everything and then wrote it in a single transaction made
        # no progress at all when the quota ran out partway: nothing was
        # saved, so the next restart began from exactly the same place and
        # failed exactly the same way. On this machine that meant 462 repairs
        # stayed unembedded across every restart for days.
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            try:
                vectors = embed(chunk, is_query=False)
            except Exception as e:
                # Out of quota, or Vertex is unreachable. Keep everything
                # earned so far instead of throwing the corpus away.
                print(f"[embed] stopped after {embedded} of {len(todo)}: "
                      f"{type(e).__name__}. The rest fall back to word "
                      f"matching and will be picked up on the next run.")
                break
            unique.update(zip(chunk, vectors))
            embedded += len(chunk)
            with db.txn() as c:
                for rid, _, text in pending:
                    if text in unique:
                        c.execute("UPDATE repairs SET embedding=? WHERE id=?",
                                  (pack(unique[text]), rid))

        for rid, rep, text in pending:
            vec = unique.get(text)
            if vec is not None:
                idx.add(rep, vec)
            else:
                # Searchable by word overlap, absent from the vector side.
                # Better than being absent from both, which is what happened
                # when one quota error aborted the whole build.
                idx.add_unembedded(rep)

    return idx, idx.size(), embedded
