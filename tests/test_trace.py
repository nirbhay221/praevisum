"""The arithmetic, made visible while it is happening.

Two properties matter and both are easy to lose later.

It must never compute. A trace line that did its own arithmetic could disagree
with the decision it claims to describe, and a feed that lies is worse than no
feed. Everything published here is a value the decision already produced.

It must never be in the way. events.publish is fire and forget, and every
function in trace.py is wrapped, because a dashboard is not allowed to be the
reason a phone call fails.
"""

from __future__ import annotations

import pytest


def _feed(dealer="D-REF"):
    from src import events
    return [e for e in events.recent(dealer) if e["kind"] == "reasoning"]


@pytest.fixture(autouse=True)
def _clear_feed():
    from src import events
    events._RECENT.clear()
    yield
    events._RECENT.clear()


def test_both_sides_of_the_inequality_are_shown(dbfile):
    """"Carry the defrost heater" is an assertion. The numbers are a claim
    somebody can check, and being checkable is the whole point."""
    from src import trace

    trace.van_load("D-REF",
                   carry=[{"sku": "P-DEFROSTHEA", "probability": 0.555,
                           "expected_saving": 216.45, "carrying_cost": 5.92}],
                   skip=[{"sku": "P-CONTROLBOA", "probability": 0.04,
                          "expected_saving": 12.0, "carrying_cost": 15.44}])

    text = " ".join(e["text"] for e in _feed())
    assert "CARRY" in text and "SKIP" in text
    assert "216.45" in text, "the saving was not shown"
    assert "5.92" in text, "the cost of carrying was not shown"
    assert "15.44" in text, "the rejected part's cost was not shown"


def test_what_was_left_behind_is_published_too(dbfile):
    """A system that shows only what it chose is showing a conclusion.

    Showing what it rejected, and why, is showing a decision.
    """
    from src import trace

    trace.van_load("D-REF", carry=[], skip=[
        {"sku": "P-CONTROLBOA", "probability": 0.11,
         "expected_saving": 33.0, "carrying_cost": 15.44}])

    assert any("SKIP" in e["text"] for e in _feed())


def test_the_evidence_tier_travels_with_the_probability(dbfile):
    """44% off machines of the same defrost design is a different claim from
    44% off the same model, and the dealer is entitled to the difference."""
    from src import trace

    trace.fault_distribution("D-REF", "frost on the coil", [
        {"probability": 0.44, "cause": "evaporator fan motor seized",
         "evidence_from": ["same defrost and cooling design", "elsewhere"]}])

    text = " ".join(e["text"] for e in _feed())
    assert "44%" in text
    assert "same defrost and cooling design" in text


def test_having_no_evidence_is_said_out_loud(dbfile):
    """The honest empty answer is worth publishing. A blank feed reads as a
    system that did not run."""
    from src import trace

    trace.fault_distribution("D-REF", "makes an odd noise", [])
    assert any("nothing in our own history" in e["text"] for e in _feed())


def test_the_trace_never_computes_anything(dbfile):
    """It publishes values the decision already produced.

    A trace that did its own arithmetic could disagree with the decision it
    claims to describe, and a record that contradicts the thing it records is
    worse than no record.

    Writing a row is allowed and reading one is not. The distinction is the
    whole property: an INSERT cannot change a conclusion, and a SELECT is how
    a log quietly becomes a second opinion.
    """
    import inspect

    from src import trace

    src = inspect.getsource(trace)
    for banned in ("TRUCK_ROLL", "CARRY_RATE", "_fault_distribution",
                   "SELECT", "fetchone", "fetchall"):
        assert banned not in src, f"trace.py reaches past recording: {banned}"

    assert "INSERT INTO decisions" in src, "the trace stopped being durable"


def test_rubbish_in_never_reaches_the_caller(dbfile):
    """A formatting mistake in a dashboard must not end a phone call."""
    from src import trace

    for bad in (None, [], [{}], [{"probability": None, "cause": None}],
                [{"sku": None, "probability": "x"}]):
        trace.fault_distribution("D-REF", "x", bad if isinstance(bad, list) else [])
        trace.van_load("D-REF", bad if isinstance(bad, list) else [], [])
        trace.send_decision("D-REF", {})
        trace.outside_opinion("D-REF", "Any", {})
        trace.settled("D-REF", {})


def test_the_decision_is_identical_with_the_feed_broken(dbfile, monkeypatch):
    """Turning the feed off, or breaking it, cannot change what the desk does."""
    import src.memory as memory
    from src import db, reason

    memory.load_from_db()
    with db.connect() as c:
        a = c.execute("SELECT id FROM assets LIMIT 1").fetchone()

    before = reason.what_to_load("D-REF", a["id"], "not holding temperature")

    monkeypatch.setattr("src.events.publish",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("feed down")))
    after = reason.what_to_load("D-REF", a["id"], "not holding temperature")

    assert before == after, "a broken dashboard changed the decision"


def test_a_send_decision_shows_what_it_avoids(dbfile):
    """The most consequential thing this desk decides."""
    from src import trace

    trace.send_decision("D-REF", {
        "send": "offer_first",
        "cost_avoided_if_it_works": 300,
        "remote_fix": {"source": "recall", "worked_before": "4 of 6"}})

    text = " ".join(e["text"] for e in _feed())
    assert "OFFER FIRST" in text
    assert "recall" in text and "4 of 6" in text
    assert "300" in text, "the money it avoids was not shown"
