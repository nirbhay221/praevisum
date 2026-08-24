"""How the desk did, derived from what the calls wrote.

Two things are being defended.

That nothing here is the agent's opinion. Every field is read back out of the
tables the call genuinely wrote, because an agent grading its own conversation
is not measurement.

And the case every bought dashboard would get wrong: a service call that ends
with no work order is a FAILURE if the desk lost the thread and the BEST
possible outcome if a documented remote fix worked and no van moved. Scoring
those the same would show this product getting worse exactly as the remote-fix
layer started working.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _call(db, call_id="CALL-T1", dealer="D-REF", intent=None,
          transcript=None, minutes=4.0):
    started = datetime.now() - timedelta(minutes=minutes)
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,started_at,ended_at,intent,
                                  transcript,dealer_id)
               VALUES (?,?,?,?,?,?,?)""",
            (call_id, "+13095550101", started.isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds"), intent,
             transcript, dealer))
    return call_id


def test_a_call_that_avoided_a_van_is_a_win_not_a_failure(dbfile):
    """The case a containment metric gets exactly backwards.

    No work order, no visit, nothing booked. A bought dashboard scores this as
    a failed call. It is the best outcome the desk can produce: the industry's
    own figure is 14% of truck rolls unnecessary at $200 to $300 each.
    """
    from src import db, review

    _call(db, "CALL-REMOTE", intent="service")
    with db.txn() as c:
        c.execute(
            """INSERT INTO remote_fixes (id,dealer_id,symptom,check_first,
                                         instruction,source)
               VALUES ('RF-T1','D-REF','warm','shut?','shut it','general')""")
        c.execute(
            """INSERT INTO remote_attempts (id,dealer_id,fix_id,from_call,
                                            outcome,attempted_at,saved_a_visit)
               VALUES ('RA-T1','D-REF','RF-T1','CALL-REMOTE','resolved',?,1)""",
            (datetime.now().isoformat(timespec="seconds"),))

    out = review.settle("CALL-REMOTE")
    assert out["outcome"] == "fixed_remotely"
    assert out["resolved"] is True, "a fix that worked was scored as a failure"
    assert out["avoided_visit"] is True

    r = review.review("D-REF")
    assert r["visits_avoided"] == 1
    assert r["forced_escalation"] == 0, "a solved call was counted as a breakage"


def test_a_service_call_that_produced_nothing_is_a_forced_escalation(dbfile):
    """The same empty result, for the opposite reason.

    The desk knew it was a service call and finished with nothing. That is the
    number that says the product is not working.
    """
    from src import db, review

    _call(db, "CALL-EMPTY", intent="service")

    out = review.settle("CALL-EMPTY")
    assert out["outcome"] == "nothing"
    assert out["resolved"] is False

    r = review.review("D-REF")
    assert r["forced_escalation"] == 1


def test_a_call_nobody_could_classify_is_its_own_failure(dbfile):
    """Different from a classified call that produced nothing.

    One is the desk not understanding the caller. The other is understanding
    and then losing the thread. Merging them hides which is happening.
    """
    from src import db, review

    _call(db, "CALL-LOST", intent=None)

    out = review.settle("CALL-LOST")
    assert out["outcome"] == "no_intent"
    r = review.review("D-REF")
    assert r["never_classified"] == 1
    assert r["forced_escalation"] == 0, \
        "a call that was never classified cannot have escalated from anything"


def test_every_flow_has_its_own_ending_not_just_service(dbfile):
    """A buying call that ends with no order is as much a failure as a service
    call with no job. Each flow is settled against its own terminal state."""
    from src import db, review

    _call(db, "CALL-ORDER", intent="order")
    _call(db, "CALL-SUPPLIER", intent="supplier")
    _call(db, "CALL-PRODUCT", intent="product")

    with db.txn() as c:
        acct = c.execute("SELECT id FROM accounts LIMIT 1").fetchone()
        site = c.execute("SELECT id FROM sites LIMIT 1").fetchone()
        if acct is None or site is None:
            pytest.skip("fixture has no account to hang an order on")
        c.execute(
            """INSERT INTO purchase_orders (id,account_id,site_id,
                                            from_call,status,placed_at)
               VALUES ('PO-T1',?,?,'CALL-ORDER','confirmed',?)""",
            (acct["id"], site["id"], datetime.now().isoformat(timespec="seconds")))

    assert review.settle("CALL-ORDER")["outcome"] == "order_confirmed"

    # A product question legitimately writes no row at all, so it is the one
    # flow where nothing in the database still counts as an answer.
    assert review.settle("CALL-PRODUCT")["outcome"] == "answered"

    # A supplier call that logged nothing did not do its job.
    assert review.settle("CALL-SUPPLIER")["resolved"] is False


def test_a_draft_order_is_not_a_confirmed_one(dbfile):
    """Half a call. The customer never said yes, and it must not read as a sale."""
    from src import db, review

    _call(db, "CALL-DRAFT", intent="order")
    with db.txn() as c:
        acct = c.execute("SELECT id FROM accounts LIMIT 1").fetchone()
        site = c.execute("SELECT id FROM sites LIMIT 1").fetchone()
        if acct is None or site is None:
            pytest.skip("fixture has no account")
        c.execute(
            """INSERT INTO purchase_orders (id,account_id,site_id,
                                            from_call,status,placed_at)
               VALUES ('PO-T2',?,?,'CALL-DRAFT','draft',?)""",
            (acct["id"], site["id"], datetime.now().isoformat(timespec="seconds")))

    out = review.settle("CALL-DRAFT")
    assert out["outcome"] == "order_drafted"
    assert out["resolved"] is False


