"""Answering the after-visit question by ringing, rather than by texting.

THE HOLE

A day after a visit the desk texts "is it still working?". Reply by text and
desk.py ties the answer back and acts on it: a yes queues the review request,
a no is treated as a second failure on the same job.

Ring instead, which plenty of people do, and none of that ran. `desk.answer`
is reached only from the SMS and WhatsApp webhooks; the voice agent never
passes through it and had no tool of its own. The answer was heard, the
follow-up stayed open forever, and the one piece of feedback the database
cannot produce for itself was thrown away.

WHY IT MATTERS MORE THAN A MISSED REVIEW

A "no, it went off again" arriving by phone is a second failure on a job we
believe is closed. Losing that is worse than losing the review it also costs.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, state):
        self.state = state


@pytest.fixture
def asked_them(dbfile):
    """A visit closed, and the after-visit question already sent."""
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-R','D-REF','business','Ring Back','2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-R','A-R','kitchen')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('C-R','A-R','Sam','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) "
                  "VALUES ('+15557770001','C-R','mobile',1)")
        c.execute("INSERT INTO work_orders (id,account_id,site_id,"
                  "reported_symptom,status,opened_at,dealer_id) VALUES "
                  "('WO-R','A-R','S-R','not cooling','closed','2026-08-01',"
                  "'D-REF')")
        c.execute("INSERT INTO followups (id,kind,account_id,contact_id,phone,"
                  "work_order_id,context,due_after,status,dealer_id,"
                  "created_at,sent_at) VALUES "
                  "('FU-R','after_visit','A-R','C-R','+15557770001','WO-R',"
                  "'walk-in cooler','2026-08-02','sent','D-REF',"
                  "'2026-08-01T09:00:00','2026-08-02T09:00:00')")
    return _Ctx({"dealer_id": "D-REF",
                 "caller": {"phone": "+15557770001", "account_id": "A-R"}})


def test_a_spoken_yes_is_tied_back_to_what_we_asked(asked_them):
    """Before this, ringing back meant the question had been rhetorical."""
    from src import db
    from src.tools import they_answered_our_question

    out = they_answered_our_question("yes, it has been fine since", asked_them)
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT status, reply FROM followups WHERE id='FU-R'"
                        ).fetchone()
    assert row["status"] == "answered", "the follow-up is still open"
    assert "fine since" in (row["reply"] or "")


def test_a_spoken_yes_earns_the_review_request(asked_them):
    """The only moment a review is worth asking for is once they have said it
    held. Asking somebody whose freezer may still be broken is how a business
    earns one-star reviews."""
    from src import db
    from src.tools import they_answered_our_question

    they_answered_our_question("yes all good thanks", asked_them)

    with db.connect() as c:
        asked = c.execute("SELECT COUNT(*) n FROM followups "
                          "WHERE kind='review_ask' AND account_id='A-R'"
                          ).fetchone()["n"]
    assert asked == 1


def test_a_spoken_no_does_not_ask_for_a_review(asked_them):
    """A no is a second failure on the same job. Asking that customer to rate
    us is the worst possible moment."""
    from src import db
    from src.tools import they_answered_our_question

    they_answered_our_question("no, it went off again last night", asked_them)

    with db.connect() as c:
        asked = c.execute("SELECT COUNT(*) n FROM followups "
                          "WHERE kind='review_ask' AND account_id='A-R'"
                          ).fetchone()["n"]
    assert asked == 0


def test_it_refuses_without_a_number_to_tie_it_to(dbfile):
    from src.tools import they_answered_our_question

    out = they_answered_our_question("yes fine", _Ctx({"dealer_id": "D-REF"}))
    assert out["ok"] is False
    assert "no number" in out["why"]


def test_the_phone_agent_actually_holds_it(dbfile):
    """Structural. The typed path had this and the voice path did not, which
    is the entire bug."""
    from src import agents

    for a in (agents.front_agent, agents.desk_agent):
        names = [getattr(t, "__name__", "") for t in a.tools]
        assert "they_answered_our_question" in names
