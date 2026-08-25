"""The half that was missing: something that reads what review.py measures.

review.py settled every call and nothing imported it outside an API endpoint.
The instrument existed and nothing was wired to the dial, which is the
difference between a system that records its performance and one that improves.

And the decisions themselves were visible but not durable: events.py holds
sixty per dealer in memory, so the arithmetic behind a van load scrolled past a
dashboard nobody was watching and was gone. "Why did you carry that part three
weeks ago" had no answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _broken_call(db, call_id, said, intent="service",
                 caller_repeats=0, agent_repeats=0, dealer="D-REF"):
    """A call that was understood and still produced nothing."""
    now = datetime.now()
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,started_at,ended_at,intent,
                                  transcript,dealer_id)
               VALUES (?,?,?,?,?,?,?)""",
            (call_id, "+13095550101", now.isoformat(timespec="seconds"),
             now.isoformat(timespec="seconds"), intent,
             f"caller: {said}\nagent: let me look that up", dealer))
        c.execute(
            """INSERT INTO call_outcomes
               (call_id,dealer_id,intent,outcome,resolved,caller_repeats,
                agent_repeats,turns,settled_at)
               VALUES (?,?,?,'nothing',0,?,?,2,?)""",
            (call_id, dealer, intent, caller_repeats, agent_repeats,
             now.isoformat(timespec="seconds")))


# The decisions, kept.


def test_the_reasoning_survives_the_process(dbfile):
    """It was visible and not durable, which is the wrong half to skip.

    A live feed answers "what is happening now", which is a demo question. A
    dealer asks "why did you put a defrost heater in that van three weeks
    ago", and until this existed nobody knew any more.
    """
    from src import db, patterns, trace

    trace.call_context("CALL-WHY")
    trace.van_load("D-REF",
                   [{"sku": "P-DEFROSTHEA", "probability": 0.555,
                     "expected_saving": 216.45, "carrying_cost": 5.92}],
                   [{"sku": "P-CONTROLBOA", "probability": 0.04,
                     "expected_saving": 12.0, "carrying_cost": 15.44}])

    out = patterns.where_the_reasoning_went("CALL-WHY")
    assert len(out["decisions"]) == 2
    verdicts = {d["verdict"] for d in out["decisions"]}
    assert verdicts == {"carry", "skip"}, "what was rejected was not kept"


def test_the_figures_are_stored_apart_from_the_sentence(dbfile):
    """Keeping only the English would make "how often were we right" an
    exercise in parsing prose, which is how a record stops being evidence."""
    import json

    from src import patterns, trace

    trace.call_context("CALL-NUM")
    trace.van_load("D-REF", [{"sku": "P-EVAPFAN", "probability": 0.445,
                              "expected_saving": 160.2,
                              "carrying_cost": 3.76}], [])

    d = patterns.where_the_reasoning_went("CALL-NUM")["decisions"][0]
    nums = json.loads(d["numbers"])
    assert nums["probability"] == 0.445
    assert nums["expected_saving"] == 160.2
    assert nums["carrying_cost"] == 3.76, "the arithmetic was not queryable"


