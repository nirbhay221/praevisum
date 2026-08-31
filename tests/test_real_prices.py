"""Prices are real listings, or they are not quoted.

WHAT WAS WRONG

The price list was invented. `seed_product_stock` held a dictionary of trade
costs chosen by hand, scaled by capacity, marked up by a constant. Every
number a customer heard was made up and delivered in the same voice as the
EnergyStar efficiency figures and the federal recall data sitting beside it.

A desk that says it will have to confirm is honest. A desk that says five
thousand five hundred and forty-four dollars off a number nobody can source
is not.

And the capability was already here: reviews.py had been querying Google
Shopping through Serper for ratings, and those listings carry PRICES.

Everything network-facing is stubbed. These test the judgement, not the
provider: which listings count, what happens when too few do, and that an
estimate is never passed off as a price.
"""

from __future__ import annotations

import pytest


def _listing(title, price, source="A Shop"):
    return {"title": title, "price": price, "source": source}


@pytest.fixture
def shopping(monkeypatch):
    """Swap the search for a fixed set of listings."""
    from src import market

    box = {"results": []}

    def fake(query):
        return {"shopping": box["results"]}

    monkeypatch.setattr(market, "_fetch_shopping", fake)
    return box


# Which listings count.


def test_a_price_is_a_range_not_a_number(shopping, dbfile):
    """The same machine comes back at $3,037 from one supplier and $3,978 from
    another. Quoting one as the price is a choice dressed up as a fact."""
    from src import market

    shopping["results"] = [
        _listing("True Undercounter Freezer TUC-27F-HC", "$3,037.11"),
        _listing("True Mfg. TUC-27F-HC Undercounter Freezer", "$3,500.00"),
        _listing("True Manufacturing TUC-27F-HC SPEC SERIES", "$3,978.46"),
    ]
    out = market.price_for("True", "TUC-27F-HC")

    assert out["ok"] is True
    assert out["low"] == 3037.11 and out["high"] == 3978.46
    assert out["median"] == 3500.0
    assert out["listings"] == 3
    assert "range" in out["say"]


def test_another_makers_lookalike_is_not_priced_as_ours(shopping, dbfile):
    """That search really does return an $859 USR Brands Coldline UC-27F.
    A confident price for the wrong freezer is the same error as a confident
    rating for one."""
    from src import market

    shopping["results"] = [
        _listing("True Undercounter Freezer TUC-27F-HC", "$3,037.11"),
        _listing("True Mfg. TUC-27F-HC Undercounter Freezer", "$3,500.00"),
        _listing("True Manufacturing TUC-27F-HC Spec", "$3,978.46"),
        _listing("USR Brands Coldline Undercounter Freezer UC-27F", "$859.00"),
        _listing("Webcoolers UC-27F Undercounter Freezer", "$1,190.00"),
    ]
    out = market.price_for("True", "TUC-27F-HC")

    assert out["listings"] == 3, "only the three real Trues"
    assert out["low"] == 3037.11


def test_one_listing_is_a_quote_not_a_market(shopping, dbfile):
    from src import market

    shopping["results"] = [_listing("True TUC-27F-HC Freezer", "$3,037.11")]
    out = market.price_for("True", "TUC-27F-HC")

    assert out["ok"] is False
    assert "supplier" in out["why"]
    assert "price it up and come back" in out["say"]


def test_a_scraped_nonsense_price_does_not_drag_the_median(shopping, dbfile):
    from src import market

    shopping["results"] = [
        _listing("True TUC-27F-HC Freezer", "$3,000.00"),
        _listing("True TUC-27F-HC Freezer", "$3,200.00"),
        _listing("True TUC-27F-HC Freezer", "$3,400.00"),
        _listing("True TUC-27F-HC Freezer door gasket", "$89.00"),
    ]
    out = market.price_for("True", "TUC-27F-HC")
    assert out["median"] == 3200.0
    assert out["low"] >= 3000.0


# Model variants.


def test_the_model_is_tried_at_decreasing_precision(dbfile):
    """Our catalogue holds TUC-27F-LP-HC~SPEC3; the shops list TUC-27F-HC.
    Requiring the whole string found nothing for a machine with 35 listings."""
    from src import market

    tries = market._model_attempts("TUC-27F-LP-HC~SPEC3")
    assert tries[0] == "TUC-27F-LP-HC"
    assert tries[-1] == "TUC-27F"


