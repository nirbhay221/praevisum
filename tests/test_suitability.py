"""The question a trade note only asked for nicely.

WHAT THIS ENFORCES

Each vendor carries its own trade knowledge in its instruction. The furniture
one says the single question deciding whether a recommendation is honest is how
many hours a day a chair will be sat in, and by how many people. It says it
well and it enforces nothing, so the desk could quote a task chair to a 24 hour
dispatch office, never ask, and nothing would notice until the chair failed
with its warranty void for exceeding a duty rating nobody checked.

Same for a consumer television, whose warranty EXCLUDES commercial and public
display use. Mounted in a dining room it is uncovered from the day it goes up.

WHY A GUARD RATHER THAN A BETTER PROMPT, OR A GATEKEEPER AGENT

"Before the Tool Call: Deterministic Pre-Action Authorization for Autonomous AI
Agents" (arXiv 2603.20953) measures social engineering succeeding 74.6% of the
time under permissive policies when enforcement rests on model judgement, and
gives the reason: alignment "shifts behavior across the distribution but does
not guarantee any individual output". An agent per company would be talked past
exactly as a prompt is, because it is the same substrate. The paper's six
principles are deterministic, bypass-resistant, auditable, fail-closed,
framework-agnostic and implementer-independent, and its architecture is a
before-tool hook, which is what guards.py already is.

THE TWO FAILURES THAT ONLY TESTING FOUND

Both are here as tests because both were live in the first working version.

A REPLACEMENT GAS LIFT resolved to "office chair", because `parts.families`
records what a part FITS. So somebody buying a spare cylinder for a chair they
already own was asked how many hours a day they sit in it.

A REAL CHAIR ON THE SHELF resolved to nothing, because the catalogue match was
an equality test and the shelf reads "WorkPro 1000 Series Ergonomic Mesh
Mid-Back" while a caller says "the WorkPro 1000". That one is the more
dangerous of the two: the gate silently never fires.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, **state):
        self.state = dict(state)


def _tool(name):
    def f():
        pass
    f.__name__ = name
    return f


@pytest.fixture
def a_furniture_book(dbfile):
    """A chair on the shelf and a chair part in the bin, for one dealer."""
    from src import db

    with db.txn() as c:
        c.execute(
            """INSERT OR REPLACE INTO dealers (id,name,trade,phone_e164,families)
               VALUES ('D-FURN','Prairie Contract Furnishings','furniture',
                       '+13095550100','office chair,desk')""")
        c.execute(
            """INSERT INTO product_stock
                 (dealer_id,manufacturer,model_number,family,list_price,
                  on_hand,on_order)
               VALUES ('D-FURN','WorkPro',
                       '1000 Series Ergonomic Mesh Mid-Back','office chair',
                       199.99,4,0)""")
        c.execute(
            """INSERT INTO parts (sku,name,unit_cost,lead_time_days,dealer_id,
                                  families)
               VALUES ('FURN-GASLIFT','Gas lift cylinder',38.50,2,'D-FURN',
                       'office chair')""")


# --------------------------------------------------------------------------
# resolving a line item to a family
# --------------------------------------------------------------------------

def test_a_chair_on_the_shelf_is_recognised_from_a_partial_name(a_furniture_book):
    """The dangerous failure. An equality test found nothing, so the gate
    silently never fired for the exact product it exists to cover."""
    from src import suitability

    assert suitability.families_in(["WorkPro 1000 Series Ergonomic Mesh"],
                                   "D-FURN") == ["office chair"]


def test_a_replacement_part_is_not_the_product(a_furniture_book):
    """`parts.families` records what a part FITS, not what the line is. Read
    the other way, a spare gas lift became a chair purchase and the customer
    was asked how many hours a day they sit in a cylinder."""
    from src import suitability

    assert suitability.families_in(["FURN-GASLIFT"], "D-FURN") == []


def test_a_word_that_merely_contains_the_family_does_not_count(a_furniture_book):
    from src import suitability

    assert suitability.families_in(["a chairman portrait"], "D-FURN") == []


def test_plain_words_still_resolve(a_furniture_book):
    """People say "an office chair", not a model number."""
    from src import suitability

    assert suitability.families_in(["office chair"], "D-FURN") == ["office chair"]


# --------------------------------------------------------------------------
# the gate itself
# --------------------------------------------------------------------------

def test_quoting_a_chair_is_refused_until_the_question_is_asked(a_furniture_book):
    from src import guards

    out = guards.guard_tool(
        _tool("create_purchase_order"),
        {"account_id": "A-1", "items": ["office chair"]},
        _Ctx(dealer_id="D-FURN", intent="order"))

    assert out is not None
    assert out["blocked"] is True
    assert "hours a day" in out["why"]
    assert "note_how_it_will_be_used" in out["do_this"]


def test_the_refusal_says_why_it_matters_not_that_a_rule_exists(a_furniture_book):
    """A refusal the model cannot act on is just an obstacle. This one carries
    the trade reason, so the desk can ask the customer a sensible question."""
    from src import guards

    out = guards.guard_tool(
        _tool("create_purchase_order"),
        {"account_id": "A-1", "items": ["office chair"]},
        _Ctx(dealer_id="D-FURN", intent="order"))

    assert "duty rating" in out["why"]
    assert "warranty" in out["why"]


def test_answering_the_question_lets_the_order_through(a_furniture_book):
    from src import guards, tools

    ctx = _Ctx(dealer_id="D-FURN", intent="order")
    args = {"account_id": "A-1", "items": ["office chair"]}

    assert guards.guard_tool(_tool("create_purchase_order"), dict(args), ctx)

    tools.note_how_it_will_be_used("office chair", hours_per_day=9,
                                   people_sharing=3, tool_context=ctx)

    assert guards.guard_tool(_tool("create_purchase_order"), dict(args),
                             ctx) is None


def test_a_part_order_is_never_gated(a_furniture_book):
    """Somebody replacing a gas lift on a chair they already own is not making
    a purchasing decision this rule is about."""
    from src import guards

    assert guards.guard_tool(
        _tool("create_purchase_order"),
        {"account_id": "A-1", "items": ["FURN-GASLIFT"]},
        _Ctx(dealer_id="D-FURN", intent="order")) is None


def test_a_television_is_gated_on_a_different_question(dbfile):
    """A screen's question is where it is going, because a consumer warranty
    excludes commercial and public display use. Answering the chair question
    must not satisfy it."""
    from src import guards, tools

    ctx = _Ctx(dealer_id="D-AV", intent="order")
    tools.note_how_it_will_be_used("office chair", hours_per_day=9,
                                   tool_context=ctx)

    out = guards.guard_tool(_tool("create_purchase_order"),
                            {"account_id": "A-1", "items": ["television"]}, ctx)

    assert out is not None
    assert "where it is going" in out["why"]


def test_looking_things_up_is_never_gated(a_furniture_book):
    """The opening principle of guards.py. A caller must be able to browse
    chairs before anybody asks how they will sit in one."""
    from src import guards

    for name in ("lookup_product", "options_under", "product_availability"):
        assert guards.guard_tool(
            _tool(name), {"query": "office chair"},
            _Ctx(dealer_id="D-FURN", intent="product")) is None


def test_other_trades_are_untouched(dbfile):
    """Refrigeration has no such question, so nothing changes for it."""
    from src import guards

    assert guards.guard_tool(
        _tool("create_purchase_order"),
        {"account_id": "A-1", "items": ["Door gasket"]},
        _Ctx(dealer_id="D-REF", intent="order")) is None


# --------------------------------------------------------------------------
# how it behaves when it cannot do its job
# --------------------------------------------------------------------------

def test_it_fails_open_rather_than_breaking_a_call(a_furniture_book,
                                                   monkeypatch):
    """This gate protects the QUALITY of a recommendation. The ownership and
    intent gates protect other people's data and fail closed. It would be
    wrong for the softer rule to be the one that can break a call, so an
    internal error here lets the order proceed."""
    from src import guards, suitability

    def broken(*a, **k):
        raise RuntimeError("catalogue unavailable")

    monkeypatch.setattr(suitability, "unanswered_for", broken)

    assert guards.guard_tool(
        _tool("create_purchase_order"),
        {"account_id": "A-1", "items": ["office chair"]},
        _Ctx(dealer_id="D-FURN", intent="order")) is None


def test_the_refusal_is_recorded(a_furniture_book):
    """Auditable is one of the paper's six principles, and the interventions
    table is where this project keeps that promise."""
    from src import guards

    guards.guard_tool(
        _tool("create_purchase_order"),
        {"account_id": "A-1", "items": ["office chair"]},
        _Ctx(dealer_id="D-FURN", intent="order"))

    out = guards.what_the_guards_did()
    assert any(k["kind"] == "unasked_fitness" for k in out["by_kind"])


def test_the_policy_is_readable_separately_from_the_enforcement(dbfile):
    """The paper separates the declarative Policy Pack from the Authorization
    Engine. Adding a fifth trade should be a dictionary entry, not a change to
    the callback."""
    from src import suitability

    assert "office chair" in suitability.REQUIRED
    assert "television" in suitability.REQUIRED
    for name, q in suitability.REQUIRED.items():
        assert q.ask and q.why and q.fields and q.refusing


def test_an_incomplete_answer_is_not_an_answer(dbfile):
    """Recording the tool call must not be mistaken for recording the fact."""
    from src import tools

    ctx = _Ctx(dealer_id="D-FURN")
    out = tools.note_how_it_will_be_used("office chair", tool_context=ctx)

    assert out["ok"] is False
    assert "hours_per_day" in out["still_needed"]
