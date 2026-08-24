"""What the owner may do, and what the phone agent may never do.

The division is enforced in code rather than in a prompt. A discount can only
come into existence through the console, where the person who owns the
business is looking at a screen. The phone agent can read a live offer and
mention it; it cannot invent one to close a call, however reasonable that
would sound in the moment.

The date test is here because it caught a real one: the console model, given
"September 30" with no year, chose 2024 and tried to create an offer that had
already expired.
"""

from __future__ import annotations

from datetime import date, timedelta

from conftest import IT, REF


def test_an_offer_needs_an_end_date(dbfile):
    """An offer with no end runs forever, which is a discount nobody approved."""
    from src import console

    r = console.create_promotion(REF, "10% off defrost parts", "")
    assert not r.get("ok")


def test_an_offer_cannot_end_in_the_past(dbfile):
    """The guard that caught the model guessing 2024."""
    from src import console

    r = console.create_promotion(REF, "10% off defrost parts", "2024-09-30")
    assert not r.get("ok")


def test_a_real_offer_is_created_and_readable(dbfile):
    from src import console

    ends = (date.today() + timedelta(days=14)).isoformat()
    r = console.create_promotion(REF, "10% off defrost parts", ends,
                                 applies_to=["P-DEFROSTTHE"])
    assert r.get("ok"), r

    live = console.snapshot(REF)["promotions"]
    assert any(p["headline"] == "10% off defrost parts" for p in live)


def test_offers_do_not_cross_dealers(dbfile):
    """One dealer's discount is not another dealer's discount."""
    from src import console

    ends = (date.today() + timedelta(days=14)).isoformat()
    console.create_promotion(REF, "10% off defrost parts", ends)

    assert console.snapshot(IT)["promotions"] == []


def test_the_phone_agent_cannot_create_an_offer():
    """Not a matter of instruction. The tool is not on the agent.

    A prompt saying "never invent a discount" is a request. An absent function
    is a guarantee, and this asserts the guarantee rather than the request.
    """
    from src import agents

    names = set()
    for agent in [agents.front_agent]:
        for t in agent.tools:
            names.add(getattr(t, "__name__", getattr(t, "name", "")))

    for forbidden in ("create_promotion", "start_offer", "set_price",
                      "change_price", "upsert_part", "add_or_update_part"):
        assert forbidden not in names, \
            f"the phone agent can call {forbidden}"


def test_the_phone_agent_can_read_offers():
    """Reading a live offer is the whole point of having created one."""
    from src import agents

    names = {getattr(t, "__name__", getattr(t, "name", ""))
             for t in agents.front_agent.tools}
    assert "current_deals" in names


def test_ending_an_offer_stops_it_being_quoted(dbfile):
    """Stopping an offer has to stop it immediately, not at midnight.

    The bug this caught: end_promotion set the end date to today, and every
    reader asks for offers whose end date is today or later. So the owner
    stopped an offer and the phone desk went on quoting it for the rest of the
    day, which is a discount nobody was still authorising.
    """
    from src import console, tools

    class Ctx:
        def __init__(self):
            self.state = {"dealer_id": REF}

    ends = (date.today() + timedelta(days=14)).isoformat()
    pid = console.create_promotion(
        REF, "10% off defrost parts", ends,
        applies_to=["P-DEFROSTTHE"])["promotion_id"]

    # live to begin with, on both sides
    assert any(p["id"] == pid for p in console.snapshot(REF)["promotions"])
    assert tools.current_deals("", Ctx()).get("deals")

    console.end_promotion(REF, pid)

    assert all(p["id"] != pid for p in console.snapshot(REF)["promotions"])
    quoted = tools.current_deals("", Ctx()).get("deals") or []
    assert all(o.get("headline") != "10% off defrost parts" for o in quoted), \
        "the phone desk is still quoting an offer the owner stopped"
