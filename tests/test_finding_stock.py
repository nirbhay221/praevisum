"""Finding a machine on the floor the way a person reads a label.

OBSERVED ON A LIVE CALL, AND IT COST A SALE

The caller asked for a display cooler, was offered one at $1,127, said yes, and
was then told it was not in stock with a 21 day lead time. Eleven of them were
on the floor. The order was never taken.

The lookup was `manufacturer=? AND model_number=?`, both exact, and two normal
things defeat it.

THE DATA IS SPLIT WRONG. Product titles were divided at the first space, so
the floor holds manufacturer='ESM-13R' model='1-Door Merchandiser
Refrigerator', and manufacturer='Global' model='Industrial Nexel Merchandiser
Refrigerator'. Global Industrial is the maker and ESM-13R is a model code in
the maker column. 24 of 212 rows look like this.

THE AGENT CANNOT KNOW WHICH HALF IS WHICH. On that call it passed them the
other way round, and it had no way to do better: the data itself is ambiguous.

So the fix matches on the whole name rather than the halves, and these pin it.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_mislabelled_floor(dbfile):
    """Exactly the shape the live data is in."""
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,on_order,list_price,"
                  "lead_time_days) VALUES "
                  "('D-REF','ESM-13R','1-Door Merchandiser Refrigerator',"
                  "'display cooler',11,0,1127.0,21)")
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,on_order,list_price,"
                  "lead_time_days) VALUES "
                  "('D-REF','Global','Industrial Nexel Merchandiser "
                  "Refrigerator','display cooler',10,0,1003.32,14)")
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,on_order,list_price,"
                  "lead_time_days) VALUES "
                  "('D-REF','Beverage-Air','HR1HC***G********',"
                  "'display cooler',1,0,5112.77,21)")


def test_the_call_that_lost_the_sale(a_mislabelled_floor):
    """Eleven on the floor, and it said 21 day lead time."""
    from src.supply import product_availability

    out = product_availability("ESM-13R", "1-Door Merchandiser Refrigerator",
                               "D-REF")
    assert out.get("on_hand") == 11


def test_it_works_with_the_halves_swapped(a_mislabelled_floor):
    """What the agent actually sent, and it could not have known better."""
    from src.supply import product_availability

    out = product_availability("1-Door Merchandiser Refrigerator", "ESM-13R",
                               "D-REF")
    assert out.get("on_hand") == 11


def test_a_maker_whose_name_has_a_space_in_it(a_mislabelled_floor):
    """Global Industrial was split into 'Global' and everything else."""
    from src.supply import product_availability

    out = product_availability("Global Industrial",
                               "Nexel Merchandiser Refrigerator", "D-REF")
    assert out.get("on_hand") == 10


def test_a_partial_model_still_finds_it(a_mislabelled_floor):
    """Nobody says HR1HC***G********. They say the Beverage-Air HR1HC."""
    from src.supply import product_availability

    assert product_availability("Beverage-Air", "HR1HC", "D-REF")["on_hand"] == 1
    assert product_availability("Beverage-Air HR1HC", "", "D-REF")["on_hand"] == 1


def test_it_prefers_something_we_actually_hold(dbfile):
    """Two rows can match loosely. Offer the one on the shelf, not the one on
    a 21 day lead."""
    from src import db
    from src.supply import product_availability

    with db.txn() as c:
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,list_price,lead_time_days) "
                  "VALUES ('D-REF','Acme','Cooler 100','display cooler',0,"
                  "500.0,21)")
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,list_price,lead_time_days) "
                  "VALUES ('D-REF','Acme','Cooler 200','display cooler',6,"
                  "600.0,3)")

    assert product_availability("Acme", "Cooler", "D-REF")["on_hand"] == 6


def test_something_we_genuinely_do_not_have_still_says_so(a_mislabelled_floor):
    """Loosening the match must not make it claim everything. Not carrying it
    is a real answer, and the tool already handles it without saying no."""
    from src.supply import product_availability

    out = product_availability("Wolfram", "Nonexistent 9000", "D-REF")
    assert not out.get("on_hand")


def test_one_business_cannot_see_anothers_floor(a_mislabelled_floor):
    """The looser matching must not leak across tenants."""
    from src.supply import product_availability

    out = product_availability("ESM-13R", "1-Door Merchandiser Refrigerator",
                               "D-IT")
    assert not out.get("on_hand")
