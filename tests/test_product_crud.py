"""Changing the machines on the floor, which was a screen you could not edit.

WHAT WAS MISSING

Parts had create, price and stock. Promotions had create and stop. The 923
machines on the shop floor had nothing at all: an owner could watch their own
stock and not correct it, which makes a console a report.

TWO DECISIONS WORTH PINNING

ONLY WHAT IS PASSED CHANGES. Correcting a price must not silently zero the
stock, and a function that takes six fields and writes all six will do exactly
that the first time somebody uses it for one of them.

RETIRING IS NOT DELETING. `purchase_lines`, complaints and returns point at
what a machine sold and what came back. Removing the row would orphan the
history that explains why you stopped stocking it, so retiring takes it off
the floor and keeps the record.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_machine(dbfile):
    from src import console

    console.set_product("D-REF", "TESTUNIT-1", list_price=1499.0,
                        manufacturer="Testco", family="reach-in freezer",
                        on_hand=4, lead_time_days=6)
    return "TESTUNIT-1"


def test_a_new_machine_goes_on_the_floor(a_machine):
    from src import db

    with db.connect() as c:
        row = c.execute(
            "SELECT list_price, on_hand, price_source FROM product_stock "
            "WHERE model_number = ?", (a_machine,)).fetchone()

    assert row["list_price"] == 1499.0
    assert row["on_hand"] == 4
    assert "owner" in row["price_source"], (
        "a price a person set must not look like a market median")


def test_adding_needs_more_than_a_model_number(dbfile):
    """A bare model and a price would add a product every time somebody
    mistyped a model they meant to edit, and the floor would fill with
    near-duplicates nobody put there. Creating is a thing you should have to
    mean."""
    from src import console

    assert console.set_product("D-REF", "NOPRICE-1",
                               manufacturer="Testco")["ok"] is False
    assert console.set_product("D-REF", "NOMAKE-1",
                               list_price=99.0)["ok"] is False
    assert console.set_product("D-REF", "BOTH-1", manufacturer="Testco",
                               list_price=99.0)["ok"] is True


def test_correcting_a_price_does_not_touch_the_stock(a_machine):
    """The failure this would have had. Six fields, one of them passed, and a
    naive update writes all six: the price is fixed and the shelf reads zero."""
    from src import console, db

    console.set_product("D-REF", a_machine, list_price=1299.0)

    with db.connect() as c:
        row = c.execute(
            "SELECT list_price, on_hand, lead_time_days FROM product_stock "
            "WHERE model_number = ?", (a_machine,)).fetchone()

    assert row["list_price"] == 1299.0
    assert row["on_hand"] == 4, "the stock was zeroed by a price change"
    assert row["lead_time_days"] == 6


def test_correcting_the_stock_does_not_touch_the_price(a_machine):
    from src import console, db

    console.set_product("D-REF", a_machine, on_hand=9)

    with db.connect() as c:
        row = c.execute(
            "SELECT list_price, on_hand FROM product_stock "
            "WHERE model_number = ?", (a_machine,)).fetchone()

    assert row["on_hand"] == 9
    assert row["list_price"] == 1499.0


def test_zero_stock_is_a_real_answer_and_is_written(a_machine):
    """`on_hand=0` must mean "none left", not "leave it alone". A sentinel of
    0 would make it impossible to say the shelf is empty."""
    from src import console, db

    console.set_product("D-REF", a_machine, on_hand=0)

    with db.connect() as c:
        assert c.execute("SELECT on_hand FROM product_stock "
                         "WHERE model_number = ?",
                         (a_machine,)).fetchone()["on_hand"] == 0


def test_an_ambiguous_model_changes_nothing(dbfile):
    """Two machines matching a partial number must not have one of them
    silently picked."""
    from src import console, db

    console.set_product("D-REF", "AMBIG-100", list_price=100.0,
                        manufacturer="Testco")
    console.set_product("D-REF", "AMBIG-200", list_price=200.0,
                        manufacturer="Testco")

    out = console.set_product("D-REF", "AMBIG", list_price=999.0)
    assert out["ok"] is False
    assert len(out["which"]) == 2

    with db.connect() as c:
        prices = {r["list_price"] for r in c.execute(
            "SELECT list_price FROM product_stock WHERE model_number LIKE "
            "'AMBIG%'")}
    assert prices == {100.0, 200.0}, "an ambiguous match changed something"


def test_retiring_keeps_the_row(a_machine):
    """Not a delete. The history of what it sold is why you stopped stocking
    it, and orphaning that is worse than a stale row."""
    from src import console, db

    console.retire_product("D-REF", a_machine)

    with db.connect() as c:
        row = c.execute(
            "SELECT on_hand, on_order, price_source FROM product_stock "
            "WHERE model_number = ?", (a_machine,)).fetchone()

    assert row is not None, "the row was deleted rather than retired"
    assert row["on_hand"] == 0
    assert row["on_order"] == 0
    assert "retired" in row["price_source"]


def test_one_business_cannot_change_anothers_floor(a_machine):
    """The same tenancy rule as everything else on this desk."""
    from src import console, db

    # No manufacturer, so this cannot quietly create a D-IT copy either.
    out = console.set_product("D-IT", a_machine, list_price=1.0)
    assert out["ok"] is False

    with db.connect() as c:
        assert c.execute("SELECT list_price FROM product_stock "
                         "WHERE model_number = ?",
                         (a_machine,)).fetchone()["list_price"] == 1499.0


def test_the_phone_agent_can_never_write_the_floor(dbfile):
    """Structural, and the same rule promotions already follow: the console is
    the only place stock and prices are set. A desk that could change its own
    prices mid-call is a desk that can be talked into a discount."""
    from src import agents

    for name in ("front_agent", "desk_agent", "supply_agent", "advice_agent"):
        tools = [getattr(t, "__name__", "") for t in getattr(agents, name).tools]
        assert "set_product" not in tools
        assert "retire_product" not in tools
