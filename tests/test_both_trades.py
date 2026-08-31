"""Two vendors behind one desk, and only one of them worked.

WHAT WAS WRONG

The database has always been multi-tenant. Two vendors, two trades, separate
technicians, parts and repair corpora, every query scoped by dealer_id:

    D-REF  Midwest Commercial Refrigeration   reach-in freezer, ice machine...
    D-IT   Quad City IT Services              laptop, desktop, server, printer

The IT dealer has 161 machines and 240 repairs on its book. Everything built
on top of the tenancy was refrigeration-shaped anyway:

  THE LABOUR RATE was a constant naming occupation 49-9021, refrigeration
  mechanics. An IT job was quoted at a refrigeration mechanic's wage.

  THE WARRANTY TERMS held fifteen refrigeration brands and no Dell, Lenovo or
  HP, so every IT customer was told we hold no terms for their make.

  THE PRICE LIST skipped every IT family, so that business had zero machines
  on it and could not answer "have you got one" about anything.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, dealer_id):
        self.state = {"dealer_id": dealer_id}


@pytest.fixture
def rates(dbfile):
    from scripts.load_trade_rates import load
    return load()


@pytest.fixture
def terms(dbfile):
    from scripts.load_warranties import load
    return load()


# The front counter moved out.
#
# It used to answer as whichever vendor was dialled, which is the bug this file
# was written about. That is now one desk over both vendors and is tested in
# test_one_front_many_vendors.py. What stays here is the half that is still
# per-vendor underneath: the wage, the warranty terms and the price list.


def test_the_trade_reads_like_a_person_said_it(dbfile):
    """Kept because the helper is still used where a trade IS named. The desk
    itself no longer announces one: it covers all of them."""
    from src import agents

    assert agents._a("it") == "an IT"
    assert agents._a("refrigeration") == "a refrigeration"


# The labour rate.


def test_each_trade_is_priced_at_its_own_wage(rates):
    from src import pricing

    ref = pricing.labour_rate("D-REF")
    it = pricing.labour_rate("D-IT")

    assert ref["rate"] != it["rate"]
    assert "49-9021" in ref["source"]
    assert "15-1232" in it["source"]


def test_the_rate_says_where_the_figure_came_from(rates):
    """One is a metro figure and the other is state-wide, because BLS does not
    publish that occupation for this metro. Quoting a state average as though
    it were local is the same quiet dishonesty as inventing one."""
    from src import pricing

    assert "Davenport" in pricing.labour_rate("D-REF")["source"]
    it = pricing.labour_rate("D-IT")["source"]
    assert "Iowa" in it and "no metro series is published" in it


def test_the_call_out_differs_by_trade(rates):
    """A refrigeration van with its stock, its recovery machine and its EPA
    certification is not the same cost to send as somebody with a toolkit."""
    from src import pricing

    assert pricing.call_out_fee("D-REF") > pricing.call_out_fee("D-IT")


def test_a_dealers_own_posted_rate_still_wins(rates):
    from src import db, pricing

    with db.txn() as c:
        c.execute("UPDATE dealers SET labour_rate=145.0 WHERE id='D-IT'")
    assert pricing.labour_rate("D-IT")["rate"] == 145.0


# Warranty.


def test_the_it_makes_have_published_terms_too(terms):
    from src import db

    with db.connect() as c:
        n = c.execute(
            """SELECT COUNT(*) n FROM warranty_terms
               WHERE manufacturer IN ('DELL','Lenovo','HP','ASUS','Acer')"""
        ).fetchone()["n"]
    assert n > 0, "every IT customer was told we hold no terms for their make"


def test_a_business_laptop_is_covered_longer_than_a_consumer_one(terms):
    """Within the same brand the two lines differ by two years, which is the
    same shape as Beverage-Air's CF and CT split."""
    from src import cover

    assert cover.published_terms("Lenovo", "ThinkPad T14")["parts_years"] == 3
    assert cover.published_terms("Lenovo", "IdeaPad 3")["parts_years"] == 1
    assert cover.published_terms("DELL", "Latitude 5440")["parts_years"] == 3
    assert cover.published_terms("DELL", "Inspiron 15")["parts_years"] == 1


def test_a_laptop_battery_is_a_wear_item(terms):
    """The door gasket of this trade: the commonest thing to fail, everybody
    assumes it is covered, and it is consistently excluded."""
    from src import cover

    out = cover.is_wear_item("Replacement battery")
    assert out is not None
    assert "consumable" in out["why"]


def test_toner_is_not_a_fault(terms):
    from src import cover

    assert cover.is_wear_item("Toner cartridge") is not None


def test_the_terms_reach_both_trades_not_just_one(terms, dbfile):
    """On the real book they reached 131 of 424 machines when only
    refrigeration was loaded, and 285 once the IT makes were added. The
    fixture holds three machines, so this asserts the shape rather than the
    number: terms exist for BOTH trades.
    """
    from src import db

    with db.connect() as c:
        fridge = c.execute(
            """SELECT COUNT(*) n FROM warranty_terms
               WHERE manufacturer IN ('Traulsen','True Refrigeration',
                                      'Continental','Beverage-Air')"""
        ).fetchone()["n"]
        computers = c.execute(
            """SELECT COUNT(*) n FROM warranty_terms
               WHERE manufacturer IN ('DELL','Lenovo','HP','ASUS','Acer')"""
        ).fetchone()["n"]

    assert fridge > 0 and computers > 0


# The price list.


def test_the_it_dealer_has_machines_on_its_price_list(dbfile, monkeypatch):
    from scripts.seed_product_stock import load

    from src import db, market

    monkeypatch.setattr(market, "price_for",
                        lambda *a, **k: {"ok": False, "why": "stubbed"})
    load()

    with db.connect() as c:
        n = c.execute(
            """SELECT COUNT(*) n FROM product_stock
               WHERE family IN ('laptop','desktop','server','printer','ups')"""
        ).fetchone()["n"]
    assert n > 0, "that business could not answer 'have you got one' about anything"


def test_a_laptop_is_not_priced_like_a_walk_in(dbfile, monkeypatch):
    from scripts.seed_product_stock import load

    from src import db, market

    monkeypatch.setattr(market, "price_for",
                        lambda *a, **k: {"ok": False, "why": "stubbed"})
    load()

    with db.connect() as c:
        laptop = c.execute("SELECT AVG(list_price) p FROM product_stock "
                           "WHERE family='laptop'").fetchone()["p"]
        walkin = c.execute("SELECT AVG(list_price) p FROM product_stock "
                           "WHERE family='walk-in cooler'").fetchone()["p"]
    if laptop and walkin:
        assert laptop < walkin


# What was already right and must stay right.


def test_a_laptop_still_needs_no_refrigerant_certificate(dbfile):
    """The certification gate was correct from the start: it asks about
    circuits, not competence in general."""
    from src import cover, db

    with db.connect() as c:
        t = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()["id"]

    out = cover.can_work_on(t, "laptop")
    assert out["allowed"] is True
    assert "no refrigerant certification is required" in out["why"]