def test_repeats_are_counted_as_structure_not_as_mood(dbfile):
    """The signal is that they had to say it again, not that they sounded cross.

    Normalised hard, because a person repeating themselves rarely uses the
    identical sentence.
    """
    from src import db, review

    transcript = "\n".join([
        "caller: the model number is H R P 2 H C one S",
        "agent: sorry, could you say that again",
        "caller: the model number is HRP2HC-1S",
        "agent: sorry, could you say that again",
        "caller: yes",
    ])
    _call(db, "CALL-REPEAT", intent="service", transcript=transcript)

    out = review.settle("CALL-REPEAT")
    assert out["turns"] == 5

    with db.connect() as c:
        row = c.execute(
            "SELECT caller_repeats, agent_repeats FROM call_outcomes WHERE call_id=?",
            ("CALL-REPEAT",)).fetchone()
    assert row["caller_repeats"] >= 1, "punctuation and spacing defeated the match"
    assert row["agent_repeats"] >= 1, "the desk asking twice was not noticed"

    r = review.review("D-REF")
    assert r["needs_attention"], "a call that went nowhere twice was not flagged"
    assert "repeated themselves" in r["needs_attention"][0]["why"]


def test_a_spoken_model_number_matches_the_written_one(dbfile):
    """The case exact matching missed, which is the only case that matters.

    Nobody reads a model number out twice the same way. If this does not hold,
    the repeat count is zero on exactly the calls it exists to find.
    """
    from src.review import _repeats

    assert _repeats(["the model number is H R P 2 H C one S",
                     "the model number is HRP2HC-1S"]) == 1

    # Three attempts is two repeats, not three.
    assert _repeats(["it is a beverage air MT34 dash one",
                     "beverage air MT34-1",
                     "the beverage air MT34 1"]) == 2

    # Two genuinely different sentences are not a repeat.
    assert _repeats(["the walk in cooler is not holding temperature",
                     "can you also send a price for the door gasket"]) == 0

    # Short acknowledgements never count, however often they occur. Dropped by
    # meaning rather than by length, because the length floor has to stay low
    # enough for a bare model number to survive it.
    assert _repeats(["yes", "yes", "okay", "okay", "yes",
                     "thanks", "thanks", "alright", "alright"]) == 0

    # And the line that matters is short. An eight character model number,
    # given twice, is the case a 12 character floor silently discarded.
    assert _repeats(["HRP2HC-1S", "HRP2HC 1 S"]) == 1


def test_settling_twice_does_not_double_count(dbfile):
    """A redelivered webhook or a manual re-run must not invent calls."""
    from src import db, review

    _call(db, "CALL-TWICE", intent="service")
    review.settle("CALL-TWICE")
    review.settle("CALL-TWICE")

    assert review.review("D-REF")["calls"] == 1


def test_an_empty_window_says_so_rather_than_showing_zeroes(dbfile):
    """A dashboard of zeroes reads as a working desk that had a quiet month.

    The truth here is that no call has been taken at all, and the two must not
    look the same.
    """
    from src import review

    r = review.review("D-REF")
    assert r["calls"] == 0
    assert "has not taken a call" in r["say"]


def test_one_dealer_cannot_see_another_dealers_calls(dbfile):
    """Same rule as the repair corpus and for the same reason."""
    from src import db, review

    _call(db, "CALL-REF", dealer="D-REF", intent="service")
    _call(db, "CALL-IT", dealer="D-IT", intent="service")
    review.settle("CALL-REF")
    review.settle("CALL-IT")

    assert review.review("D-REF")["calls"] == 1
    assert review.review("D-IT")["calls"] == 1


def test_set_intent_writes_the_intent_onto_the_call_row(dbfile):
    """The column that has existed since the first schema and never held anything.

    Writing it to session state looked like recording it. Session state dies
    with the process, so every later question about how the desk did had
    nothing to read.
    """
    from types import SimpleNamespace

    from src import db, tools

    _call(db, "CALL-INTENT")
    ctx = SimpleNamespace(state={"call_id": "CALL-INTENT", "dealer_id": "D-REF"})
    tools.set_intent("service", ctx)

    with db.connect() as c:
        row = c.execute("SELECT intent FROM calls WHERE id=?",
                        ("CALL-INTENT",)).fetchone()
    assert row["intent"] == "service"


def test_recording_an_intent_never_takes_the_call_down(dbfile, monkeypatch):
    """Bookkeeping must not be able to end a conversation."""
    from types import SimpleNamespace

    from src import tools

    monkeypatch.setattr("src.review.db.txn",
                        lambda: (_ for _ in ()).throw(RuntimeError("locked")))
    ctx = SimpleNamespace(state={"call_id": "CALL-X"})
    out = tools.set_intent("service", ctx)
    assert out["ok"] is True
