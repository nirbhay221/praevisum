"""Asking for a review, but only after the customer says the fix held.

WHERE THIS SITS

The loop already existed and stopped one step short:

    technician texts to close -> repair written -> a day later the customer
    is asked "is it holding now?" -> they reply -> record_reply ties it back

Then nothing. The answer was stored and acted on by no one, which made the
question rhetorical in a second way: it was recorded and it changed nothing.

WHY THE ORDER IS THE WHOLE FEATURE

The obvious build asks for the review in the after-visit message itself. That
is the version that earns one-star reviews, because it asks somebody whose
freezer may still be broken to go and rate the repair.

So the request is only ever queued after the customer has SAID it held. And a
negative answer is worth more than any review: a second failure on the same
work order is the strongest signal this book produces that a fix should stop
being offered.

THE TEST THAT MATTERS MOST

"yes but it is still warm" must read as a failure. It contains the word yes,
and a naive check would ask that customer for a public review.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("text", [
    "yes all good thanks",
    "yep working fine",
    "all sorted",
    "holding fine now",
    "perfect, thanks",
])
def test_a_clear_yes_is_read_as_held(dbfile, text):
    from src.asking import read_the_answer

    assert read_the_answer(text) == "held"


@pytest.mark.parametrize("text", [
    "no it is still doing it",
    "yes but it is still warm",
    "same problem again",
    "it is worse",
    "not fixed",
])
def test_anything_negative_is_read_as_failed(dbfile, text):
    """Negatives are checked FIRST and unconditionally. "yes but it is still
    warm" contains the word yes, and reading it the other way round asks a
    customer with a broken freezer to go and review us publicly."""
    from src.asking import read_the_answer

    assert read_the_answer(text) == "failed"


def test_an_ambiguous_reply_is_neither(dbfile):
    """A customer who wrote a paragraph gets a person, not an automated
    request for a five-star rating."""
    from src.asking import read_the_answer

    assert read_the_answer("we were out all day so I have not looked") in (
        "failed", "unclear")
    assert read_the_answer("") == "unclear"


@pytest.fixture
def they_were_asked(dbfile):
    """An after-visit question that has gone out to a real number."""
    from datetime import datetime

    from src import db

    with db.connect() as c:
        acct = c.execute("SELECT id FROM accounts WHERE dealer_id='D-REF' "
                         "LIMIT 1").fetchone()["id"]

    with db.txn() as c:
        c.execute(
            """INSERT INTO followups
                 (id,dealer_id,kind,account_id,phone,work_order_id,context,
                  due_after,status,sent_at,created_at)
               VALUES ('FU-1','D-REF','after_visit',?,'+15551110000',NULL,
                       'walk-in cooler','2026-08-29','sent',?,?)""",
            (acct, datetime.now().isoformat(), datetime.now().isoformat()))
    return {"phone": "+15551110000", "account": acct}


def test_a_yes_queues_a_review_request_for_later(they_were_asked):
    """Later, not now. The desk is mid-sentence with somebody and must not
    have a second message injected into the conversation."""
    from src import db
    from src.asking import after_they_said_it_held

    out = after_they_said_it_held(they_were_asked["phone"], "yes all good")
    assert out["verdict"] == "held"
    assert out["asked_for_a_review"] is True
    assert "do not mention it" in out["say"]

    with db.connect() as c:
        row = c.execute("SELECT kind, status FROM followups "
                        "WHERE kind='review_ask'").fetchone()
    assert row is not None
    assert row["status"] == "queued"


def test_a_no_never_asks_for_a_review_and_says_what_to_do(they_were_asked):
    from src import db
    from src.asking import after_they_said_it_held

    out = after_they_said_it_held(they_were_asked["phone"],
                                  "no it is still not holding")
    assert out["verdict"] == "failed"
    assert out["asked_for_a_review"] is False
    assert "second failure" in out["say"]

    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM followups "
                         "WHERE kind='review_ask'").fetchone()["n"] == 0


def test_nobody_is_asked_twice(they_were_asked):
    """Once is enough. Asking again is how a business becomes a nuisance."""
    from src.asking import after_they_said_it_held

    assert after_they_said_it_held(
        they_were_asked["phone"], "yes fine")["asked_for_a_review"] is True
    again = after_they_said_it_held(they_were_asked["phone"], "yes still fine")
    assert again["asked_for_a_review"] is False
    assert "Once is enough" in again["say"]


def test_a_reply_to_nothing_is_not_treated_as_an_answer(dbfile):
    from src.asking import after_they_said_it_held

    out = after_they_said_it_held("+15559990000", "yes all good")
    assert out["ok"] is False


def test_the_message_never_offers_an_incentive(dbfile):
    """Paying for reviews breaks every platform's terms and poisons the only
    honest signal a service business has."""
    from src.asking import render_review_ask

    text = render_review_ask({"context": "walk-in cooler"})
    low = text.lower()
    for bribe in ("discount", "voucher", "free", "off your next", "reward",
                  "gift", "%"):
        assert bribe not in low, f"the review request offered {bribe!r}"
    assert "no pressure" in low
    assert "not ask again" in low


def test_the_wording_is_built_not_generated(dbfile):
    """Same rule as every other follow-up and the technician briefing: a
    message that goes out unattended must not contain a sentence nobody
    chose."""
    import inspect

    from src import asking

    src = inspect.getsource(asking.render_review_ask)
    # Strip the docstring before scanning: it SAYS "no model writes this",
    # which is the claim, not a violation of it.
    code = src.split('"""')[-1]
    for generated in ("Runner", "LlmAgent", "generate_content", "Gemini"):
        assert generated not in code


@pytest.mark.parametrize("text,expected", [
    ("yes still fine", "held"),
    ("still working", "held"),
    ("it is still holding", "held"),
    ("yes but it is still warm", "failed"),
    ("still doing it", "failed"),
    ("still broken", "failed"),
])
def test_still_cuts_both_ways(dbfile, text, expected):
    """"still" carries no polarity on its own; the word after it does.

    Treating it as a flat negative read "yes still fine" as a complaint, so a
    happy customer was never asked for the review they would have left. And
    dropping it from the negatives entirely would read "still doing it" as
    neutral, which is worse.
    """
    from src.asking import read_the_answer

    assert read_the_answer(text) == expected
