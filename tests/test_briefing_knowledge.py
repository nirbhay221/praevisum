"""The engineer is shown the trade checks, instead of having to ask for them.

THE GAP

`remote_fixes` holds first-line procedures for each kind of machine: the free
checks a trade does before pulling anything apart. A blocked condenser grille,
a door not seating, a shelf pressed against a vent.

The only route to them was `should_send_someone`, which runs BEFORE anybody is
dispatched and is aimed at talking a CUSTOMER through something simple. So the
knowledge existed, and the one person actually qualified to act on it, standing
in front of the open machine, was the one person never shown it. An engineer
who drives out, pulls a panel and finds a grille packed with lint has spent an
hour on something the briefing could have told them in a line.

WHY IT IS LABELLED AND LAST

Our own repair history is evidence about this model from this company. A
general trade check is not. An engineer deciding what to open first is entitled
to know which is which, and blending them would make the weaker claim borrow
the authority of the stronger one. Same separation `reviews.py` draws between
what we know and what the world says.

WHY IT IS ALLOWED TO RETURN NOTHING

The machine that surfaced this was a walk-in cooler reported as "frost building
on the coil, temp climbing at night". The four walk-in procedures on file are
all variants of "not cooling", so nothing matched and nothing was carried.

That is the right answer. "Check the thermostat setting" is real advice for a
warm box and nonsense for frost on a coil, and a briefing padded with generic
lines teaches people to stop reading it.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, **state):
        self.state = state


@pytest.fixture
def a_job_with_procedures(dbfile):
    """A reach-in freezer job whose symptom has a first-line check on file."""
    from src import db

    with db.connect() as c:
        site = c.execute(
            """SELECT s.id site_id, s.account_id FROM sites s
               JOIN accounts a ON a.id = s.account_id
               WHERE a.dealer_id = 'D-REF' LIMIT 1""").fetchone()
        asset = c.execute(
            """SELECT id FROM assets WHERE site_id = ?
               AND family = 'reach-in freezer' LIMIT 1""",
            (site["site_id"],)).fetchone()

    if asset is None:
        pytest.skip("the fixture book has no reach-in freezer")

    with db.txn() as c:
        c.execute(
            """INSERT INTO remote_fixes
                 (id,dealer_id,family,symptom,check_first,instruction,source,source_ref)
               VALUES ('RF-T1','D-REF','reach-in freezer',
                       'frost building on the coil',
                       'Has it been manually defrosted in the last three months?',
                       'Switch it off and empty it, leave the door open.',
                       'general','trade first-line check, not from a manual')""")
        c.execute(
            """INSERT INTO work_orders
                 (id,account_id,site_id,asset_id,dealer_id,reported_symptom,
                  status,opened_at)
               VALUES ('WO-K1',?,?,?,'D-REF',
                       'frost building on the coil, temp climbing at night',
                       'open','2026-08-01')""",
            (site["account_id"], site["site_id"], asset["id"]))

    return {"work_order": "WO-K1", "asset": asset["id"]}


def test_the_trade_checks_reach_the_briefing(a_job_with_procedures):
    from src import tools

    out = tools._trade_checks(
        "D-REF", "reach-in freezer",
        "frost building on the coil, temp climbing at night")

    assert out
    assert any("defrosted" in (c["check"] or "") for c in out)


def test_they_are_labelled_as_not_ours(a_job_with_procedures):
    """The whole point of carrying them separately. A general check must never
    borrow the authority of this company's own repair record."""
    from src import tools

    out = tools._trade_checks("D-REF", "reach-in freezer",
                              "frost building on the coil")
    assert out
    for check in out:
        assert "not from our own jobs" in check["this_is"]


def test_a_procedure_for_a_different_machine_is_not_offered(dbfile):
    """A reach-in procedure is not a walk-in procedure. "Empty it and leave the
    door open" is real advice for one and nonsense for the other."""
    from src import db, tools

    with db.txn() as c:
        c.execute(
            """INSERT INTO remote_fixes
                 (id,dealer_id,family,symptom,check_first,instruction,source,source_ref)
               VALUES ('RF-T2','D-REF','reach-in freezer',
                       'frost building on the coil',
                       'Empty it and leave the door open',
                       'Leave the door open until clear','general','trade check')""")

    assert tools._trade_checks("D-REF", "walk-in cooler",
                               "frost building on the coil") == []


def test_nothing_matching_carries_nothing(dbfile):
    """A briefing padded with generic advice trains people to stop reading it,
    so an unmatched symptom carries no checks rather than the closest guess."""
    from src import db, tools

    with db.txn() as c:
        c.execute(
            """INSERT INTO remote_fixes
                 (id,dealer_id,family,symptom,check_first,instruction,source,source_ref)
               VALUES ('RF-T3','D-REF','walk-in cooler',
                       'not cooling incorrect thermostat setting',
                       'Check the thermostat setting',
                       'Set it back to the design temperature','general','trade check')""")

    assert tools._trade_checks("D-REF", "walk-in cooler",
                               "frost building on the coil") == []


def test_another_business_procedures_are_not_ours(dbfile):
    """Same tenancy rule as everything else on this desk."""
    from src import db, tools

    with db.txn() as c:
        c.execute(
            """INSERT INTO remote_fixes
                 (id,dealer_id,family,symptom,check_first,instruction,source,source_ref)
               VALUES ('RF-T4','D-IT','reach-in freezer',
                       'frost building on the coil',
                       'Something the IT desk knows',
                       'Something else the IT desk knows','general','trade check')""")

    out = tools._trade_checks("D-REF", "reach-in freezer",
                              "frost building on the coil")
    assert all("IT desk" not in (c["check"] or "") for c in out)


def test_the_briefing_actually_carries_the_field(a_job_with_procedures):
    """The wiring, not just the helper. The briefing is what the engineer
    receives, and a field computed but never attached is the bug this whole
    project keeps finding."""
    from src import memory, tools

    memory.load_from_db()
    brief = tools.build_briefing(
        a_job_with_procedures["work_order"],
        _Ctx(dealer_id="D-REF", intent="service"))

    assert "general_checks" in brief
    assert brief["general_checks"]


def test_our_own_history_still_comes_first(a_job_with_procedures):
    """Order matters in a briefing somebody reads on a phone in a van."""
    from src import memory, tools

    memory.load_from_db()
    brief = tools.build_briefing(
        a_job_with_procedures["work_order"],
        _Ctx(dealer_id="D-REF", intent="service"))

    keys = list(brief.keys())
    assert keys.index("prior_visits_this_machine") < keys.index("general_checks")
    assert keys.index("likely_causes") < keys.index("general_checks")
