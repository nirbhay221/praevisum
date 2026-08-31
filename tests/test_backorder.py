"""Selling something we do not hold, and actually ordering it.

WHAT WAS MISSING

`confirm_purchase_order` set the status to confirmed and stopped. It never
asked whether we had the machine, never ordered one, and never told the
customer the wait was because we were sourcing it. So an order could be
confirmed for a freezer nobody owned and nothing anywhere would buy one: the
customer waits indefinitely for a delivery that was never coming.

The schema had anticipated it the whole time. Both `purchase_orders` and
`supply_orders` carry `equipment_id`. Only the link was absent.

BACK-TO-BACK IS WHAT THE TRADE CALLS THIS

A supply order raised on the back of a customer order and hard pegged to it.
Three properties, each of which breaks something if skipped: one supply order
to one customer line, what arrives is RESERVED for that customer, and it
carries the customer reference so a buyer chasing a supplier knows who is
waiting.

AND THE KIND MATTERS

Replenishment and a customer waiting are different orders that lived in the
same table looking identical. One is "the shelf is getting low". The other has
a restaurant behind it.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def an_order(dbfile):
    """A confirmed customer order for things we do not hold."""
    from src import db

    with db.connect() as c:
        account = c.execute("SELECT id FROM accounts LIMIT 1").fetchone()["id"]

    with db.txn() as c:
        c.execute("""INSERT INTO purchase_orders
                     (id,account_id,status,subtotal,placed_at)
                     VALUES ('PO-1',?,'draft',6599.0,'2026-08-27T09:00:00')""",
                  (account,))
        c.execute("""INSERT INTO purchase_lines
                     (po_id,line_no,description,qty,unit_price)
                     VALUES ('PO-1',1,'Traulsen G12010 reach-in freezer',1,6599.0)""")
    return "PO-1"


# What something actually is, rather than what words are in its name.


@pytest.mark.parametrize("description,expected,why", [
    ("Traulsen G12010 reach-in freezer", 21, "machine"),
    ("Compressor overload relay", 3, "shelf part"),
    ("Compressor start capacitor", 3, "shelf part"),
    ("Door gasket", 3, "shelf part"),
    ("Semi-hermetic compressor", 90, "OEM"),
    ("Evaporator coil", 90, "OEM"),
    ("Electronic control board", 15, "specialised"),
])
def test_lead_time_follows_what_the_thing_is(dbfile, description, expected, why):
    """"Compressor overload relay" is a fifty dollar shelf part and matching on
    the word "compressor" quoted a customer NINETY DAYS for one.

    The same failure as "Continental" matching car tyres and "bunn" matching
    "Woven Bunny Baskets": a word appearing in a name is not the thing.
    """
    from src import backorder

    days, _ = backorder._lead_days(description, False)
    assert days == expected


def test_our_own_catalogue_beats_any_guess(dbfile):
    """parts.lead_time_days is a real figure sitting in the database, and
    inferring from words while ignoring it is how the relay became 90 days."""
    from src import backorder, db

    with db.connect() as c:
        sku = c.execute("SELECT sku FROM parts WHERE lead_time_days IS NOT NULL "
                        "LIMIT 1").fetchone()["sku"]
        real = c.execute("SELECT lead_time_days FROM parts WHERE sku=?",
                         (sku,)).fetchone()["lead_time_days"]

    days, why = backorder._lead_days("Semi-hermetic compressor", False, sku)
    assert days == real
    assert "our own catalogue" in why


def test_a_machine_by_description_is_not_priced_as_a_part(dbfile):
    """Ordered by description rather than catalogue id, a whole freezer was
    given a three day part lead time. That is a promise nobody can keep."""
    from src import backorder

    days, why = backorder._lead_days("Traulsen G12010 reach-in freezer", False)
    assert days == backorder.LEAD_DAYS["machine"]
    assert "whole machine" in why


# Confirming an order sources it.


def test_confirming_raises_a_supply_order_for_what_we_lack(an_order):
    from src import buying, db

    out = buying.confirm_purchase_order(an_order, agreed_by="Arjun Raman")

    assert out["status"] == "confirmed"
    assert out["being_sourced"], "nothing was ordered for a machine we do not hold"

    with db.connect() as c:
        so = c.execute("SELECT * FROM supply_orders WHERE for_purchase_order=?",
                       (an_order,)).fetchone()
    assert so is not None
    assert so["kind"] == "back_to_back"
    assert so["reserved"] == 1
    assert so["for_line"] == 1


def test_the_customer_line_records_what_it_is_waiting_on(an_order):
    """So the desk can answer "where is my freezer" without a buyer reading a
    spreadsheet."""
    from src import buying, db

    buying.confirm_purchase_order(an_order)

    with db.connect() as c:
        line = c.execute("SELECT sourced_by FROM purchase_lines WHERE po_id=?",
                         (an_order,)).fetchone()
    assert line["sourced_by"], "the line does not know what is coming for it"


def test_the_date_given_is_when_it_reaches_THEM(an_order):
    """A promise that assumes the supplier's date is the customer's date
    breaks on the last mile."""
    from datetime import date, timedelta

    from src import backorder, buying

    out = buying.confirm_purchase_order(an_order)
    line = out["being_sourced"][0]

    arrives = date.fromisoformat(line["expected_here"])
    promised = date.fromisoformat(line["promised_by"])
    assert promised - arrives == timedelta(days=backorder.OUR_HANDLING_DAYS)


def test_the_desk_is_told_not_to_shorten_the_date(an_order):
    from src import buying

    out = buying.confirm_purchase_order(an_order)
    assert "Do not shorten the date" in out["told_caller"]
    assert "do not have it on the floor" in out["told_caller"]


def test_saying_yes_twice_does_not_order_two(an_order):
    """Saying yes twice on a phone call is ordinary."""
    from src import buying, db

    buying.confirm_purchase_order(an_order)
    buying.confirm_purchase_order(an_order)

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM supply_orders "
                      "WHERE for_purchase_order=?", (an_order,)).fetchone()["n"]
    assert n == 1


def test_something_on_the_floor_is_not_ordered_again(dbfile):
    from src import backorder, db

    with db.connect() as c:
        account = c.execute("SELECT id FROM accounts LIMIT 1").fetchone()["id"]
    with db.txn() as c:
        c.execute("""INSERT INTO product_stock
                     (dealer_id,manufacturer,model_number,family,on_hand,list_price)
                     VALUES ('D-REF','Traulsen','G12010','reach-in freezer',3,6599)""")
        c.execute("""INSERT INTO purchase_orders (id,account_id,status,placed_at)
                     VALUES ('PO-2',?,'draft','2026-08-27T09:00:00')""", (account,))
        c.execute("""INSERT INTO purchase_lines (po_id,line_no,description,qty)
                     VALUES ('PO-2',1,'Traulsen G12010',1)""")

    out = backorder.source_order("PO-2")
    assert out["sourced"] == []
    assert out["from_stock"]


# What arrives is spoken for.


def test_a_pegged_delivery_is_not_put_on_the_general_shelf(an_order):
    """The whole point of the peg: the machine turns up, goes on the shelf,
    and is sold to whoever asks next while the customer who paid keeps
    waiting."""
    from src import backorder, buying

    out = buying.confirm_purchase_order(an_order)
    so_id = out["being_sourced"][0]["supply_order"]

    got = backorder.receive_reserved(so_id)
    assert got["reserved_for"] == an_order
    assert "Do NOT put it on the general shelf" in got["say"]


def test_receiving_it_twice_is_harmless(an_order):
    from src import backorder, buying

    out = buying.confirm_purchase_order(an_order)
    so_id = out["being_sourced"][0]["supply_order"]

    backorder.receive_reserved(so_id)
    assert backorder.receive_reserved(so_id)["already"] is True


# The buyer's list.


def test_the_buyer_sees_only_orders_with_somebody_waiting(an_order):
    """Replenishment is deliberately left out. This is the list of orders with
    a named customer on the other end."""
    from src import backorder, buying, db

    buying.confirm_purchase_order(an_order)
    with db.txn() as c:
        c.execute("""INSERT INTO supply_orders
                     (id,dealer_id,qty,status,placed_at,kind)
                     VALUES ('SO-STOCK','D-REF',5,'placed','2026-08-27T09:00:00',
                             'replenishment')""")

    out = backorder.waiting_on()
    assert out["waiting"] == 1
    assert all(o["for_purchase_order"] for o in out["orders"])


def test_a_missed_promise_is_visible(an_order):
    """A late one is somebody who was given a date and has not been rung."""
    from src import backorder, buying, db

    buying.confirm_purchase_order(an_order)
    with db.txn() as c:
        c.execute("UPDATE supply_orders SET promised_by='2020-01-01' "
                  "WHERE for_purchase_order=?", (an_order,))

    out = backorder.waiting_on()
    assert out["late"] == 1


# The budget question that started this.


def test_best_within_a_budget_is_the_dearest_not_the_cheapest(dbfile, monkeypatch):
    """Somebody with two thousand dollars wants the best machine two thousand
    dollars buys. Being shown the cheapest reads as being fobbed off."""
    from src import db, supply

    with db.txn() as c:
        c.executemany(
            """INSERT INTO product_stock
               (dealer_id,manufacturer,model_number,family,on_hand,list_price)
               VALUES ('D-REF',?,?,'reach-in freezer',0,?)""",
            [("A", "CHEAP", 900.0), ("B", "MIDDLE", 1400.0),
             ("C", "BEST", 1900.0), ("D", "OVER", 2600.0)])

    out = supply.options_under(2000, "reach-in freezer")
    assert [o["model_number"] for o in out["options"]][0] == "BEST"
    assert "OVER" not in [o["model_number"] for o in out["options"]]


def test_the_desk_is_told_it_can_sell_what_it_does_not_hold(dbfile):
    from src import agents

    r = " ".join(agents.DESK_RULES.split())
    assert "WE DO NOT HAVE TO HOLD IT TO SELL IT" in r
    assert "Never shorten that date to sound helpful" in r
