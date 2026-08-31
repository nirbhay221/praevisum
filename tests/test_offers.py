"""Offers applied at the moment of the quote, and never invented.

THE GAP THIS CLOSES

The owner records promotions through the console and `promotion_parts` maps
them to the exact SKUs they cover. Grep for what read that mapping: the console
that writes it, outreach that rings customers about it, and `current_deals`,
which the desk calls only when somebody thinks to ask "what offers are on?".

No pricing path read it at all. So a customer ringing about a door gasket was
quoted the full 92.00 while a live 15% offer on door gaskets sat in the
database, and the only route to the discount was already knowing it existed.
That is not merely a lost margin, it is a customer discovering afterwards that
there was an offer nobody mentioned, from a desk whose whole proposition is
that it does not do that.

THE HARDER HALF IS KNOWING WHEN NOT TO COMPUTE

A promotion is a headline written by a person, and there is no discount column.
Two of the four in the book are arithmetic:

    "10% off defrost components"      "15% off door gaskets"

Two are not:

    "Evaporator fan motors, buy 3 pay for 2"
    "Free first-year labour on planned maintenance"

A buy-three-pay-for-two depends on quantity and free labour is not a discount
on a part. The failure mode worth testing for is not missing an offer: it is a
desk that produces "so that works out about 30% off" and commits the business
to a number nobody agreed.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def promos(dbfile):
    """A percentage offer, a non-computable offer, and a trade-only one."""
    from src import db

    with db.txn() as c:
        c.executemany(
            "INSERT INTO parts (sku,dealer_id,name,unit_cost,lead_time_days) "
            "VALUES (?,?,?,?,?)",
            [("T-GASKET", "D-REF", "Door gasket", 92.0, 3),
             ("T-FAN", "D-REF", "Evaporator fan motor", 94.10, 5),
             ("T-PLAIN", "D-REF", "Shelf clip", 4.20, 1)])

        c.executemany(
            "INSERT INTO promotions (id,dealer_id,headline,detail,ends,terms) "
            "VALUES (?,?,?,?,?,?)",
            [("TP-1", "D-REF", "15% off door gaskets", "all sizes",
              "2099-01-01", "trade accounts only"),
             ("TP-2", "D-REF", "Evaporator fan motors, buy 3 pay for 2",
              "across all fitments", "2099-01-01", ""),
             ("TP-OLD", "D-REF", "50% off shelf clips", "", "2020-01-01", "")])

        c.executemany(
            "INSERT INTO promotion_parts (promotion_id,sku) VALUES (?,?)",
            [("TP-1", "T-GASKET"), ("TP-2", "T-FAN"), ("TP-OLD", "T-PLAIN")])


def test_a_percentage_offer_is_actually_applied(promos):
    from src import offers

    out = offers.offer_on("T-GASKET", "D-REF", "on_account")
    assert out["applies"] is True
    assert out["computed"] is True
    assert out["was"] == 92.0
    assert out["now"] == 78.20
    assert out["saving"] == 13.80


def test_an_offer_that_is_not_arithmetic_is_never_turned_into_a_price(promos):
    """The important one. A buy-three-pay-for-two depends on how many they
    take, so there is no unit price to quote and the desk is told to read the
    offer out instead of doing sums."""
    from src import offers

    out = offers.offer_on("T-FAN", "D-REF", "on_account")
    assert out["applies"] is True
    assert out["computed"] is False
    assert "now" not in out
    assert "saving" not in out
    assert "work nothing out" in out["say"] or "do NOT work out" in out["say"]


def test_an_expired_offer_is_not_offered(promos):
    from src import offers

    assert offers.offer_on("T-PLAIN", "D-REF", "on_account")["applies"] is False


def test_a_trade_only_offer_is_withheld_from_somebody_without_an_account(promos):
    """Reading an offer out and then withdrawing it at the counter is worse
    than never raising it."""
    from src import offers

    assert offers.offer_on("T-GASKET", "D-REF", "known")["applies"] is False
    assert offers.offer_on("T-GASKET", "D-REF", "new")["applies"] is False
    assert offers.offer_on("T-GASKET", "D-REF", "on_account")["applies"] is True


def test_an_unidentified_caller_still_hears_it(promos):
    """Deliberate, and the reason is in _qualifies: before we know who they
    are, staying quiet at somebody who turns out to hold an account is a lost
    sale we caused, while mentioning one they cannot use is a conversation."""
    from src import offers

    assert offers.offer_on("T-GASKET", "D-REF", "unknown")["applies"] is True


def test_a_part_with_no_offer_says_so_plainly(promos):
    from src import offers

    assert offers.offer_on("T-NOTHING", "D-REF", "on_account")["applies"] is False


def test_another_dealers_promotion_is_not_ours(promos):
    """Same tenancy rule as everything else: an offer belongs to one business."""
    from src import offers

    assert offers.offer_on("T-GASKET", "D-IT", "on_account")["applies"] is False


@pytest.mark.parametrize("headline,expected", [
    ("15% off door gaskets", 15),
    ("10 % off defrost components", 10),
    ("15% OFF everything", 15),
    ("Evaporator fan motors, buy 3 pay for 2", 0),
    ("Free first-year labour on planned maintenance", 0),
    ("100% off", 0),
    ("Half price gaskets", 0),
])
def test_only_an_unambiguous_percentage_is_read_as_one(dbfile, headline,
                                                       expected):
    """"Half price" is a discount a human understands and this deliberately
    does not, because the alternative is a matcher that guesses at prose and
    occasionally guesses wrong about money."""
    from src.offers import _percentage

    assert _percentage(headline) == expected


def test_a_whole_quote_totals_only_what_it_could_compute(promos):
    """The gasket saving counts. The fan motor offer is real but has no number,
    so it appears in the list and contributes nothing to the total rather than
    being estimated into it."""
    from src import offers

    out = offers.offers_on_many(["T-GASKET", "T-FAN", "T-PLAIN"], "D-REF",
                                "on_account")
    assert out["any"] is True
    assert len(out["offers"]) == 2
    assert out["total_saving"] == 13.80
