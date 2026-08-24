"""A promise is either made or it is not. There is no half.

This is the only place in the system where several tables change together: a
visit, a set of reservations against real stock, an appointment in a real
technician's diary, and the work order's status. If a part turns out to be
short partway through, an earlier version unwound the reservations by hand and
left the visit row behind. A customer then had a visit nobody was coming to.

The rule these tests hold to: after a refused promise the database looks
exactly as it did before anyone asked.
"""

from __future__ import annotations

import pytest
from conftest import REF


class Ctx:
    def __init__(self, dealer=REF):
        self.state = {"dealer_id": dealer, "caller": {}}


def counts(db):
    with db.connect() as c:
        return {t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                for t in ("visits", "reservations", "appointments",
                          "work_orders")}


@pytest.fixture()
def wo(dbfile):
    from src import tools
    return tools.open_work_order("AS-FREEZER", "not holding temp overnight",
                                 Ctx())["work_order_id"]


def test_promise_succeeds_with_stocked_parts(dbfile, wo):
    from src import db, tools

    before = counts(db)
    r = tools.promise_slot(wo, "T-1", "2026-09-01T09:00",
                           ["P-DEFROSTTHE"], Ctx())
    assert r["ok"], r
    after = counts(db)

    assert after["visits"] == before["visits"] + 1
    assert after["reservations"] == before["reservations"] + 1

    with db.connect() as c:
        status = c.execute("SELECT status FROM work_orders WHERE id=?",
                           (wo,)).fetchone()["status"]
    assert status == "scheduled"


def test_short_part_leaves_nothing_behind(dbfile, wo):
    """The bug this file exists for.

    The first part reserves fine. The second cannot be had. Everything the
    attempt touched must be gone, including the row written before the failure.
    """
    from src import db, tools

    before = counts(db)
    r = tools.promise_slot(wo, "T-1", "2026-09-01T09:00",
                           ["P-DEFROSTTHE", "P-NOSUCHPART"], Ctx())

    assert not r["ok"]
    assert r["blocking_sku"] == "P-NOSUCHPART"
    assert counts(db) == before, "a refused promise left rows behind"


def test_out_of_stock_part_is_refused(dbfile, wo):
    """The control board is in the catalogue but none are on the shelf."""
    from src import db, tools

    before = counts(db)
    r = tools.promise_slot(wo, "T-1", "2026-09-01T09:00",
                           ["P-CONTROLBOA"], Ctx())

    assert not r["ok"]
    assert r["blocking_sku"] == "P-CONTROLBOA"
    assert counts(db) == before


def test_stock_cannot_be_promised_twice(dbfile):
    """Two jobs, one remaining fan motor at the warehouse.

    Stock that is reserved is not available. Without this the desk cheerfully
    promises the same physical part to two customers, and the second technician
    arrives to an empty shelf.
    """
    from src import db, tools

    a = tools.open_work_order("AS-FREEZER", "fan noise", Ctx())["work_order_id"]
    b = tools.open_work_order("AS-FREEZER", "fan noise", Ctx())["work_order_id"]

    with db.txn() as c:
        c.execute("""UPDATE stock SET on_hand=1
                     WHERE sku='P-EVAPFAN' AND location_id='L-REF-WH'""")
        c.execute("DELETE FROM stock WHERE location_id='L-REF-VAN1'")

    first = tools.promise_slot(a, "T-1", "2026-09-01T09:00",
                               ["P-EVAPFAN"], Ctx())
    assert first["ok"], first

    before = counts(db)
    second = tools.promise_slot(b, "T-1", "2026-09-02T09:00",
                                ["P-EVAPFAN"], Ctx())
    assert not second["ok"], "the same physical part was promised twice"
    assert counts(db) == before
