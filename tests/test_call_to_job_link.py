"""Which call opened a job, and the screen that silently depended on it.

`work_orders.opened_from_call` has existed since the first schema and was NULL
on all 673 jobs. `open_work_order` never carried it, while `call_id` sat in
session state the whole time and was already being written to other tables.

That was not a cosmetic gap. calibration.py joins work orders to decisions ON
THIS COLUMN to answer "when this desk says 44%, is it right 44% of the time".
With the column empty the join matched nothing, so the screen reported:

    No prediction has yet been followed by a technician saying what it really
    was. This is not a good result or a bad one, it is an empty one.

Which is honest, and would have stayed true forever however many jobs were
finished. A feature that is truthful about being empty and structurally unable
to fill is worse than one that is obviously broken, because nobody chases it.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, state):
        self.state = state


@pytest.fixture
def a_call(dbfile):
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-L','D-REF','business','Linked Cafe','2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-L','A-L','kitchen')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('C-L','A-L','Pat','owner')")
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on) VALUES "
                  "('AS-L','S-L','Testco','TX-2','reach-in cooler','2024-01-01')")
        c.execute("INSERT INTO calls (id,started_at,from_e164,dealer_id) "
                  "VALUES ('CALL-LINK',?,'+15551234567','D-REF')",
                  ("2026-08-30T10:00:00",))
    return _Ctx({"dealer_id": "D-REF", "call_id": "CALL-LINK",
                 "caller": {"contact_id": "C-L", "account_id": "A-L"}})


def test_a_job_records_the_call_that_opened_it(a_call):
    from src import db
    from src.tools import open_work_order

    out = open_work_order("AS-L", "not getting cold", a_call)
    assert out.get("work_order_id") or out.get("id")

    with db.connect() as c:
        row = c.execute("SELECT opened_from_call FROM work_orders "
                        "WHERE account_id = 'A-L'").fetchone()
    assert row["opened_from_call"] == "CALL-LINK", (
        "the job cannot be traced back to the call, and calibration joins "
        "on exactly this")


def test_calibration_can_now_join_at_all(a_call):
    """Not that the numbers are right, only that the join reaches. Before the
    fix this could never match a single row."""
    from src import db
    from src.tools import open_work_order

    open_work_order("AS-L", "not getting cold", a_call)

    with db.connect() as c:
        joined = c.execute(
            """SELECT COUNT(*) n FROM work_orders w
               WHERE w.opened_from_call IS NOT NULL""").fetchone()["n"]
    assert joined >= 1


def test_a_job_opened_with_no_call_still_opens(dbfile):
    """The console and the sweep open jobs with no call behind them. A missing
    call must be null, not a crash and not a fake id."""
    from src import db
    from src.tools import open_work_order

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-N','D-REF','business','No Call','2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-N','A-N','kitchen')")
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on) VALUES "
                  "('AS-N','S-N','Testco','TX-3','reach-in cooler','2024-01-01')")

    out = open_work_order("AS-N", "warm", _Ctx({"dealer_id": "D-REF"}))
    assert out.get("work_order_id") or out.get("id")

    with db.connect() as c:
        assert c.execute("SELECT opened_from_call FROM work_orders "
                         "WHERE account_id='A-N'").fetchone()[0] is None


def test_an_id_with_no_call_row_does_not_cancel_the_job(dbfile):
    """The foreign key turns a missing call row into an IntegrityError that
    kills the work order. The customer is on the phone; losing the job is far
    worse than losing the link."""
    from src import db
    from src.tools import open_work_order

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-G','D-REF','business','Ghost Call','2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-G','A-G','kitchen')")
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on) VALUES "
                  "('AS-G','S-G','Testco','TX-4','reach-in cooler','2024-01-01')")

    out = open_work_order("AS-G", "warm",
                          _Ctx({"dealer_id": "D-REF",
                                "call_id": "CALL-DOES-NOT-EXIST"}))
    assert out.get("work_order_id") or out.get("id"), "the job was lost"

    with db.connect() as c:
        assert c.execute("SELECT opened_from_call FROM work_orders "
                         "WHERE account_id='A-G'").fetchone()[0] is None