def test_reasoning_outside_a_call_is_still_kept(dbfile):
    """A restock sweep reasons too, and nobody is on the phone for it."""
    from src import db, trace

    trace.CALL.set("")
    trace.van_load("D-REF", [{"sku": "P-X", "probability": 0.5,
                              "expected_saving": 10, "carrying_cost": 1}], [])

    with db.connect() as c:
        row = c.execute(
            "SELECT call_id FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
    assert row["call_id"] is None


def test_a_text_conversation_is_not_forced_to_be_a_call_row(dbfile):
    """A WhatsApp thread produces reasoning and is not a phone call.

    Making call_id a foreign key into `calls` would have failed every insert
    from the text channels, silently, since the write is guarded.
    """
    from src import patterns, trace

    trace.call_context("whatsapp:+15005550999")
    trace.van_load("D-REF", [{"sku": "P-Y", "probability": 0.5,
                              "expected_saving": 10, "carrying_cost": 1}], [])

    out = patterns.where_the_reasoning_went("whatsapp:+15005550999")
    assert out["decisions"], "a text conversation's reasoning was dropped"


def test_a_failed_write_never_changes_the_decision(dbfile, monkeypatch):
    """Losing the record is bad. Losing the decision because the record failed
    is worse."""
    from src import trace

    monkeypatch.setattr("src.trace.db.txn",
                        lambda: (_ for _ in ()).throw(RuntimeError("disk full")))
    trace.van_load("D-REF", [{"sku": "P-Z", "probability": 0.5,
                              "expected_saving": 10, "carrying_cost": 1}], [])


# The patterns.


def test_three_failures_on_the_same_word_become_a_pattern(dbfile):
    """Two calls that both went wrong may be two bad calls. Three is a shape."""
    from src import db, patterns

    for i in range(3):
        _broken_call(db, f"CALL-W{i}",
                     "the walk-in cooler at the back is not holding",
                     agent_repeats=1)

    out = patterns.failing_patterns("D-REF")
    words = {p["word"] for p in out["patterns"]}
    assert "walk" in words or "cooler" in words or "holding" in words

    top = out["patterns"][0]
    assert top["calls"] == 3
    assert top["call_ids"], "the calls behind the pattern were not nameable"


def test_two_failures_are_not_a_pattern(dbfile):
    from src import db, patterns

    for i in range(2):
        _broken_call(db, f"CALL-T{i}", "the ice machine is making hollow cubes")

    assert patterns.failing_patterns("D-REF")["patterns"] == []


def test_the_finding_carries_the_structural_evidence(dbfile):
    """"Four calls about walk-ins produced nothing, and on three the desk asked
    the same thing twice" is actionable. A score is not."""
    from src import db, patterns

    for i in range(3):
        _broken_call(db, f"CALL-E{i}", "the reach-in freezer keeps icing over",
                     agent_repeats=1)

    said = patterns.failing_patterns("D-REF")["patterns"][0]["says"]
    assert "produced nothing" in said
    assert "asked the same thing twice" in said
    for banned in ("score", "rating", "quality", "sentiment", "/10"):
        assert banned not in said.lower(), f"a verdict crept in: {banned}"


def test_resolved_calls_are_never_a_pattern(dbfile):
    """It reports what keeps failing, not what keeps happening."""
    from src import db, patterns

    now = datetime.now()
    for i in range(4):
        with db.txn() as c:
            c.execute(
                """INSERT INTO calls (id,from_e164,started_at,intent,transcript,
                                      dealer_id)
                   VALUES (?,?,?,'service',?,'D-REF')""",
                (f"CALL-OK{i}", "+13095550101",
                 now.isoformat(timespec="seconds"),
                 "caller: the walk-in cooler is not holding"))
            c.execute(
                """INSERT INTO call_outcomes
                   (call_id,dealer_id,intent,outcome,resolved,settled_at)
                   VALUES (?,'D-REF','service','visit_booked',1,?)""",
                (f"CALL-OK{i}", now.isoformat(timespec="seconds")))

    assert patterns.failing_patterns("D-REF")["patterns"] == []


def test_words_every_call_contains_are_not_patterns(dbfile):
    """Reporting that customers mention temperature is true and useless."""
    from src import db, patterns

    for i in range(4):
        _broken_call(db, f"CALL-N{i}",
                     "there is a problem with the temperature on this machine")

    words = {p["word"] for p in patterns.failing_patterns("D-REF")["patterns"]}
    for noise in ("problem", "temperature", "machine", "with", "this"):
        assert noise not in words, f"noise reported as a pattern: {noise}"


def test_an_unclassified_call_is_counted_apart(dbfile):
    """The desk not understanding somebody is a different failure from
    understanding them and then losing the thread."""
    from src import db, patterns

    now = datetime.now()
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,started_at,dealer_id)
               VALUES ('CALL-U1','+13095550101',?,'D-REF')""",
            (now.isoformat(timespec="seconds"),))
        c.execute(
            """INSERT INTO call_outcomes
               (call_id,dealer_id,intent,outcome,resolved,settled_at)
               VALUES ('CALL-U1','D-REF',NULL,'no_intent',0,?)""",
            (now.isoformat(timespec="seconds"),))

    out = patterns.failing_patterns("D-REF")
    assert out["never_classified"] == 1
    assert out["failed_with_an_intent"] == 0


def test_an_empty_window_does_not_read_as_a_good_week(dbfile):
    from src import patterns

    out = patterns.failing_patterns("D-REF")
    assert out["calls"] == 0
    assert "has not taken a call" in out["say"]


def test_nothing_here_asks_a_model_anything(dbfile):
    """A model reading transcripts would produce fluent findings nobody could
    verify. Every line is a GROUP BY over rows the calls actually wrote."""
    import inspect

    from src import patterns

    src = inspect.getsource(patterns)
    for banned in ("generate_content", "LlmAgent", "genai", "Runner"):
        assert banned not in src, f"patterns reaches for a model: {banned}"


def test_one_dealer_cannot_see_another_dealers_failures(dbfile):
    from src import db, patterns

    for i in range(3):
        _broken_call(db, f"CALL-R{i}", "the walk-in cooler is not holding")
    for i in range(3):
        _broken_call(db, f"CALL-I{i}", "the laptop will not charge at all",
                     dealer="D-IT")

    ref = patterns.failing_patterns("D-REF")
    it = patterns.failing_patterns("D-IT")
    assert ref["failed_with_an_intent"] == 3
    assert it["failed_with_an_intent"] == 3
    assert all("laptop" != p["word"] for p in ref["patterns"])
