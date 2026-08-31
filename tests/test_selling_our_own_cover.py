"""Buying the cover we just quoted, which is the half that had no test.

WHAT WENT WRONG, AND HOW IT GOT PAST A GREEN SUITE

`warranty_options` was changed so that, when we hold no published terms for a
make, it offers a plan of our own instead of saying "we do not know". That was
tested: the quote comes back, the tiers are right, the dear ones price
themselves out.

Nothing tested the customer saying YES.

On the very next live call the desk quoted our three year plan at $23.73, the
customer agreed, and the model put "3-year Essential warranty" on the order as
a line item. It is not a product, so nothing could price it, so the
unpriced-line gate refused the whole order and the desk said:

    "I'm sorry, I'm unable to confirm the warranty price at the moment."

Every function involved was tested and correct. The seam between them was not
tested at all, which is the same failure this project keeps finding: a suite
that exercises functions with correct inputs while the bug lives in the join.

So these tests run the ORDER, not the quote.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_desk_and_a_buyer(dbfile):
    """A machine on the floor and somebody to sell it to."""
    from src import db, tenancy, trace

    trace.call_context("CA-cover-test")
    tenancy.routed_to("D-FURN", "CA-cover-test")

    # BUILT, NOT LOOKED FOR. Depending on the fixture happening to hold a
    # furniture account meant every test below skipped silently, which is the
    # same as not having written them -- the exact trap another fixture in
    # this suite already has a docstring warning about.
    with db.txn() as c:
        c.execute("INSERT OR IGNORE INTO dealers (id,name,trade) "
                  "VALUES ('D-FURN','Riverbend Office','furniture')")
        c.execute("INSERT OR IGNORE INTO accounts (id,dealer_id,kind,name,"
                  "opened_on) VALUES ('A-COV','D-FURN','business',"
                  "'Cover Test Ltd','2026-01-01')")
        c.execute("""INSERT OR IGNORE INTO product_stock
                     (dealer_id,manufacturer,model_number,family,on_hand,
                      on_order,unit_cost,list_price,lead_time_days)
                     VALUES ('D-FURN','Testmaker','TC-100','office chair',
                             5,0,150.0,400.00,7)""")

    with db.connect() as c:
        machine = c.execute(
            """SELECT manufacturer, model_number, list_price, family
               FROM product_stock
               WHERE dealer_id='D-FURN' AND model_number='TC-100'""").fetchone()

    yield "A-COV", dict(machine)

    trace.call_over("CA-cover-test")


def test_a_cover_line_is_not_read_as_a_product(dbfile):
    """The decision everything else rests on, and the one that could do harm
    in both directions.

    Reading a real machine as cover would price it as a percentage of itself.
    Reading cover as a machine leaves it unpriceable, which is the bug.
    """
    from src.buying import _is_our_cover

    for line in ("3-year Essential warranty", "Complete cover, 5 years",
                 "2 year protection plan"):
        assert _is_our_cover(line) is True, line

    for line in ("FlexiSpot E5 Lite Standing Desk (STK-625)",
                 "the freezer with the good warranty",
                 "Serta Works Ergonomic Mesh Office Chair"):
        assert _is_our_cover(line) is False, line


def test_the_cover_they_said_yes_to_goes_on_the_order(a_desk_and_a_buyer):
    """THE BUG, exactly as it happened on the call."""
    from src import db
    from src.buying import create_purchase_order

    account, machine = a_desk_and_a_buyer
    out = create_purchase_order(
        account,
        [f"{machine['manufacturer']} {machine['model_number']}",
         "3-year Essential warranty"])

    assert out["ok"] is True
    assert not out.get("unpriced"), (
        "the cover line came back unpriced, which is what refused the order")

    with db.connect() as c:
        lines = [dict(r) for r in c.execute(
            "SELECT description, unit_price FROM purchase_lines "
            "WHERE po_id=? ORDER BY line_no", (out["purchase_order"],))]

    assert len(lines) == 2
    cover = [ln for ln in lines if "cover" in ln["description"].lower()]
    assert cover, "the cover line lost its description"
    assert cover[0]["unit_price"] > 0, "cover went on the order at nothing"

    # And the total is the machine plus the cover, not one or the other.
    assert out["subtotal"] == pytest.approx(
        sum(ln["unit_price"] for ln in lines), rel=1e-6)


def test_the_cover_is_priced_off_the_machine_on_the_same_order(a_desk_and_a_buyer):
    """A share of what they are paying, not a flat fee.

    This is why it needs two passes: the cover cannot be priced until the
    machine lines have been.
    """
    from src import db
    from src.buying import create_purchase_order

    account, machine = a_desk_and_a_buyer
    out = create_purchase_order(
        account,
        [f"{machine['manufacturer']} {machine['model_number']}",
         "3-year Essential warranty"])

    with db.connect() as c:
        rows = {r["description"]: r["unit_price"] for r in c.execute(
            "SELECT description, unit_price FROM purchase_lines WHERE po_id=?",
            (out["purchase_order"],))}

    cover = next(v for k, v in rows.items() if "cover" in k.lower())
    kit = next(v for k, v in rows.items() if "cover" not in k.lower())

    assert 0 < cover < kit, "cover should cost less than the machine"
    # Within the published retail band this is priced from, generously wide so
    # the test is about the mechanism rather than the exact rate.
    assert 0.05 <= cover / kit <= 0.30


def test_cover_alone_joins_the_machine_already_ordered(a_desk_and_a_buyer):
    """Cover is a line on the sale of the machine it protects, not an order.

    HEARD LIVE. The desk confirmed a freezer at $1,999, the customer said yes
    to three years of cover, and it raised a SECOND order carrying only the
    cover. Nothing could price it -- cover is a share of a machine and that
    order had no machine on it -- so it sat on the board reading "not priced"
    beside the freezer it belonged to.

    Even priced it would be wrong: two orders means two invoices, two delivery
    notes, and a customer who cancels one and keeps the other.
    """
    from src import db
    from src.buying import create_purchase_order

    account, machine = a_desk_and_a_buyer
    first = create_purchase_order(
        account, [f"{machine['manufacturer']} {machine['model_number']}"])

    second = create_purchase_order(account, ["3-year Essential warranty"])
    assert second["purchase_order"] == first["purchase_order"], (
        "cover was put on an order of its own instead of the machine's")

    with db.connect() as c:
        lines = [dict(r) for r in c.execute(
            "SELECT description, unit_price FROM purchase_lines "
            "WHERE po_id=? ORDER BY line_no", (first["purchase_order"],))]
    assert len(lines) == 2
    cover = [ln for ln in lines if "cover" in ln["description"].lower()]
    assert cover and cover[0]["unit_price"] > 0


def test_cover_with_nothing_at_all_behind_it_is_still_refused(dbfile):
    """The rule that has to survive the one above.

    Joining the machine's order is only possible when there IS one. With no
    machine anywhere, cover is a share of nothing, and guessing a number is
    worse than refusing.
    """
    from src import db, tenancy, trace
    from src.buying import create_purchase_order

    trace.call_context("CA-nothing")
    tenancy.routed_to("D-FURN", "CA-nothing")
    with db.txn() as c:
        c.execute("INSERT OR IGNORE INTO dealers (id,name,trade) "
                  "VALUES ('D-FURN','Riverbend Office','furniture')")
        c.execute("INSERT OR IGNORE INTO accounts (id,dealer_id,kind,name,"
                  "opened_on) VALUES ('A-NIL','D-FURN','business','Nil','2026-01-01')")

    out = create_purchase_order("A-NIL", ["3-year Essential warranty"])
    assert out.get("unpriced"), (
        "cover on its own was given a price with no machine behind it")
    trace.call_over("CA-nothing")


def test_a_tier_we_do_not_sell_is_named_rather_than_substituted(dbfile):
    """Quoting three years and invoicing five is the kind of quiet swap
    nobody notices until a claim."""
    from src.buying import _price_our_cover

    _, description, price, why = _price_our_cover(
        "99-year Essential warranty", 500.0, "office chair")

    assert price is None
    assert "99" in why or "do not sell" in why
