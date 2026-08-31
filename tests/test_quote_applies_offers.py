"""A live offer reaching the quote it applies to.

offers.py opens with its own purpose: "Applying a live offer at the moment of
the quote, instead of when asked." It did not happen. `offers_on_many` was
written for exactly this, described as "every live offer touching a list of
parts, for a whole quote", and nothing called it. A repair quote containing a
part under a running offer was priced at full cost, and the customer only got
the discount if the agent separately remembered to look it up.

THE BUG INSIDE THE FIX, WHICH IS THE ONE WORTH KEEPING

The first version read the result as a dict keyed by sku and looked for
`offer_price`. It returns {"offers": [ ... ]}, a list, and the discounted
figure is called `now`. Nothing raised, nothing logged, and every part was
still quoted at full price. A guess about a return shape fails silently and
looks exactly like a feature that is switched off.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_discounted_part(dbfile):
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-Q','D-REF','business','Quoted Cafe','2020-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label,lat,lon) "
                  "VALUES ('S-Q','A-Q','kitchen',41.5,-90.5)")
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on,installed_source) VALUES "
                  "('AS-Q','S-Q','Testco','TQ-1','reach-in cooler',"
                  "'2015-01-01','sold_by_us')")
        c.execute("INSERT INTO parts (sku,dealer_id,name,unit_cost,"
                  "lead_time_days) VALUES "
                  "('P-Q','D-REF','Defrost thermostat',100.0,3)")
        # The percentage is read out of the headline a person wrote. There is
        # no percent column: offers.py parses the words, because a promotion
        # is a sentence somebody typed.
        c.execute("INSERT INTO promotions (id,dealer_id,headline,starts,ends) "
                  "VALUES ('PR-Q','D-REF','20% off defrost components',"
                  "'2020-01-01','2099-01-01')")
        c.execute("INSERT INTO promotion_parts (promotion_id,sku) "
                  "VALUES ('PR-Q','P-Q')")
    return "P-Q"


def test_the_offer_reaches_the_quote(a_discounted_part):
    """The whole gap: the offer existed, the quote ignored it."""
    from src import pricing

    q = pricing.quote_visit("AS-Q", [a_discounted_part])
    part = [l for l in q["lines"] if "Defrost" in l["what"]][0]

    assert part["amount"] == 80.0, "the live offer was not applied"
    assert part["was"] == 100.0


def test_the_discount_is_said_out_loud(a_discounted_part):
    """A price that is quietly lower is a price the customer cannot check, and
    the offer's own guidance is to mention it before they agree the full
    amount rather than after."""
    from src import pricing

    q = pricing.quote_visit("AS-Q", [a_discounted_part])
    part = [l for l in q["lines"] if "Defrost" in l["what"]][0]

    assert "20% off defrost components" in part["why"]
    assert "saving 20.00" in part["why"]


def test_the_total_reflects_it(a_discounted_part):
    from src import db, pricing

    with_offer = pricing.quote_visit("AS-Q", [a_discounted_part])["total"]
    with db.txn() as c:
        c.execute("UPDATE promotions SET ends='2021-01-01' WHERE id='PR-Q'")
    without = pricing.quote_visit("AS-Q", [a_discounted_part])["total"]

    assert without - with_offer == pytest.approx(20.0, abs=0.01)


def test_an_expired_offer_does_not_discount(a_discounted_part):
    from src import db, pricing

    with db.txn() as c:
        c.execute("UPDATE promotions SET ends='2021-01-01' WHERE id='PR-Q'")

    part = [l for l in pricing.quote_visit("AS-Q", [a_discounted_part])["lines"]
            if "Defrost" in l["what"]][0]
    assert part["amount"] == 100.0
    assert "was" not in part


def test_a_broken_offers_lookup_leaves_the_quote_whole(a_discounted_part,
                                                       monkeypatch):
    """Charging the undiscounted amount is a conversation. Losing the quote is
    a dropped call."""
    from src import pricing

    def boom(*a, **k):
        raise RuntimeError("offers table gone")

    monkeypatch.setattr("src.offers.offers_on_many", boom)

    q = pricing.quote_visit("AS-Q", [a_discounted_part])
    assert q["total"] > 0
    part = [l for l in q["lines"] if "Defrost" in l["what"]][0]
    assert part["amount"] == 100.0


def test_the_result_shape_is_read_not_guessed(dbfile):
    """The bug inside the fix. Pin the two field names the pricing code
    depends on, so a rename over in offers.py fails here loudly instead of
    quietly reverting every quote to full price."""
    from src import db
    from src.offers import offers_on_many

    with db.txn() as c:
        c.execute("INSERT INTO parts (sku,dealer_id,name,unit_cost,"
                  "lead_time_days) VALUES ('P-S','D-REF','Thing',50.0,1)")
        c.execute("INSERT INTO promotions (id,dealer_id,headline,starts,ends) "
                  "VALUES ('PR-S','D-REF','50% off things','2020-01-01',"
                  "'2099-01-01')")
        c.execute("INSERT INTO promotion_parts (promotion_id,sku) "
                  "VALUES ('PR-S','P-S')")

    got = offers_on_many(["P-S"], "D-REF", "unknown")
    assert isinstance(got.get("offers"), list), "offers is a list, not a map"
    row = got["offers"][0]
    assert "sku" in row and "now" in row and "applies" in row, (
        "pricing reads sku, now and applies. One of them moved.")
