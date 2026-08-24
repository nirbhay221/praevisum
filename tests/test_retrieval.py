"""Retrieval, and the query cache underneath it.

Callers and technicians do not share words. A caller says "screen went blank
and it shut itself off"; the technician wrote "control board failed, no output
to compressor relay". Word overlap finds nothing. That miss is the reason the
embedding index exists, so the paraphrase cases are worth holding onto.

The cache tests are here rather than with the reasoning tests because the bug
was a cost bug, not a correctness one: a single briefing embedded the same
sentence three times while the caller waited.
"""

from __future__ import annotations

from conftest import REF


class FakeEmbed:
    """Counts calls and returns a vector keyed on the words present.

    Deliberately crude. These tests are about how many times the network is
    asked and whether filters survive, not about embedding quality, and a real
    Vertex call would make them slow, flaky and billable.
    """

    VOCAB = ["temp", "warm", "ice", "fan", "noise", "screen", "black", "board"]

    def __init__(self):
        self.calls = []

    def __call__(self, texts, *, is_query=False):
        self.calls.append((texts[0], is_query))
        out = []
        for t in texts:
            low = t.lower()
            out.append([1.0 if w in low else 0.0 for w in self.VOCAB] + [0.1])
        return out


def _index(fake):
    from src import embed as E
    from src.domain.models import Repair

    E.embed = fake
    idx = E.VertexEmbeddingIndex()
    idx.add(Repair("R-1", "S1", "Traulsen", "G12010",
                   "not holding temp overnight", "",
                   "defrost thermostat open, ice on coil",
                   ["P-DEFROSTTHE"], 1.5, "2026-05-01", "T-1"))
    idx.add(Repair("R-2", "S2", "Traulsen", "G12010",
                   "loud rattling noise", "", "fan motor bearing gone",
                   ["P-EVAPFAN"], 1.0, "2026-05-02", "T-1"))
    fake.calls.clear()
    return idx


def test_repeated_query_is_embedded_once(monkeypatch):
    """Three lookups of one sentence must cost one embedding call.

    prior_repairs, what_to_load and assess_job all search the caller's own
    words during a single call. They need different filters, so the results
    genuinely differ, but the vector is identical every time.
    """
    from src import embed as E

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    for _ in range(3):
        idx.search("not holding temp overnight", limit=5)

    assert len(fake.calls) == 1


def test_cache_ignores_case_and_spacing(monkeypatch):
    """The same sentence transcribed twice is the same sentence."""
    from src import embed as E

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    idx.search("not holding temp overnight", limit=5)
    idx.search("  Not  Holding TEMP overnight ", limit=5)

    assert len(fake.calls) == 1


def test_different_queries_are_embedded_separately(monkeypatch):
    """The cache must not collapse genuinely different questions."""
    from src import embed as E

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    idx.search("not holding temp overnight", limit=5)
    idx.search("loud rattling noise", limit=5)

    assert len(fake.calls) == 2


def test_cache_does_not_leak_across_filters(monkeypatch):
    """Same vector, different filters, different results.

    The dangerous version of this optimisation caches whole result lists. Then
    a filtered search returns rows the filter should have removed, which is the
    corpus leak all over again.
    """
    from src import embed as E

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    everything = idx.search("not holding temp overnight", limit=5)
    filtered = idx.search("not holding temp overnight",
                          manufacturer="Whirlpool", limit=5)

    assert everything
    assert filtered == []
    assert len(fake.calls) == 1


def test_queries_are_tagged_as_queries(monkeypatch):
    """Documents and queries take different task types.

    Using one type for both measurably degrades retrieval, and it is a silent
    degradation: nothing errors, results just get worse.
    """
    from src import embed as E

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)
    idx.search("not holding temp overnight", limit=5)

    assert all(is_query for _, is_query in fake.calls)


def test_retrieval_survives_vertex_being_down(monkeypatch):
    """A degraded briefing beats a dead phone line."""
    from src import embed as E

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    def dead(texts, *, is_query=False):
        raise RuntimeError("vertex unreachable")

    monkeypatch.setattr(E, "embed", dead)
    hits = idx.search("not holding temp overnight", limit=5)
    assert hits, "fell over instead of falling back to word overlap"


