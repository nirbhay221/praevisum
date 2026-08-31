"""One number, one desk, several vendors behind it.

WHAT THIS REPLACED

The front counter was split. Two numbers, two company names, and a caller who
had to know which one they needed. A refrigeration customer asking about a
laptop was told "we do not sell those" by a desk that could have served them
in one hop.

That produced three increasingly elaborate answers to a problem that only
existed because of the split: first a refusal, then another company's number
read out, then a live call transfer. None of them should have been built. The
fix was to stop splitting the front.

WHAT STAYED

Everything underneath. Each vendor keeps its own stock, technicians, rates,
warranty terms and repair corpus, and every query downstream is still scoped
to exactly one of them. dealer_id is decided in three places and used in two
hundred; only the deciding changed.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, dealer_id="D-REF"):
        self.state = {"dealer_id": dealer_id, "caller": {}}


class _ToolCtx:
    def __init__(self, dealer_id="D-REF"):
        self.state = {"dealer_id": dealer_id, "intent": "service"}


# Routing.


# Only families the fixture actually gives its two vendors. Asking for one it
# does not carry tests the fixture, not the routing.
@pytest.mark.parametrize("asked,expected", [
    ("laptop", "D-IT"),
    ("a printer", "D-IT"),
    ("ups", "D-IT"),
    ("walk-in cooler", "D-REF"),
    ("my reach-in freezer", "D-REF"),
])
def test_the_desk_finds_the_right_vendor(dbfile, asked, expected):
    from src import desk

    ctx = _ToolCtx("D-REF")
    out = desk.route_to_vendor(asked, ctx)

    assert out["ok"] is True
    assert ctx.state["dealer_id"] == expected


def test_routing_changes_mid_call(dbfile):
    """Somebody can ring about a freezer and buy a laptop on the same call,
    and both are ordinary."""
    from src import desk

    ctx = _ToolCtx("D-REF")
    desk.route_to_vendor("walk-in cooler", ctx)
    assert ctx.state["dealer_id"] == "D-REF"

    desk.route_to_vendor("laptop", ctx)
    assert ctx.state["dealer_id"] == "D-IT"


def test_something_nobody_covers_is_said_plainly(dbfile):
    from src import desk

    out = desk.route_to_vendor("a lawnmower", _ToolCtx())
    assert out["ok"] is False
    assert "do not guess at a company" in out["say"]


def test_the_caller_is_never_told_about_the_routing(dbfile):
    """They rang one number and are talking to one desk. Hearing about our
    internal arrangements is no better than hearing "we do not do that here"."""
    from src import desk

    out = desk.route_to_vendor("laptop", _ToolCtx())
    assert "Say nothing about the supplier" in out["say"]


def test_a_successful_route_says_we_carry_it(dbfile):
    """The reply used to say "routed, carry on", which the model read as
    permission to carry on refusing. On a live call it routed a laptop to the
    IT vendor and then told the caller we do not carry laptops, contradicting
    the tool that had just succeeded. The tool now says what is true.
    """
    from src import desk

    out = desk.route_to_vendor("laptop", _ToolCtx())
    assert "WE CARRY laptop" in out["say"]
    assert "Do NOT tell them we do not sell or service it" in out["say"]


# The front.


def test_the_greeting_names_the_desk_not_a_vendor(dbfile):
    from src.config import settings
    from src import agents, db

    with db.connect() as c:
        vendors = [(r["greeting_name"] or r["name"])
                   for r in c.execute("SELECT name, greeting_name FROM dealers")]

    out = agents.front_agent.instruction(_Ctx("D-IT"))
    assert settings.front_name in out
    for v in vendors:
        assert v not in out


def test_one_desk_lists_every_trade_it_covers(dbfile):
    from src import agents

    out = agents.front_agent.instruction(_Ctx("D-REF"))
    for family in ("reach-in freezer", "ice machine", "laptop", "printer"):
        assert family in out


def test_the_hand_off_and_the_transfer_are_gone(dbfile):
    """Both were elaborate answers to a problem created by splitting the front
    counter. Neither should be reachable."""
    from src import agents, desk

    names = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "another_business_handles_it" not in names
    assert "transfer_live_call" not in names
    assert not hasattr(desk, "another_business_handles_it")


def test_route_to_vendor_is_on_the_desk(dbfile):
    from src import agents

    names = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "route_to_vendor" in names


# What must not have changed.


def test_the_vendors_data_still_never_mixes(dbfile):
    """The whole point of routing is that exactly one vendor's data applies."""
    from scripts.load_trade_rates import load

    from src import db, pricing

    load()
    ref = pricing.labour_rate("D-REF")
    it = pricing.labour_rate("D-IT")
    assert ref["rate"] != it["rate"]

    with db.connect() as c:
        fridge = c.execute(
            "SELECT COUNT(*) n FROM parts WHERE dealer_id='D-REF'").fetchone()["n"]
        computers = c.execute(
            "SELECT COUNT(*) n FROM parts WHERE dealer_id='D-IT'").fetchone()["n"]
    assert fridge and computers
