"""No tool may quietly assume it is talking to one particular vendor.

THE BUG THIS MAKES IMPOSSIBLE

Four separate times in one day, the same mistake, found one call site at a
time by ringing the phone:

    options_under(budget, family, dealer_id="D-REF")
    source_order(purchase_order_id, dealer_id="D-REF")
    _on_the_floor(description, dealer_id)          scoped to one shelf
    can_we_serve(asset_id, dealer_id="D-REF")      and eleven more like it

Every one is a multi-tenant system with one tenant's name written in as a
default argument. Every one was invisible for as long as that tenant happened
to hold everything, and every one produced a confidently wrong answer the
moment routing started working:

    "I'm not finding any ASUS laptops in our system at the moment."
        four of them on the shelf, filed under the other vendor

    "We don't have the Brother HL-L2400D in stock right now, but we can order
     it in. It typically takes about 21 days."
        thirteen of them, on the IT shelf, while it asked refrigeration

    should_send_someone -> can_we_serve -> raise_it
        a freezer escalated to a human callback on Saturday morning because
        three tools in a row asked the wrong vendor whether anybody was
        qualified

WHY A TEST AND NOT A FIX

Because the fix is done and it will rot. Twelve tools were repaired by making
the guard fill the vendor from session state, and nothing stops a thirteenth
being written next week with the same default, by somebody reasonably copying
the shape of the tool next to it.

This test fails when that happens. It is deliberately a rule about the SHAPE
of the code rather than about any one function, because that is the level the
mistake lives at.
"""

from __future__ import annotations

import inspect

import pytest

# The one place a vendor id may legitimately be hard-coded: a fallback for a
# channel with no caller to read one from. Anything here must say why.
ALLOWED = {
    # (module, function): why
}


def _agent_tools():
    """Every callable reachable from a live call, deduplicated."""
    from src import agents

    seen = {}
    for a in (agents.front_agent, agents.desk_agent, agents.history_agent,
              agents.dispatch_agent, agents.parts_agent,
              agents.scheduling_agent, agents.advice_agent,
              agents.supply_agent):
        for t in getattr(a, "tools", []) or []:
            fn = getattr(t, "func", None) or getattr(t, "_func", None) or t
            name = getattr(fn, "__name__", None)
            if name and callable(fn):
                seen[name] = fn
    return seen


def test_no_tool_defaults_to_one_particular_vendor(dbfile):
    """A default naming one tenant is a wrong answer waiting for the data to
    be right."""
    offenders = []
    for name, fn in sorted(_agent_tools().items()):
        if (fn.__module__, name) in ALLOWED:
            continue
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            continue
        for pname, p in params.items():
            if "dealer" not in pname:
                continue
            if isinstance(p.default, str) and p.default.startswith("D-"):
                offenders.append(f"{name}({pname}={p.default!r})")

    assert not offenders, (
        "these tools assume a vendor instead of reading the routed one:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe guard fills dealer_id from session state before every tool. "
          "Take the default off, or add it to ALLOWED with a reason.")


def test_the_guard_fills_the_routed_vendor(dbfile):
    """The mechanism the test above relies on."""
    from src import cover, guards

    class Ctx:
        def __init__(self, d):
            self.state = {"dealer_id": d, "intent": "service"}

    args = {"asset_id": "AST-X"}
    guards.guard_tool(cover.can_we_serve, args, Ctx("D-IT"))
    assert args.get("dealer_id") == "D-IT"


def test_a_vendor_named_outright_is_left_alone(dbfile):
    """Something meant it. The guard fills what is absent, never overrides."""
    from src import cover, guards

    class Ctx:
        state = {"dealer_id": "D-REF", "intent": "service"}

    args = {"asset_id": "AST-X", "dealer_id": "D-IT"}
    guards.guard_tool(cover.can_we_serve, args, Ctx())
    assert args["dealer_id"] == "D-IT"


def test_nothing_is_filled_when_nothing_was_routed(dbfile):
    from src import cover, guards

    class Ctx:
        state = {"intent": "service"}

    args = {"asset_id": "AST-X"}
    guards.guard_tool(cover.can_we_serve, args, Ctx())
    assert "dealer_id" not in args


# The other half of the same mistake: asking one shelf when the desk has four.


def test_stock_questions_ask_the_whole_desk(dbfile):
    """"Do you have one" is a question about the counter, not about whichever
    vendor happens to be loaded. It said no to a printer we had thirteen of."""
    from src import backorder, db

    with db.txn() as c:
        c.execute("""INSERT INTO product_stock
                     (dealer_id,manufacturer,model_number,family,on_hand,list_price)
                     VALUES ('D-IT','Brother','HL-TEST','printer',13,188.0)""")

    assert backorder._on_the_floor("Brother HL-TEST printer") == 13
    assert backorder._on_the_floor("Brother HL-TEST printer", "D-REF") == 0


def test_every_vendor_has_somebody_who_can_attend(dbfile):
    """Two vendors were added with stock, warranty terms and a wage, and no
    technicians at all. They could sell and not serve."""
    from src import db

    with db.connect() as c:
        dealers = [r["id"] for r in c.execute("SELECT id FROM dealers")]
        staffed = {r["dealer_id"] for r in c.execute(
            "SELECT DISTINCT dealer_id FROM technicians WHERE active=1")}

    missing = [d for d in dealers if d not in staffed]
    assert not missing, f"these vendors have no active technicians: {missing}"
