"""Call every tool on the desk and see which ones blow up.

WHY THIS IS WORTH ITS RUNTIME

Most of this suite tests behaviour: given this input, say that. This one tests
something dumber and, on the evidence, more useful: that a tool the model can
choose actually runs at all.

Two of the three bugs the first real phone calls found were of that shape.
`confirm_details` and `register_asset` were written, documented and tested,
and wired to no agent. `product_availability` was written specifically to
answer "have you got one in the building", and was wired to nothing while the
table behind it held zero rows. Nothing failed. Nothing was red. The tools
simply were not reachable, and the only way anyone found out was by ringing
the number and listening.

A test that walks the actual agent tool lists cannot be fooled by that: if a
tool is not on an agent it is not walked, and if it is on an agent it has to
survive being called.

WHAT IT DELIBERATELY DOES NOT DO

It does not check the answers. Every other file does that. This one only
insists that nothing raises, because an exception inside a tool during a live
call is invisible: ADK hands the model an error, the model apologises, and
the caller hears a desk that has gone vague rather than one that has crashed.
"""

from __future__ import annotations

import inspect

import pytest


class _Ctx:
    """Enough of an ADK ToolContext for tools that take one."""

    state = {"dealer_id": "D-REF", "intent": "service", "language": ""}

    def __init__(self):
        self.actions = type("A", (), {"escalate": False})()


def _wired_tools():
    """Every plain function reachable from the phone desk or the message desk.

    Walks tools AND sub_agents, because the assessment stage is a
    SequentialAgent whose children carry their own tools and an earlier
    version of this walk missed them entirely.
    """
    from src import agents

    found: dict = {}

    def collect(agent):
        for t in getattr(agent, "tools", []) or []:
            if inspect.isfunction(t):
                found[t.__name__] = t
            sub = getattr(t, "agent", None)
            if sub is not None:
                collect(sub)
        for sa in getattr(agent, "sub_agents", []) or []:
            collect(sa)

    collect(agents.front_agent)
    collect(agents.desk_agent)
    return found


def _sample(dbfile):
    """Real ids out of the fixture, so tools are called with things that exist."""
    from src import db

    with db.connect() as c:
        asset = c.execute("SELECT id FROM assets WHERE family IS NOT NULL "
                          "LIMIT 1").fetchone()["id"]
        site = c.execute("SELECT site_id FROM assets WHERE id=?",
                         (asset,)).fetchone()["site_id"]
        account = c.execute("SELECT account_id FROM sites WHERE id=?",
                            (site,)).fetchone()["account_id"]
        sku = c.execute("SELECT sku FROM parts LIMIT 1").fetchone()["sku"]
        tech = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()["id"]

    return {
        "asset_id": asset, "site_id": site, "account_id": account,
        "sku": sku, "skus": [sku], "sku_or_model": sku, "parts": [sku],
        "technician_id": tech, "dealer_id": "D-REF",
        "brand": "Traulsen", "brand_hint": "Traulsen",
        "manufacturer": "Traulsen", "model_number": "G12010",
        "spoken": "Traulsen G12010", "symptom": "not holding temp overnight",
        "reported_symptom": "not holding temp overnight",
        "family": "reach-in freezer", "query": "freezer not holding temp",
        "intent": "service", "code": "es", "about": "", "text": "hello",
        "name": "Test Person", "what": "it is too loud", "kind": "part",
        "qty": 1, "days": 5, "from_min": 540, "to_min": 660,
        "when": "2026-08-27T10:00:00", "note": "", "reason": "test",
        "detail": "test", "request": "earliest possible",
        "company": "Acme", "contact": "Bob 555", "offering": "parts",
        "item": "defrost thermostat", "channel": "whatsapp",
        "reference": "MM-1",
        # ids that deliberately do not exist: a tool handed a stranger's
        # reference must answer, not explode.
        "purchase_order_id": "PO-NONE", "fix_id": "RF-NONE",
        "claim_id": "WC-NONE", "work_order_id": "WO-NONE",
        "branch_id": "B-NONE",
        "tool_context": _Ctx(),
    }


def test_the_desk_has_the_tools_it_is_supposed_to(dbfile):
    """A cheap guard against the exact bug that shipped twice: a tool written,
    documented, tested and reachable by nobody."""
    names = set(_wired_tools())

    for must in ("confirm_details", "register_asset", "quote_visit",
                 "can_we_serve", "raise_it", "product_availability",
                 "where_to_send_proof", "current_deals"):
        assert must in names, f"{must} is on no agent, so the model cannot call it"


def test_every_tool_on_the_desk_survives_being_called(dbfile, corpus):
    """Not what it answers. Only that it answers.

    An exception inside a tool on a live call is invisible: ADK hands the
    model an error, the model apologises, and the caller hears a desk that has
    gone vague rather than one that has crashed.
    """
    from src import trace

    trace.call_context("")
    sample = _sample(dbfile)
    tools = _wired_tools()

    broke, ran, skipped = [], [], []
    for name, fn in sorted(tools.items()):
        kwargs, missing = {}, None
        for pname, p in inspect.signature(fn).parameters.items():
            if pname in sample:
                kwargs[pname] = sample[pname]
            elif p.default is inspect.Parameter.empty:
                missing = pname
                break
        if missing:
            skipped.append(f"{name} (no sample for {missing})")
            continue
        try:
            fn(**kwargs)
            ran.append(name)
        except Exception as e:
            broke.append(f"{name}: {type(e).__name__}: {e}")

    assert not broke, "tools raised:\n  " + "\n  ".join(broke)
    assert len(ran) >= 20, (
        f"only {len(ran)} tools were actually exercised, which means the "
        f"sample data has drifted rather than that the desk got smaller. "
        f"Skipped: {skipped}")
