"""Ordering from a supplier, and stocking machines as well as parts.

Two holes, both on the buying side.

`restock_advice` worked out what to reorder and priced a stockout at a truck
roll, and then stopped. `purchase_orders` is the customer's side, where
`account_id` is who is buying from us. Nothing recorded this dealer ordering
from a supplier, so "we knew and did not order" and "we ordered and it is
late" were indistinguishable from inside the system.

And `restock_advice` reads the `parts` table and nothing else, so the desk
could recommend a Traulsen over a Beverage-Air, weigh their running costs from
federal data, quote the delivery, and have no idea whether one was in the
building. That is a gap the customer can feel.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

SKU = "P-EVAPFAN"


# Us buying from a supplier.


def test_an_order_records_what_was_advised_next_to_what_was_bought(dbfile):
    """A buyer who consistently orders half the recommendation is either wiser
    than the model or costing the company truck rolls, and there is no way to
    tell which without both numbers."""
    from src import db, supply

    out = supply.place_supply_order(SKU, qty=2, advised_qty=4,
                                    reason="covers the lead time and the review")
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT advised_qty, qty, reason FROM supply_orders"
                        ).fetchone()
    assert row["advised_qty"] == 4
    assert row["qty"] == 2
    assert "lead time" in row["reason"]


def test_the_expected_date_comes_from_the_part_not_a_guess(dbfile):
    from src import db, supply

    with db.connect() as c:
        lead = c.execute("SELECT lead_time_days FROM parts WHERE sku=?",
                         (SKU,)).fetchone()["lead_time_days"]

    out = supply.place_supply_order(SKU, qty=1)
    want = (datetime.now() + timedelta(days=lead)).date().isoformat()
    assert out["expected"] == want


def test_a_part_this_dealer_does_not_sell_cannot_be_ordered(dbfile):
    from src import supply

    assert supply.place_supply_order("P-NONSENSE", qty=1)["ok"] is False


def test_receiving_puts_it_on_the_shelf(dbfile):
    from src import db, supply

    with db.connect() as c:
        before = c.execute(
            """SELECT COALESCE(SUM(on_hand),0) n FROM stock s
               JOIN stock_locations l ON l.id = s.location_id
               WHERE s.sku=? AND l.kind <> 'van'""", (SKU,)).fetchone()["n"]

    o = supply.place_supply_order(SKU, qty=3)
    supply.receive(o["order"])

    with db.connect() as c:
        after = c.execute(
            """SELECT COALESCE(SUM(on_hand),0) n FROM stock s
               JOIN stock_locations l ON l.id = s.location_id
               WHERE s.sku=? AND l.kind <> 'van'""", (SKU,)).fetchone()["n"]
    assert after == before + 3


def test_a_short_delivery_says_how_short(dbfile):
    """Three ordered and two arrived is not a closed order in the usual sense,
    and the shelf must not be credited with the missing one."""
    from src import supply

    o = supply.place_supply_order(SKU, qty=3)
    got = supply.receive(o["order"], qty=2)
    assert got["received"] == 2
    assert got["short_by"] == 1


def test_receiving_twice_does_not_double_the_shelf(dbfile):
    from src import db, supply

    o = supply.place_supply_order(SKU, qty=3)
    supply.receive(o["order"])
    supply.receive(o["order"])

    with db.connect() as c:
        n = c.execute(
            """SELECT COALESCE(SUM(on_hand),0) n FROM stock s
               JOIN stock_locations l ON l.id = s.location_id
               WHERE s.sku=? AND l.kind <> 'van'""", (SKU,)).fetchone()["n"]
    assert n <= 3 + 100, "sanity"
    assert supply.receive(o["order"]).get("already") is True


def test_late_is_distinguished_from_never_ordered(dbfile):
    """The whole reason the table was missing mattered. An empty shelf because
    nobody ordered needs a different phone call from one because a supplier is
    late."""
    from src import db, supply

    o = supply.place_supply_order(SKU, qty=2)
    with db.txn() as c:
        c.execute("UPDATE supply_orders SET expected_at=? WHERE id=?",
                  ((datetime.now() - timedelta(days=3)).isoformat(timespec="seconds"),
                   o["order"]))

    out = supply.on_order("D-REF")
    assert out["open"] == 1
    assert out["late"] == 1
    assert "never ordered" in out["say"]


def test_advice_nobody_acted_on_is_surfaced(dbfile):
    """Every row is a truck roll the desk already priced and somebody quietly
    declined to prevent."""
    from src import supply

    out = supply.advised_but_not_ordered("D-REF")
    for row in out:
        assert "Nothing is coming" in row["say"]


def test_something_already_on_order_is_not_chased_again(dbfile):
    from src import supply

    # Consume the part so the advice genuinely recommends reordering it,
    # rather than skipping on a fixture that happens to be well stocked. A
    # skip here would hide the suppression never working at all.
    from src import db

    now = datetime.now().isoformat(timespec="seconds")
    with db.txn() as c:
        c.execute("UPDATE stock SET on_hand = 0 WHERE sku = ?", (SKU,))
        site = c.execute("SELECT id, account_id FROM sites LIMIT 1").fetchone()
        asset = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
        for i in range(6):
            c.execute(
                """INSERT INTO work_orders (id,account_id,site_id,asset_id,
                       reported_symptom,status,opened_at,dealer_id)
                   VALUES (?,?,?,?,'x','closed',?, 'D-REF')""",
                (f"WO-S{i}", site["account_id"], site["id"], asset["id"], now))
            c.execute("""INSERT INTO visits (id,work_order_id,seq,completed_at)
                         VALUES (?,?,1,?)""", (f"V-S{i}", f"WO-S{i}", now))
            # restock reads `repairs.parts_consumed`, not `parts_used`. The
            # closed repair is the record of what was actually fitted; the
            # reservation table only ever held a claim.
            c.execute(
                """INSERT INTO repairs (id,visit_id,asset_id,manufacturer,
                       model_number,family,reported_symptom,found_cause,
                       parts_consumed,closed_on,dealer_id)
                   VALUES (?,?,?,'Traulsen','G12010','reach-in freezer',
                           'x','fan motor seized',?,?,'D-REF')""",
                (f"R-S{i}", f"V-S{i}", asset["id"], SKU, now[:10]))

    before = {r["sku"] for r in supply.advised_but_not_ordered("D-REF")}
    assert SKU in before, "a part with no stock and steady use was not advised"

    sku = SKU
    supply.place_supply_order(sku, qty=1)
    after = {r["sku"] for r in supply.advised_but_not_ordered("D-REF")}
    assert sku not in after


# Machines, stocked for the opposite reason to parts.


def test_a_machine_we_do_not_carry_is_said_plainly(dbfile):
    """The desk's hard rule is never to claim availability unless a tool said
    so. For machines no tool could say anything at all."""
    from src import supply

    out = supply.product_availability("Traulsen", "G12010")
    assert out["stocked"] is False
    assert "do not invent a lead time" in out["say"]


def test_a_machine_in_stock_answers_with_the_number(dbfile):
    from src import db, supply

    with db.txn() as c:
        c.execute(
            """INSERT INTO product_stock
               (dealer_id,manufacturer,model_number,family,on_hand,
                lead_time_days,list_price)
               VALUES ('D-REF','Traulsen','G12010','reach-in freezer',2,14,4200)""")

    out = supply.product_availability("Traulsen", "G12010")
    assert out["stocked"] is True
    assert out["on_hand"] == 2
    assert "2 in stock" in out["say"]


def test_none_in_stock_gives_the_lead_time_not_a_shrug(dbfile):
    from src import db, supply

    with db.txn() as c:
        c.execute(
            """INSERT INTO product_stock
               (dealer_id,manufacturer,model_number,family,on_hand,on_order,
                lead_time_days)
               VALUES ('D-REF','Beverage-Air','HRP2HC','reach-in cooler',0,1,21)""")

    out = supply.product_availability("Beverage-Air", "HRP2HC")
    assert out["on_hand"] == 0
    assert "21 days" in out["say"]
    assert "1 already on order" in out["say"]


def test_machines_and_parts_are_stocked_separately(dbfile):
    """Parts are held because a missing one fails a service call, so
    availability beats cost. A machine is held at real capital cost against a
    sale that may not come. One table with one policy would force the wrong
    answer on one of them."""
    from src import db

    with db.connect() as c:
        parts_cols = {r[1] for r in c.execute("PRAGMA table_info(stock)")}
        prod_cols = {r[1] for r in c.execute("PRAGMA table_info(product_stock)")}

    assert "sku" in parts_cols
    assert "sku" not in prod_cols
    assert {"list_price", "unit_cost", "lead_time_days"} <= prod_cols


# What being short actually costs decides the margin.


def test_a_walk_in_part_is_held_harder_than_a_printer_part(dbfile):
    """A walk-in going down spoils thousands of dollars of stock and can shut
    a kitchen. A printer going down does not. One service level for both means
    over-stocking one or under-stocking the other."""
    from src.restock import _service_level
    from src.thresholds import SERVICE_Z, SERVICE_Z_CRITICAL

    assert _service_level("walk-in cooler,reach-in freezer") == SERVICE_Z_CRITICAL
    assert _service_level("printer,laptop") == SERVICE_Z
    assert _service_level("") == SERVICE_Z


def test_a_part_fitting_both_is_held_at_the_critical_level(dbfile):
    """Being generous in that direction is the cheap mistake. The expensive one
    is a restaurant losing its stock because we saved four dollars of carrying
    cost."""
    from src.restock import _service_level
    from src.thresholds import SERVICE_Z_CRITICAL

    assert _service_level("printer,walk-in cooler") == SERVICE_Z_CRITICAL