def test_unembedded_repairs_are_still_reachable(monkeypatch):
    """A corpus half embedded must still search all of itself.

    This is the state the live machine was actually in: 208 of 670 repairs had
    vectors and the rest did not. The unembedded ones were invisible to the
    vector path, and invisible to the word-overlap path too, because that only
    ran when nothing at all was embedded. Several hundred closed jobs were
    simply unreachable.
    """
    from src import embed as E
    from src.domain.models import Repair

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    idx.add_unembedded(Repair(
        "R-3", "S3", "Traulsen", "G12010",
        "screen went black", "", "control board failed, no output",
        ["P-CONTROLBOA"], 2.0, "2026-05-03", "T-1"))

    found = {h.repair.id for h in idx.search("screen black", limit=10)}
    assert "R-3" in found, "an unembedded repair is unreachable"


def test_partial_corpus_still_prefers_semantic_matches(monkeypatch):
    """Word-overlap hits must not outrank real vector matches."""
    from src import embed as E
    from src.domain.models import Repair

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    idx.add_unembedded(Repair(
        "R-9", "S9", "Traulsen", "G12010",
        "temp", "", "unrelated job that happens to share a word",
        [], 1.0, "2026-05-09", "T-1"))

    hits = idx.search("not holding temp overnight", limit=5)
    assert hits[0].repair.id != "R-9"


def test_results_are_not_duplicated_across_both_paths(monkeypatch):
    """One repair, one hit, whichever path found it."""
    from src import embed as E

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)

    ids = [h.repair.id for h in idx.search("temp ice fan noise", limit=10)]
    assert len(ids) == len(set(ids))


def test_a_quota_failure_keeps_the_vectors_already_earned(monkeypatch, dbfile):
    """One quota error must not throw the whole corpus away.

    The behaviour that produced this test: build_index embedded everything and
    wrote it in a single transaction at the end. A quota error partway through
    raised, so nothing was written and the caller dropped all of it to word
    matching, including hundreds of repairs whose vectors were already stored
    and perfectly good.
    """
    from src import db, embed as E

    with db.txn() as c:
        for i in range(6):
            c.execute(
                """INSERT INTO repairs
                   (id,dealer_id,asset_id,manufacturer,model_number,
                    reported_symptom,found_cause,parts_consumed,labor_hours,
                    closed_on,technician_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (f"Q-{i}", REF, "AS-FREEZER", "Traulsen", "G12010",
                 f"symptom number {i}", f"cause number {i}", "", 1.0,
                 "2026-05-01", "T-1"))

    calls = {"n": 0}

    def flaky(texts, *, is_query=False):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return [[0.5] * 9 for _ in texts]

    monkeypatch.setattr(E, "embed", flaky)

    idx, size, embedded = E.build_index()

    assert embedded > 0, "gave up before saving anything"
    assert size > 0, "threw away the whole corpus over one quota error"

    # and the progress must be on disk, so the next run starts further along
    with db.connect() as c:
        stored = c.execute(
            "SELECT COUNT(*) FROM repairs WHERE embedding IS NOT NULL"
        ).fetchone()[0]
    assert stored > 0, "made no durable progress, so the next run repeats it"


def test_paraphrase_reaches_the_right_repair(corpus):
    """The caller's words are never the technician's words."""
    from src.memory import index_for

    hits = index_for(REF).search("it is warm inside and iced up at the back",
                                 limit=3)
    assert hits
    assert any("thermostat" in h.repair.found_cause.lower()
               or "fan" in h.repair.found_cause.lower() for h in hits)


def test_corpus_size_counts_unembedded_repairs(monkeypatch):
    """A backfill in progress must not make the corpus look smaller than it is.

    The startup line read "547 repairs" on a machine holding 670, because the
    count only included vectors. That is the kind of wrong number somebody
    later trusts.
    """
    from src import embed as E
    from src.domain.models import Repair

    fake = FakeEmbed()
    monkeypatch.setattr(E, "embed", fake)
    idx = _index(fake)          # two embedded

    before = idx.size()
    idx.add_unembedded(Repair("R-8", "S8", "Traulsen", "G12010",
                              "warm", "", "door gasket perished",
                              ["P-DOORGASKET"], 1.0, "2026-05-08", "T-1"))
    assert idx.size() == before + 1
