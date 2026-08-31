"""The routed company reaching a sub-agent, which it did not.

WHAT HAPPENED ON A LIVE CALL

The caller asked for a laptop. `route_to_vendor` fired and wrote dealer_id=D-IT
into session state. `options_under` read it and correctly offered a Lenovo
IdeaPad at $364.99. The caller said yes.

`supply` then answered "we do not carry or sell the Lenovo IdeaPad" three
times. Twelve were in stock.

WHY

A sub-agent is invoked with its own context and never sees a write the front
agent made to its session state. So every tool inside `supply` fell back to
the default company and asked the REFRIGERATION business whether it sold
laptops. It correctly said no.

This is not companies leaking into each other. It is the opposite: the wrong
company being asked. tenancy.py was written about this exact failure and says
so in its own docstring, about a printer.

TWO PLACES HAD TO CHANGE

`tools._dealer`, which reads session state, and `tenancy.the_desk`, which is
what every service function calls when no dealer is passed. Fixing only the
first left `product_availability` still answering about the wrong company,
because it takes its own dealer_id parameter and resolves it through the
second.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_routing():
    from src.tenancy import routed_to
    routed_to("")
    yield
    routed_to("")


@pytest.fixture
def two_companies(dbfile):
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,list_price,lead_time_days) "
                  "VALUES ('D-IT','Lenovo','IdeaPad 15','laptop',12,364.99,3)")
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,list_price,lead_time_days) "
                  "VALUES ('D-REF','Beverage-Air','HR1HC','display cooler',"
                  "1,5112.77,21)")


def test_a_tool_with_no_context_follows_the_routing(dbfile):
    """A sub-agent tool gets no session state at all."""
    from src.tenancy import routed_to
    from src.tools import _dealer

    routed_to("D-IT")
    assert _dealer(None) == "D-IT"


def test_the_service_layer_follows_it_too(two_companies):
    """The half that was missed. product_availability takes its own dealer_id
    and resolves it through the_desk, so fixing _dealer alone left it still
    answering about the wrong company."""
    from src.supply import product_availability
    from src.tenancy import routed_to

    routed_to("D-IT")
    assert product_availability("Lenovo", "IdeaPad 15")["on_hand"] == 12


def test_the_laptop_call_that_failed(two_companies):
    """End to end: routed to IT, the laptop is found."""
    from src.supply import product_availability
    from src.tenancy import routed_to

    routed_to("D-IT")
    out = product_availability("Lenovo", "IdeaPad 15")
    assert out["on_hand"] == 12
    assert "12 in stock" in out.get("say", "")


def test_the_wrong_company_still_says_it_is_not_theirs(two_companies):
    """Following the routing must not become searching everywhere. Each
    company keeps its own book."""
    from src.supply import product_availability
    from src.tenancy import routed_to

    routed_to("D-REF")
    assert not product_availability("Lenovo", "IdeaPad 15").get("on_hand")

    routed_to("D-IT")
    assert not product_availability("Beverage-Air", "HR1HC").get("on_hand")


def test_an_explicit_company_beats_the_routing(two_companies):
    """Something that names a company meant it."""
    from src.supply import product_availability
    from src.tenancy import routed_to, the_desk

    routed_to("D-IT")
    assert the_desk("D-FURN") == "D-FURN"
    assert not product_availability("Lenovo", "IdeaPad 15", "D-REF").get("on_hand")


def test_nothing_routed_falls_back_as_before(two_companies):
    """A console page or a nightly job has no call to read a company from and
    is entitled to a sensible default."""
    from src.tenancy import the_desk

    assert the_desk() == "D-REF"
    assert the_desk("D-IT") == "D-IT"


def test_routing_does_not_leak_between_companies(dbfile):
    """The check that matters: following a route must never widen what one
    company can see."""
    from src import db
    from src.shopfloor import whats_on_the_floor
    from src.tenancy import routed_to

    with db.txn() as c:
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,list_price,lead_time_days) "
                  "VALUES ('D-IT','Lenovo','L1','laptop',5,300.0,3)")
        c.execute("INSERT INTO product_stock (dealer_id,manufacturer,"
                  "model_number,family,on_hand,list_price,lead_time_days) "
                  "VALUES ('D-REF','Bev','B1','display cooler',5,300.0,3)")

    routed_to("D-IT")
    shown = {p.get("model") for p in whats_on_the_floor("D-IT", "", 50)["products"]}
    assert "L1" in shown
    assert "B1" not in shown


def test_a_call_states_its_vendor_rather_than_inheriting_one(dbfile):
    """THE BUG THE SUITE FOUND. The routed vendor is a ContextVar so it can
    reach a sub-agent, and a ContextVar that is only ever SET can be
    inherited: a task created while another call had routed itself to the IT
    company starts life believing it is the IT company.

    In the suite that showed up as one test routing and four unrelated offer
    tests failing afterwards, passing alone. On a live line it would be one
    caller answered about another caller's business.

    So the bridge states the vendor when the line opens. The dialled number
    decides; route_to_vendor may change it after."""
    from src.telephony.twilio_bridge import _start_this_call_on
    from src.tenancy import routed, routed_to

    routed_to("D-IT")
    assert routed() == "D-IT"

    # a new call arrives on the refrigeration number
    _start_this_call_on("D-REF")
    assert routed() == "D-REF", "the new call inherited the old one's vendor"

    # and a call with no dealer starts clean rather than keeping the last one
    _start_this_call_on("")
    assert routed() == ""
