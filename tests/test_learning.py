"""A finished job has to become something the desk knows.

THE LOOP THAT WAS ONLY HALF CLOSED

`textback.py` does this properly: a technician replies to close a job, a
`repairs` row is written, it goes into the search index, and the next caller
with the same symptom on the same model gets the benefit within seconds.

It was also the only route in. Counted on the live book:

    completed visits with a diagnosed cause    851
    of those, with a repairs row               670
    finished, diagnosed, and never learned     181

One job in five was done, diagnosed, written into `visits.found_cause`, and
never reached anywhere the desk could read it. Nothing errored. The corpus just
quietly described 670 jobs while 851 had been done.

The bias has a direction, which is what makes it worth fixing rather than
merely tidying. Jobs that close through the text channel are the ones with an
engaged technician on a phone with signal. The ones that close some other way,
on paper, in the office, by a manager tidying up on Friday, are exactly the
awkward jobs most worth learning from.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_finished_job(dbfile):
    """A visit that is completed and diagnosed but never became a repair."""
    from src import db

    with db.connect() as c:
        site = c.execute(
            """SELECT s.id site_id, s.account_id
               FROM sites s JOIN accounts a ON a.id = s.account_id
               WHERE a.dealer_id = 'D-REF' LIMIT 1""").fetchone()
        asset = c.execute("SELECT id FROM assets WHERE site_id = ? LIMIT 1",
                          (site["site_id"],)).fetchone()

    with db.txn() as c:
        c.execute(
            """INSERT INTO work_orders
                 (id,account_id,site_id,asset_id,dealer_id,reported_symptom,
                  status,opened_at)
               VALUES ('WO-L1',?,?,?,'D-REF',
                       'not holding temperature overnight','open',
                       '2026-08-01')""",
            (site["account_id"], site["site_id"], asset["id"]))
        c.execute(
            """INSERT INTO visits
                 (id,work_order_id,seq,completed_at,found_cause,tech_note,
                  labor_hours)
               VALUES ('V-L1','WO-L1',1,'2026-08-02T15:00:00',
                       'door heater open circuit','replaced the harness',2.5)""")

    return {"visit": "V-L1", "asset": asset["id"]}


def test_a_finished_diagnosed_job_is_found_as_unlearned(a_finished_job):
    from src import learning

    pending = learning.unlearned("D-REF")
    assert any(v["visit_id"] == "V-L1" for v in pending)


def test_closing_the_loop_turns_it_into_knowledge(a_finished_job):
    from src import db, learning

    out = learning.close_the_loop("D-REF", index=False)
    assert out["written"] >= 1

    with db.connect() as c:
        row = c.execute("SELECT found_cause, model_number, dealer_id "
                        "FROM repairs WHERE visit_id = 'V-L1'").fetchone()

    assert row is not None
    assert row["found_cause"] == "door heater open circuit"
    assert row["dealer_id"] == "D-REF"


def test_it_is_safe_to_run_twice(a_finished_job):
    """Reconciliation, not duplication. This is the sort of thing somebody
    puts on a nightly timer and forgets about."""
    from src import db, learning

    learning.close_the_loop("D-REF", index=False)
    second = learning.close_the_loop("D-REF", index=False)

    assert second["written"] == 0
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM repairs "
                      "WHERE visit_id = 'V-L1'").fetchone()["n"]
    assert n == 1


def test_a_visit_nobody_diagnosed_is_never_learned_from(dbfile):
    """The one thing that would make the corpus worse rather than bigger. A
    row saying a machine was fixed somehow teaches the desk nothing and
    dilutes every search that touches it."""
    from src import db, learning

    with db.connect() as c:
        site = c.execute(
            """SELECT s.id site_id, s.account_id FROM sites s
               JOIN accounts a ON a.id = s.account_id
               WHERE a.dealer_id='D-REF' LIMIT 1""").fetchone()
        asset = c.execute("SELECT id FROM assets WHERE site_id=? LIMIT 1",
                          (site["site_id"],)).fetchone()

    with db.txn() as c:
        c.execute(
            """INSERT INTO work_orders
                 (id,account_id,site_id,asset_id,dealer_id,reported_symptom,
                  status,opened_at)
               VALUES ('WO-L2',?,?,?,'D-REF','making a noise','open',
                       '2026-08-01')""",
            (site["account_id"], site["site_id"], asset["id"]))
        c.execute(
            """INSERT INTO visits (id,work_order_id,seq,completed_at,found_cause)
               VALUES ('V-L2','WO-L2',1,'2026-08-02T15:00:00',NULL)""")

    assert not any(v["visit_id"] == "V-L2"
                   for v in learning.unlearned("D-REF"))
    out = learning.close_the_loop("D-REF", index=False)
    assert all(s["visit"] != "V-L2" for s in out["skipped"])


def test_a_job_with_no_machine_is_skipped_and_said_so(dbfile):
    """A repair record with no model number cannot be matched against a future
    call, so it is not written and the reason is reported rather than the row
    being silently dropped."""
    from src import db, learning

    with db.connect() as c:
        site = c.execute(
            """SELECT s.id site_id, s.account_id FROM sites s
               JOIN accounts a ON a.id = s.account_id
               WHERE a.dealer_id='D-REF' LIMIT 1""").fetchone()

    with db.txn() as c:
        c.execute(
            """INSERT INTO work_orders
                 (id,account_id,site_id,dealer_id,reported_symptom,status,
                  opened_at)
               VALUES ('WO-L3',?,?,'D-REF','something odd','open',
                       '2026-08-01')""",
            (site["account_id"], site["site_id"]))
        c.execute(
            """INSERT INTO visits (id,work_order_id,seq,completed_at,found_cause)
               VALUES ('V-L3','WO-L3',1,'2026-08-02T15:00:00','unclear')""")

    out = learning.close_the_loop("D-REF", index=False)
    assert any(s["visit"] == "V-L3" for s in out["skipped"])


def test_one_business_never_learns_from_another(a_finished_job):
    """The same tenancy rule as everything else. A refrigeration job must not
    turn up in the IT desk's corpus."""
    from src import learning

    out = learning.close_the_loop("D-IT", index=False)
    assert all(v["visit_id"] != "V-L1" for v in learning.unlearned("D-IT"))
    assert out["written"] == 0 or all(
        s["visit"] != "V-L1" for s in out["skipped"])
