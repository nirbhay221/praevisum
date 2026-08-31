"""Matching what somebody said to what we actually sell, by meaning.

WHY WORDS WERE NOT ENOUGH

Family matching went: the exact name, then either string containing the other,
then shared words. That handles "gaming laptop" finding "laptop" because they
share a word. It cannot handle

    "refrigerator"        ->  reach-in cooler
    "a fridge for drinks" ->  display cooler
    "somewhere to sit"    ->  office chair

because there is no word in common. Every one of those was a real caller, and
each time the desk truthfully reported it had nothing and offered another
retailer's stock instead.

memory.py already says what to do about it: "Not as good as real embeddings.
Much better than substring matching, and good enough to prove the loop
closes... the same three methods when we want real embeddings." This is that,
for products rather than repairs.

WHY A MARGIN AND NOT A THRESHOLD

Cosine between two unrelated family names is about 0.58, and between related
ones about 0.72. A fixed cut anywhere in that band is a coin toss on the
cases that matter. What separates a real match from a near miss is not how
high the best score is, it is how far ahead of the runner-up it sits:

    refrigerator    reach-in cooler 0.718   office chair 0.581   ahead by .137
    gaming laptop   laptop          0.791   office chair 0.586   ahead by .205

So a match has to be both good enough AND clearly better than second place.
An ambiguous phrase produces no match, which is correct: the desk should ask
rather than guess between two kinds of machine.

WHAT IT COSTS

One API call per phrase the desk has not seen before. Family names are
embedded once and cached in the database, and there are about thirty of them
across four businesses. Never raises: without a key, without a network, or on
any error at all, this returns nothing and the word matching upstream decides
exactly as it did before.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime

from . import db

MODEL = os.getenv("PRAEVISUM_EMBED_MODEL", "gemini-embedding-001")

# Good enough to be worth considering at all.
#
# WAS 0.62, AND THAT LET NONSENSE THROUGH. "submarine" came back as
# "projector" at 0.641 and the desk offered five projectors to somebody who
# had asked for a submarine. The band that matters is narrow: an unrelated
# pair sits near 0.58 and a real one near 0.72, so 0.62 was inside the noise.
#
# 0.68 keeps every match this was built for -- refrigerator to reach-in cooler
# scores 0.718, gaming laptop to laptop 0.791 -- and rejects the ones that
# were only ever near-misses. The cost of refusing is that the desk asks; the
# cost of accepting is that it confidently sells the wrong kind of machine.
GOOD_ENOUGH = 0.68

# And clearly ahead of the next best. Below this the phrase is ambiguous
# between two families and the honest answer is to ask.
CLEARLY_AHEAD = 0.04

_CACHE: dict[str, list[float]] = {}


def _client():
    try:
        import google.genai as genai

        from . import config  # noqa: F401  (loads the key)

        return genai.Client()
    except Exception:
        return None


def embed(phrases: list[str]) -> dict[str, list[float]]:
    """Vectors for these phrases, from the cache where possible."""
    want = [p for p in phrases if p and p not in _CACHE]

    if want:
        rows = _remembered(want)
        for phrase, vec in rows.items():
            _CACHE[phrase] = vec
        want = [p for p in want if p not in _CACHE]

    if want:
        client = _client()
        if client is None:
            return {p: _CACHE[p] for p in phrases if p in _CACHE}
        try:
            got = client.models.embed_content(model=MODEL, contents=want)
            for phrase, e in zip(want, got.embeddings):
                _CACHE[phrase] = list(e.values)
            _remember({p: _CACHE[p] for p in want if p in _CACHE})
        except Exception as e:
            print(f"[meaning] could not embed {len(want)} phrase(s), falling "
                  f"back to word matching: {type(e).__name__}: {e}", flush=True)

    return {p: _CACHE[p] for p in phrases if p in _CACHE}


def _remembered(phrases: list[str]) -> dict[str, list[float]]:
    """Vectors already stored. A family name does not change its meaning."""
    try:
        with db.connect() as c:
            marks = ",".join("?" * len(phrases))
            return {r["phrase"]: json.loads(r["vector"]) for r in c.execute(
                f"SELECT phrase, vector FROM meanings WHERE phrase IN ({marks})",
                tuple(phrases))}
    except Exception:
        return {}


def _remember(vectors: dict[str, list[float]]) -> None:
    try:
        with db.txn() as c:
            for phrase, vec in vectors.items():
                c.execute(
                    """INSERT INTO meanings (phrase, model, vector, made_on)
                       VALUES (?,?,?,?)
                       ON CONFLICT(phrase) DO UPDATE SET
                         vector = excluded.vector, model = excluded.model""",
                    (phrase, MODEL, json.dumps(vec),
                     datetime.now().isoformat(timespec="seconds")))
    except Exception as e:
        print(f"[meaning] could not store embeddings: "
              f"{type(e).__name__}: {e}", flush=True)


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def closest_family(said: str, families: list[str]) -> dict:
    """Which of these families the caller most likely meant.

    Returns {} when nothing is close enough, or when two are too close to
    each other to choose between. Both of those mean "ask them", which is a
    better answer than a confident wrong one.
    """
    said = (said or "").strip()
    families = [f for f in families if f]
    if not said or len(families) < 2:
        return {}

    vectors = embed([said] + families)
    if said not in vectors:
        return {}

    scored = sorted(
        ((_cos(vectors[said], vectors[f]), f)
         for f in families if f in vectors),
        reverse=True)
    if len(scored) < 2:
        return {}

    (best, family), (second, runner_up) = scored[0], scored[1]

    if best < GOOD_ENOUGH:
        return {"why": f"nothing close enough to {said!r}",
                "best": family, "score": round(best, 3)}
    if best - second < CLEARLY_AHEAD:
        return {"why": f"{said!r} sits between {family!r} and {runner_up!r}",
                "ambiguous": [family, runner_up],
                "scores": [round(best, 3), round(second, 3)]}

    return {"family": family, "score": round(best, 3),
            "ahead_of": runner_up, "by": round(best - second, 3)}
