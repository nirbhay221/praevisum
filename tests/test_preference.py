"""Who a customer wants sent, and who they would rather not see again.

WHY THIS EXISTS

`find_technician` picked on skills and drive time, which is the right default
and misses the two commonest requests a service desk actually receives:

    "can you send the same chap as last time, he knew the machine"
    "please not him again"

The first is worth money: an engineer who has been to a site knows where the
isolator is and what was done last time. The second is worth more, and there
was nowhere to record it at all. A customer who asks not to see somebody and
then sees them anyway has been told their complaint went nowhere.

THE TWO ARE NOT THE SAME STRENGTH

An exclusion REMOVES. A preference REORDERS what is left, and is deliberately
not a promise: holding a job for three days waiting for one van while a
freezer is warm serves nobody, and a preference that silently outranked
availability is how a desk starts promising what it cannot keep.

AND NEITHER TOUCHES CERTIFICATION

cover.py decides who may legally open a machine. A preferred engineer without
the right EPA 608 type is not among the candidates by the time any of this
runs, and these tests assert the ordering cannot be reversed.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_customer_and_crew(dbfile):
    from src import db

    with db.connect() as c:
        acct = c.execute("SELECT id FROM accounts WHERE dealer_id='D-REF' "
                         "LIMIT 1").fetchone()["id"]

    # Build the crew rather than skipping without one. Nine of these tests
    # skipped silently on the first run because the fixture book holds fewer
    # than three engineers, and a skipped test guarding an exclusion rule is
    # a test that protects nothing.
    with db.txn() as c:
        for tid, name in (("T-P1", "Ada Lovelace"), ("T-P2", "Grace Hopper"),
                          ("T-P3", "Karen Sparck Jones")):
            c.execute(
                """INSERT INTO technicians (id,name,phone,dealer_id,active)
                   VALUES (?,?,?, 'D-REF', 1)
                   ON CONFLICT(id) DO NOTHING""",
                (tid, name, f"+1555000{tid[-1]}"))

    with db.connect() as c:
        crew = [dict(r) for r in c.execute(
            "SELECT id, name FROM technicians WHERE id IN "
            "('T-P1','T-P2','T-P3') ORDER BY id")]

    assert len(crew) == 3
    return {"account": acct, "crew": crew}


def test_an_exclusion_removes_them_outright(a_customer_and_crew):
    from src.preference import apply_to, remember

    acct = a_customer_and_crew["account"]
    crew = a_customer_and_crew["crew"]
    remember(acct, crew[0]["id"], "exclude", "he left the walk-in door open")

    out = apply_to(crew, acct)
    assert all(cand["id"] != crew[0]["id"] for cand in out["candidates"])
    assert out["removed"][0]["id"] == crew[0]["id"]
    assert "asked us not to send them" in out["removed"][0]["why"]


def test_a_preference_only_reorders(a_customer_and_crew):
    """It must not remove anybody. If the preferred engineer is busy, the
    soonest qualified person is still the right answer."""
    from src.preference import apply_to, remember

    acct = a_customer_and_crew["account"]
    crew = a_customer_and_crew["crew"]
    remember(acct, crew[2]["id"], "prefer", "knew the machine")

    out = apply_to(crew, acct)
    assert len(out["candidates"]) == len(crew), "a preference removed somebody"
    assert out["candidates"][0]["id"] == crew[2]["id"]


def test_an_exclusion_beats_a_preference_for_the_same_person(
        a_customer_and_crew):
    """Somebody can change their mind. The later instruction wins, and an
    exclusion is never quietly downgraded."""
    from src.preference import apply_to, remember

    acct = a_customer_and_crew["account"]
    crew = a_customer_and_crew["crew"]

    remember(acct, crew[0]["id"], "prefer", "was good last time")
    remember(acct, crew[0]["id"], "exclude", "not after this visit")

    out = apply_to(crew, acct)
    assert all(cand["id"] != crew[0]["id"] for cand in out["candidates"])


def test_the_desk_is_told_not_to_explain_the_rota(a_customer_and_crew):
    """A customer does not need to hear that somebody was removed. Saying so
    invites a conversation about a colleague."""
    from src.preference import apply_to, remember

    acct = a_customer_and_crew["account"]
    crew = a_customer_and_crew["crew"]
    remember(acct, crew[0]["id"], "exclude")

    out = apply_to(crew, acct)
    assert "Do not mention it" in out["say"]


def test_a_preference_is_never_promised(a_customer_and_crew):
    """The wording matters. "We will send him" is a commitment the rota
    cannot honour when a freezer is down on a Friday."""
    from src.preference import remember

    out = remember(a_customer_and_crew["account"],
                   a_customer_and_crew["crew"][0]["id"], "prefer")
    assert "do NOT promise" in out["say"]


def test_an_exclusion_is_absolute_and_says_so(a_customer_and_crew):
    from src.preference import remember

    out = remember(a_customer_and_crew["account"],
                   a_customer_and_crew["crew"][0]["id"], "exclude")
    assert "absolute" in out["say"]
    assert "justify" in out["say"].lower(), (
        "an exclusion must not invite the customer to explain themselves")


def test_an_unknown_engineer_is_refused_rather_than_guessed(
        a_customer_and_crew):
    from src.preference import remember

    out = remember(a_customer_and_crew["account"], "T-NOBODY", "exclude")
    assert out["ok"] is False


def test_only_prefer_or_exclude_are_accepted(a_customer_and_crew):
    from src.preference import remember

    out = remember(a_customer_and_crew["account"],
                   a_customer_and_crew["crew"][0]["id"], "maybe")
    assert out["ok"] is False


def test_a_customer_with_no_preferences_changes_nothing(a_customer_and_crew):
    """The common case. It must not reorder or drop anybody."""
    from src.preference import apply_to

    crew = a_customer_and_crew["crew"]
    out = apply_to(crew, a_customer_and_crew["account"])
    assert [c["id"] for c in out["candidates"]] == [c["id"] for c in crew]
    assert out["removed"] == []


def test_preferences_are_applied_after_certification_never_before(dbfile):
    """Structural. cover.py decides who may legally open a machine, and this
    must only ever see candidates that already passed it. If preference were
    applied first it could resurrect somebody uncertified."""
    import inspect

    from src import tools

    src = inspect.getsource(tools.find_technician)
    assert "apply_to" in src
    # It runs on `rows`, which is already the skills-filtered candidate list,
    # and after the sort rather than before the query.
    assert src.index("rows.sort") < src.index("apply_to")
