"""The guard ran on two agents and there are eight.

`before_tool_callback=guard_tool` was set on front_agent and desk_agent, the
two the customer talks to. Between them sit six sub-agents carrying 26 tools,
and none of them ran it. Two things fell through that gap.

THE ID FILLING NEVER RAN

`next_available_slot` inside the scheduling agent got no asset_id, so it went
and asked the customer for one. On a live call the desk then re-issued the
request five times, each more detailed than the last, trying to satisfy a
sub-agent that was missing something the call already knew:

    scheduling({'request': 'availability for a technician...'})
    scheduling({'request': '...on Saturday, August 29th'})
    scheduling({'request': '...between 1pm and 4pm'})
    scheduling({'request': '...for asset AST-000B10 at The Coriander House'})

Called directly the scheduler answered instantly with a real slot: Ben Kalita,
4.1 miles, fourteen minutes' drive. The tool was never the problem.

THE OWNERSHIP CHECK NEVER RAN EITHER

Which is worse. The guard that stops one customer's machine being used on
another customer's call had a hole straight through it, reachable by any tool
a sub-agent holds.
"""

from __future__ import annotations

import pytest


ALL_AGENTS = ("history_agent", "dispatch_agent", "parts_agent",
              "scheduling_agent", "advice_agent", "supply_agent",
              "front_agent", "desk_agent")


@pytest.mark.parametrize("name", ALL_AGENTS)
def test_every_agent_with_a_tool_runs_the_guard(dbfile, name):
    from src import agents, guards

    agent = getattr(agents, name)
    # A deterministic step has no `tools` attribute at all, let alone tools to
    # guard. `dispatch` is one now: it was an LlmAgent holding find_technician
    # and forbidden from deciding anything, so the model was removed. Nothing
    # for a before_tool_callback to sit in front of.
    if not (getattr(agent, "tools", None) or []):
        pytest.skip("no tools to guard")
    assert agent.before_tool_callback is guards.guard_tool, (
        f"{name} holds {len(agent.tools)} tools and none of them are guarded")


def test_no_agent_is_added_without_one(dbfile):
    """Walks the tree rather than a list, so a sub-agent added tomorrow is
    covered by default instead of being remembered."""
    from src import agents, guards

    seen, unguarded = set(), []

    def walk(agent):
        if id(agent) in seen:
            return
        seen.add(id(agent))
        if (getattr(agent, "tools", None) or []) and \
                getattr(agent, "before_tool_callback", None) is not guards.guard_tool:
            unguarded.append(agent.name)
        for t in getattr(agent, "tools", []) or []:
            sub = getattr(t, "agent", None)
            if sub is not None:
                walk(sub)
        for sa in getattr(agent, "sub_agents", []) or []:
            walk(sa)

    walk(agents.front_agent)
    walk(agents.desk_agent)
    assert unguarded == []


# What the guard now does for a sub-agent.


class _Tool:
    def __init__(self, name):
        self.__name__ = name


class _Ctx:
    def __init__(self, intent="service"):
        self.state = {"intent": intent, "language": "", "dealer_id": "D-REF"}


@pytest.fixture
def on_a_call(dbfile):
    from src import db, trace

    with db.txn() as c:
        # The call first: the work order references it.
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-S','+13095550101','CT-1','2026-08-27T13:39:00')")
        c.execute("""INSERT INTO work_orders
                     (id,dealer_id,account_id,site_id,asset_id,contact_id,
                      reported_symptom,status,opened_at,opened_from_call)
                     VALUES ('WO-LIVE','D-REF','A-1','S-1','AS-FREEZER','CT-1',
                             'warm','open','2026-08-27T13:39:00','CALL-S')""")
    trace.call_context("CALL-S")
    yield
    trace.call_context("")


def test_a_sub_agent_tool_gets_the_machine_filled_in(on_a_call):
    """It used to ask the customer for an Asset ID instead."""
    from src import guards

    args = {"asset_id": ""}
    guards.guard_tool(_Tool("next_available_slot"), args, _Ctx())
    assert args["asset_id"] == "AS-FREEZER"


def test_a_sub_agent_cannot_reach_another_customers_machine(on_a_call):
    """The hole in the tenancy guard: reachable by any tool a sub-agent holds."""
    from src import db, guards

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,kind,name) "
                  "VALUES ('A-X','business','Somebody Else')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-X','A-X','Theirs')")
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AST-9F3B2C','S-X','True','TUC-27F','reach-in freezer')""")

    out = guards.guard_tool(_Tool("next_available_slot"),
                            {"asset_id": "AST-9F3B2C"}, _Ctx())
    assert out is not None and out["blocked"] is True


def test_the_guard_does_not_block_the_ordinary_sub_agent_work(on_a_call):
    """Too tight and the thing this product is for stops working."""
    from src import guards

    for tool in ("next_available_slot", "check_stock", "find_technician",
                 "prior_repairs", "quote_delivery", "supplier_options"):
        assert guards.guard_tool(_Tool(tool), {}, _Ctx()) is None, tool


# A value that is not an id.


def test_a_model_name_in_an_id_field_is_treated_as_missing(on_a_call):
    """On a live call the model passed asset_id="Traulsen RHT126WUT-FHS", a
    model NAME in an id field. The ownership check rejected it as "no such
    machine", correctly and uselessly, and the desk then asked the customer to
    confirm a model number they had already given. Four times. Then it asked
    whether there was a typo, which put our bug on them.

    The fix is structural rather than another instruction: the model is no
    longer asked to get this right, because a value that is not id-shaped is
    replaced with the one from the call.
    """
    from src import guards

    args = {"asset_id": "Traulsen RHT126WUT-FHS"}
    out = guards.guard_tool(_Tool("can_we_serve"), args, _Ctx())

    assert out is None, "it should proceed, not refuse"
    assert args["asset_id"] == "AS-FREEZER", "filled in from the live call"


@pytest.mark.parametrize("value,is_id", [
    ("AST-1A2B3C", True), ("WO-CF1AD3", True), ("A-A15EEC", True),
    ("Traulsen RHT126WUT-FHS", False), ("RHT126WUT-FHS", False),
    ("the big one in the back", False), ("", False),
])
def test_what_counts_as_an_id(dbfile, value, is_id):
    from src import guards

    assert guards._looks_like_an_id(value) is is_id


def test_an_id_belonging_to_somebody_else_is_still_refused(on_a_call):
    """Filling in a missing id must not become a way of laundering a real one
    that points at another customer."""
    from src import db, guards

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,kind,name) "
                  "VALUES ('A-Z','business','Someone Else')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-Z','A-Z','Theirs')")
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AST-7C4D1E','S-Z','True','TUC-27F','reach-in freezer')""")

    out = guards.guard_tool(_Tool("can_we_serve"),
                            {"asset_id": "AST-7C4D1E"}, _Ctx())
    assert out is not None and out["blocked"] is True
