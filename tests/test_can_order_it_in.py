"""Not on our price list is not a dead end.

FROM A LIVE CALL

    "we don't stock that particular model. Would you be interested in us
     getting a price for you, or would you like to look at a different brand?"

Said three times, to somebody who had said three times that they wanted to buy
the machine. Nothing was ever raised.

TWO CAUSES, BOTH OURS

product_availability's own instruction for an unstocked machine said "say we
do not carry it, offer to price it in". That predates the back-to-back work
and directly contradicts the rule that we do not have to hold something to
sell it. The tool's words won and the order was never taken.

And the desk cannot take an order anyway: create_purchase_order lives on the
supply sub-agent, not on the front. So the first fix pointed it at a tool it
could not reach, which would have failed in a new way.

A DOMESTIC FREEZER IS NOT A CHEAP COMMERCIAL ONE

The same call offered a restaurant a Danby 8.5 cubic foot upright and a Summit
compact at a six hundred dollar budget. Household machines: no NSF rating, a
domestic duty cycle, and a warranty void the moment it is used in a business.
Offering one is worse than saying nothing exists at that price, because they
might buy it.
"""

from __future__ import annotations

import pytest


# Not on the list, still sellable.


def test_an_unstocked_machine_can_still_be_ordered(dbfile):
    from src import supply

    out = supply.product_availability("Hoshizaki", "KM-901MAJ")

    assert out["stocked"] is False
    assert out["can_order"] is True
    assert out["lead_time_days"] > 0
    assert "Do NOT stop at" in out["say"]


def test_the_desk_takes_the_order_itself(dbfile):
    """It moved, and the instruction moved with it.

    The old version of this asserted the opposite: create_purchase_order was
    deliberately NOT on the front desk, and product_availability told it to
    hand off to `supply`. That note ended "if this ever moves onto the front
    desk, the instruction above should change with it", which is what
    happened.

    WHY IT MOVED. The hand-off is a sub-agent call, and a sub-agent arrives
    with none of the conversation. Across one day of live calls that hop
    invented an asset id, invented an engineer id, invented a stock reference
    belonging to a different company, stalled for fifty seconds, and finally
    answered "I am not able to process that request right now" with nothing in
    the log, seconds after the customer agreed to a two thousand dollar
    freezer.

    The desk already knows the machine, the price it read out, and who it is
    speaking to. `supply` keeps what is genuinely its own: chasing a supplier
    and quoting lead times on what we do not hold.
    """
    from src import agents, supply

    say = supply.product_availability("Hoshizaki", "KM-901MAJ")["say"]
    assert "RAISE THE ORDER YOURSELF" in say
    assert "HAND OFF TO supply" not in say

    front = {getattr(t, "__name__", "") or getattr(getattr(t, "agent", None), "name", "")
             for t in agents.front_agent.tools}
    assert "create_purchase_order" in front
    assert "confirm_purchase_order" in front
    assert "supply" in front, "still there for what it is actually for"


def test_it_does_not_ask_for_permission_to_do_what_was_asked(dbfile):
    from src import supply

    say = supply.product_availability("Hoshizaki", "KM-901MAJ")["say"]
    assert "They asked to buy it" in say


def test_a_model_that_exists_nowhere_is_flagged(dbfile):
    """Offering to order something that was never made is worse than saying we
    do not carry it: the customer waits for a machine that does not exist."""
    from src import supply

    out = supply.product_availability("Nonsense", "ZZ999")
    assert out["in_certification_catalogue"] is False
    assert "read the model number back to them" in out["say"]


def test_the_desk_is_told_it_cannot_take_an_order_itself(dbfile):
    from src import agents

    r = " ".join(agents.DESK_RULES.split())
    assert "YOU CANNOT TAKE AN ORDER YOURSELF" in r
    assert "is not taking an order" in r


# A restaurant is not sold a household freezer.


@pytest.fixture
def listings(monkeypatch):
    from src import market

    box = {"rows": []}
    monkeypatch.setattr(market, "_fetch_shopping",
                        lambda q: {"shopping": box["rows"]})
    return box


def _l(title, price, source="A Shop"):
    return {"title": title, "price": price, "source": source}


def test_a_household_freezer_is_never_offered(dbfile, listings):
    from src import market

    listings["rows"] = [
        _l("Danby 8.5 cu ft Upright Freezer", "$569"),
        _l("Summit Compact Freezer", "$528"),
        _l("KoolMore Commercial Reach-In Freezer NSF", "$1,507"),
    ]
    out = market.alternatives("reach-in freezer", 2000)

    titles = [f["title"] for f in out["found"]]
    assert titles == ["KoolMore Commercial Reach-In Freezer NSF"]


def test_nothing_commercial_at_the_price_says_exactly_that(dbfile, listings):
    from src import market

    listings["rows"] = [
        _l("Danby 8.5 cu ft Upright Freezer", "$569"),
        _l("Summit Compact Freezer", "$528"),
    ]
    out = market.alternatives("reach-in freezer", 600)

    assert out["found"] == []
    assert out["domestic_skipped"] == 2
    assert "No COMMERCIAL" in out["say"]
    assert "NSF" in out["say"]
    assert "do not offer one" in out["say"]


def test_a_listing_that_says_nothing_either_way_is_not_assumed_commercial(
        dbfile, listings):
    """Most listings say neither. Assuming they are trade equipment is how a
    domestic machine reaches a kitchen."""
    from src import market

    listings["rows"] = [_l("Frigidaire 20 cu ft Freezer", "$900")]
    out = market.alternatives("reach-in freezer", 2000)
    assert out["found"] == []


def test_real_commercial_wording_is_recognised(dbfile, listings):
    from src import market

    for title in ("Atosa Undercounter Freezer",
                  "True Reach-In Freezer",
                  "Beverage-Air Merchandiser",
                  "Commercial Worktop Freezer NSF"):
        listings["rows"] = [_l(title, "$1,500")]
        out = market.alternatives("reach-in freezer", 2000)
        assert out["found"], f"{title} should count as trade equipment"
