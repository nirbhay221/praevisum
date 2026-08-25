"""What we have learned about dealing with one customer, and what we refuse to.

recall.py claimed a conversation today was retrievable tomorrow. It was a dict
in the process, so it survived until the next restart, which on this deployment
means the next deploy. The loop it described had never closed.

That is the worst class of defect here. The README carries an honest status
table, the tests assert refusals, and the standing rule is that nothing may
claim a capability beyond what is recorded. A comment describing a closed loop
that is open breaks the thing everything else is built on.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _conversation(db, phone, call_id, caller_repeats=0, agent_repeats=0,
                  resolved=0, days_ago=1, dealer="D-REF"):
    when = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,started_at,dealer_id)
               VALUES (?,?,?,?)""", (call_id, phone, when, dealer))
        c.execute(
            """INSERT INTO call_outcomes
               (call_id,dealer_id,intent,outcome,resolved,caller_repeats,
                agent_repeats,turns,settled_at)
               VALUES (?,?,'service','nothing',?,?,?,4,?)""",
            (call_id, dealer, resolved, caller_repeats, agent_repeats, when))


# What they said, kept.


def test_what_a_caller_said_survives_a_restart(dbfile):
    """The defect this file exists for. It was a dict; it died every deploy."""
    from src.recall import _remember, _remembered

    _remember("+13095550101", "the walk-in at the back is not holding overnight")
    _remember("+13095550101", "it started after the delivery on Tuesday")

    # Nothing is held in the process any more, so reading it back IS the
    # restart case.
    said = [e.content.parts[0].text for e in _remembered("+13095550101")]
    assert len(said) == 2
    assert "delivery on Tuesday" in said[-1], "most recent should be last"


def test_the_memory_service_holds_nothing_in_the_process(dbfile):
    """Asserted directly, because the dict is exactly what came back before."""
    from src.recall import MEMORY

    assert not hasattr(MEMORY, "_said"), \
        "personal memory is in the process again, so it dies on the next deploy"


def test_one_caller_never_sees_another_callers_words(dbfile):
    from src.recall import _remember, _remembered

    _remember("+13095550101", "the walk-in is warm")
    _remember("+15635550202", "the laptop will not charge")

    said = " ".join(e.content.parts[0].text for e in _remembered("+13095550101"))
    assert "laptop" not in said


def test_remembering_never_takes_a_call_down(dbfile, monkeypatch):
    """A lost memory must not end a conversation, and must not be silent."""
    from src import recall

    monkeypatch.setattr("src.recall.db.txn",
                        lambda: (_ for _ in ()).throw(RuntimeError("locked")))
    recall._remember("+13095550101", "something worth keeping")


# What we learned about dealing with them.


def test_one_conversation_is_not_a_habit(dbfile):
    """The same rule as MIN_SAMPLE in the buying advice. One call is an
    anecdote, and treating it as a pattern is how a desk becomes confidently
    wrong about somebody."""
    from src import db, knowing

    _conversation(db, "+13095550101", "C-1", agent_repeats=3)
    out = knowing.about("+13095550101")

    assert out["known_habits"] is False
    assert out["do_this"] == []


def test_a_customer_who_sends_photos_is_asked_for_one_first(dbfile):
    """The model number is the most error-prone thing a customer is ever asked
    to do. Somebody who has sent a working photo twice should not be asked to
    read a masked number out a third time."""
    from src import db, knowing

    _conversation(db, "+13095550101", "C-1")
    _conversation(db, "+13095550101", "C-2")
    knowing.note_plate_read("+13095550101", True, "Beverage-Air", "HRP2HC-1S")
    knowing.note_plate_read("+13095550101", True, "Beverage-Air", "HRP2HC-1S")

    out = knowing.about("+13095550101")
    assert out["known_habits"] is True
    assert any("photo" in i and "straight away" in i for i in out["do_this"])


def test_a_customer_who_reads_cleanly_is_not_offered_a_photo(dbfile):
    """The opposite instruction, from the opposite evidence."""
    from src import db, knowing

    for i in range(3):
        _conversation(db, "+13095550101", f"C-{i}", resolved=1)

    out = knowing.about("+13095550101")
    assert any("Do not offer" in i for i in out["do_this"])


def test_asking_the_same_thing_twice_across_calls_changes_the_brief(dbfile):
    """patterns.py counts this across customers. This is the same signal for
    one of them, and it is the desk's own failure rather than theirs."""
    from src import db, knowing

    _conversation(db, "+13095550101", "C-1", agent_repeats=1)
    _conversation(db, "+13095550101", "C-2", agent_repeats=1)

    out = knowing.about("+13095550101")
    assert any("one question at a time" in i for i in out["do_this"])


def test_a_stored_channel_preference_is_finally_read(dbfile):
    """It has been on every contact since the first schema and nothing anywhere
    read it. We knew they would rather have a message and rang them anyway."""
    from src import knowing

    out = knowing.about("+13095550101", {"channel_pref": "whatsapp"})
    assert any("prefer whatsapp" in i.lower() for i in out["do_this"])


def test_nothing_here_guesses_how_they_like_to_be_spoken_to(dbfile):
    """There is no signal anywhere for whether somebody prefers a warmer or a
    firmer manner. An invented preference acted on confidently is worse than
    no preference, and it is the sentiment score this project already refused.
    """
    import inspect

    from src import knowing

    # The module docstring says what it refuses and therefore contains the
    # words. Check the executable source, which is where a tone profile would
    # actually live.
    body = inspect.getsource(knowing).split('"""', 2)[-1]
    for banned in ("warm", "friendly", "tone", "personality", "sentiment",
                   "mood", "polite", "rapport"):
        assert banned not in body.lower(), f"a tone profile crept in: {banned}"


def test_a_habit_from_two_years_ago_is_not_a_habit(dbfile):
    """A customer who struggled with a model number then has probably replaced
    the machine."""
    from src import db, knowing

    for i in range(3):
        _conversation(db, "+13095550101", f"C-{i}", agent_repeats=2, days_ago=800)

    assert knowing.about("+13095550101")["known_habits"] is False


def test_reading_the_history_never_takes_a_call_down(dbfile, monkeypatch):
    from src import knowing

    monkeypatch.setattr("src.knowing.db.connect",
                        lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    assert knowing.about("+13095550101")["known_habits"] is False


# It reaches the agent.


def test_the_opening_brief_carries_what_we_learned(dbfile):
    """`took_two_trips` already proves one fact from the database changes how a
    call opens. These are the same shape and must arrive the same way."""
    from src.telephony.twilio_bridge import _opening_brief

    brief = _opening_brief({
        "known": True, "contact_name": "Dana", "account_name": "Marino's",
        "assets": [], "habits": {"known_habits": True, "do_this": [
            "They prefer whatsapp. Follow up there rather than ringing."]},
    })
    assert "prefer whatsapp" in brief


def test_a_first_time_caller_is_told_nothing_about_their_habits(dbfile):
    """Nobody should be characterised on the strength of no evidence."""
    from src.telephony.twilio_bridge import _opening_brief

    brief = _opening_brief({
        "known": True, "contact_name": "Dana", "account_name": "Marino's",
        "assets": [], "habits": {"known_habits": False, "do_this": []},
    })
    assert "prefer" not in brief
    assert "photo" not in brief
