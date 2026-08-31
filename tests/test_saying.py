"""The guard on what the desk SAYS, not on what it does.

THE HOLE, FOUND ON A LIVE WHATSAPP CONVERSATION

guards.py intercepts tool calls. Every rule in it is a lock on a door the model
can decline to walk through, and if it never calls a tool there is nothing to
intercept. On 30 August, on a real channel, to the owner's own phone:

    customer: What about the Traulsen G12010, is that one covered?
    desk:     6-year parts and labor, 7 on the compressor      <- CORRECT

    customer: how much is a door gasket
    desk:     The door gasket for the Traulsen G12010 is $84.64   <- INVENTED

Zero tool calls recorded for that exchange. Zero guard interventions. The
catalogue holds one gasket, at $92.00, and $84.64 is in no table anywhere.

Asked the same question with no prior context, the same code answers "$92.00,
10 in stock, and there is a 15% offer" having called the tools. The difference
was the previous turn: a Traulsen had been mentioned, so the model produced a
Traulsen-SPECIFIC price rather than the generic one it could have looked up. It
preferred being specific to being right.

WHAT IS TESTED

That a money amount cannot leave without a tool that returns money having run,
that ordinary sentences are not blocked, and that the guard fails OPEN. The
last one is deliberate and differs from the ownership gate: the cost of this
guard being wrong is a checked price that did not go out, while the cost of the
ownership gate being wrong is another customer's data, so they fail in opposite
directions on purpose.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# what counts as a price
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("The door gasket for the Traulsen G12010 is $84.64", ["$84.64"]),
    ("A gasket is USD 92.00", ["USD 92.00"]),
    ("that is 92 dollars", ["92 dollars"]),
    ("$1,649 for the unit", ["$1,649"]),
    ("free first-year labour on PM contracts", []),
    ("I will check and come back", []),
    ("model number HL-L2400DW", []),
])
def test_money_is_recognised_in_the_shapes_a_model_writes_it(dbfile, text,
                                                             expected):
    """Not just "$12.34". A model writes prices several ways and a guard that
    only catches one of them is a guard with three holes in it."""
    from src.saying import money_in

    assert money_in(text) == expected


# --------------------------------------------------------------------------
# the rule
# --------------------------------------------------------------------------

def test_the_exact_live_failure_is_blocked(dbfile):
    """The sentence that went to a real customer."""
    from src.saying import check_reply

    out = check_reply(
        "The door gasket for the Traulsen G12010 is $84.64, and we can have "
        "it delivered by Tuesday.", set())

    assert out is not None
    assert out["blocked"] is True
    assert "$84.64" in out["amounts"]
    assert "came from you rather than from our records" in out["why"]
    assert "lookup_product" in out["do_this"]


def test_a_price_that_came_from_a_tool_goes_out(dbfile):
    """The guard must not block honest answers, which is the failure that
    would actually break the product."""
    from src.saying import check_reply

    assert check_reply("The door gasket is $92.00 and we have 10 in stock.",
                       {"lookup_product"}) is None


def test_a_non_pricing_tool_does_not_license_a_price(dbfile):
    """Calling set_intent does not make a number true."""
    from src.saying import check_reply

    out = check_reply("That comes to USD 1,649.", {"set_intent", "load_memory"})
    assert out is not None


@pytest.mark.parametrize("tool", [
    "lookup_product", "price_for", "quote_visit", "quote_delivery",
    "offer_on_this", "check_stock", "supply", "advice",
])
def test_every_tool_that_returns_money_licenses_a_price(dbfile, tool):
    """Including the AgentTool workers. `supply` and `advice` quote through
    the desk, and leaving them out would block correct answers."""
    from src.saying import check_reply

    assert check_reply("It is $140.00.", {tool}) is None


def test_an_ordinary_sentence_is_never_touched(dbfile):
    from src.saying import check_reply

    for text in ("What is the model number?",
                 "I can book Thursday morning.",
                 "Free first-year labour applies on that contract."):
        assert check_reply(text, set()) is None


def test_the_condition_that_was_always_true_stays_gone(dbfile):
    """A REGRESSION TEST FOR MY OWN BUG.

    The first version contained:

        if all(w in low for w in ()) or ...

    `all()` over an EMPTY tuple is True, so the guard returned None on every
    path and blocked nothing whatsoever. It read as careful and did nothing,
    which is the worst shape a guard can have, and it died on the first test
    rather than in production.
    """
    import inspect

    from src import saying

    src = inspect.getsource(saying.check_reply)
    assert "for w in ()" not in src

    # And the behaviour, not just the absence of the line.
    assert saying.check_reply("It is $50.", set()) is not None


# --------------------------------------------------------------------------
# how it behaves when it cannot do its job
# --------------------------------------------------------------------------

def test_it_fails_open(dbfile, monkeypatch):
    """Deliberately the opposite of the ownership gate. That one protects
    another customer's data and fails closed; this one protects the accuracy
    of a number, and a crash here must not take a live call down."""
    from src import saying

    class Boom:
        @property
        def content(self):
            raise RuntimeError("nothing works")

    assert saying.guard_saying(object(), Boom()) is None


def test_a_turn_that_is_only_a_tool_call_is_left_alone(dbfile):
    """Mid-turn the model emits a function call with no prose. There is
    nothing to check and returning a refusal there would break the turn."""
    from types import SimpleNamespace

    from src import saying

    part = SimpleNamespace(text=None, function_call=SimpleNamespace(name="x"))
    resp = SimpleNamespace(content=SimpleNamespace(parts=[part]))

    assert saying.guard_saying(object(), resp) is None


def test_tools_used_this_turn_are_read_from_state(dbfile):
    """guards.guard_tool writes them there, because every tool call already
    passes through it and nothing else has to know this guard exists."""
    from types import SimpleNamespace

    from src import saying

    ctx = SimpleNamespace(state={"tools_this_turn": ["lookup_product"]})
    assert saying._tools_used(ctx) == {"lookup_product"}

    assert saying._tools_used(SimpleNamespace(state={})) == set()
    assert saying._tools_used(object()) == set()