def test_it_never_falls_back_to_the_make_alone(dbfile):
    """The make on its own would price an undercounter off a walk-in.

    The floor stops TRUNCATION, not short models. Continental's model really
    is "1FEN" and searching for it finds 25 real listings, so a model that is
    already short is used whole; what must never happen is a long model being
    cut down to a stub that matches other machines.
    """
    from src import market

    for model in ("TUC-27F-LP-HC~SPEC3", "G12010"):
        for attempt in market._model_attempts(model):
            assert len(attempt.replace("-", "")) >= 5, (
                f"{model} was truncated to {attempt}, which is short enough to "
                "match a different machine")

    assert market._model_attempts("1FEN") == ["1FEN"], (
        "a model that is genuinely short is searched whole, not discarded")


def test_a_long_model_is_never_cut_to_a_stub(dbfile):
    from src import market

    assert "TUC" not in market._model_attempts("TUC-27F-LP-HC~SPEC3")


def test_a_looser_match_says_so(shopping, dbfile):
    from src import market

    shopping["results"] = [
        _listing("True TUC-27F-HC Undercounter Freezer", "$3,000.00"),
        _listing("True TUC-27F-HC Freezer", "$3,200.00"),
        _listing("True Mfg TUC-27F-HC", "$3,400.00"),
    ]
    out = market.price_for("True", "TUC-27F-LP-HC~SPEC3")

    assert out["ok"] is True
    assert out["matched_on"] == "TUC-27F"
    assert "closest we can see" in out["say"]


# A budget our own shelf cannot meet.


def test_the_open_market_is_searched_when_our_shelf_cannot_help(shopping, dbfile):
    """Saying we do not stock one is a much worse answer than naming a
    KoolMore at fifteen hundred and offering to source it."""
    from src import market

    shopping["results"] = [
        _listing("KoolMore 12 cu. ft. Commercial Reach-In Freezer", "$1,507.00",
                 "Walmart"),
        _listing("Pemberly Row 21 cu. ft. Commercial Reach-in Freezer",
                 "$1,830.00", "Cymax"),
        _listing("Traulsen G12010", "$6,599.00", "WebstaurantStore"),
    ]
    out = market.alternatives("reach-in freezer", 2000)

    assert [f["price"] for f in out["found"]] == [1830.0, 1507.0]
    assert "not our stock" in out["say"]
    assert "Never imply we have one on the floor" in out["say"]


def test_accessories_are_not_offered_as_machines(shopping, dbfile):
    """Searching for a commercial freezer returns gaskets and shelf kits, all
    cheap and all matching the words."""
    from src import market

    shopping["results"] = [
        _listing("KoolMore Commercial Reach-In Freezer", "$1,507.00"),
        _listing("Freezer door gasket for reach-in", "$89.00"),
        _listing("Reach-in freezer shelf kit", "$120.00"),
    ]
    out = market.alternatives("reach-in freezer", 2000)
    assert [f["price"] for f in out["found"]] == [1507.0]


def test_an_empty_market_is_a_useful_answer(shopping, dbfile):
    """It tells them the budget is the problem rather than the supplier."""
    from src import market

    shopping["results"] = [_listing("Traulsen G12010", "$6,599.00")]
    out = market.alternatives("reach-in freezer", 2000)

    assert out["found"] == []
    assert "budget is the problem rather than the supplier" in out["say"]


# An estimate is never passed off as a price.


def test_an_estimated_price_is_labelled_as_one(dbfile, monkeypatch):
    from scripts import seed_product_stock as seed

    from src import db, market

    monkeypatch.setattr(market, "price_for",
                        lambda *a, **k: {"ok": False, "why": "no listings"})
    seed.load()

    with db.connect() as c:
        rows = c.execute("SELECT price_source FROM product_stock").fetchall()
    assert rows
    assert all("ESTIMATED" in (r["price_source"] or "") for r in rows)


def test_a_real_price_records_how_many_listings_it_came_from(dbfile, monkeypatch):
    from scripts import seed_product_stock as seed

    from src import db, market

    monkeypatch.setattr(market, "price_for", lambda *a, **k: {
        "ok": True, "median": 6599.0, "listings": 22})
    seed.load()

    with db.connect() as c:
        row = c.execute("SELECT list_price, price_source FROM product_stock "
                        "LIMIT 1").fetchone()
    assert row["list_price"] == 6599.0
    assert "22 real listings" in row["price_source"]


def test_the_desk_is_told_not_to_read_out_an_estimate(dbfile):
    from src import agents

    r = " ".join(agents.DESK_RULES.split())
    assert "PRICES ARE REAL OR THEY ARE NOT QUOTED" in r
    assert "do NOT read it out as a price" in r


def test_both_tools_are_on_the_desk(dbfile):
    from src import agents

    n = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "price_for" in n and "alternatives" in n
