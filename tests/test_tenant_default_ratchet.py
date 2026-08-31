"""Stop the hard-coded tenant default spreading any further.

WHAT tenancy.py ALREADY SAYS ABOUT THIS

    IT NAMES A TENANT. In a system with four businesses behind one number, one
    of them is written into the signature of twelve functions. When routing
    works, that default is silently wrong rather than obviously wrong, which
    is the worst kind.

    IT SPREADS. The same mistake appeared in four separate places in one day,
    each written by somebody reasonably copying the shape of the function next
    to it.

`the_desk()` exists to end it: pass nothing and get the configured vendor
rather than a literal. Twenty-eight functions still carry `dealer_id="D-REF"`
and never call it.

WHY THIS IS A RATCHET AND NOT A FIX

Checked, and none of the twenty-eight is reachable with its default: every one
of the fifteen console endpoints passes the dealer through, the nightly job
loops over the dealers and passes each, and not one of them is exposed to an
agent as a tool. So there is no wrong answer being given today.

Rewriting twenty-eight signatures the day before a deadline, to fix something
that is currently answering correctly, is how a working system stops working.
This pins the number instead. It cannot grow, and the count comes down as
functions are touched for other reasons.
"""

from __future__ import annotations

import pathlib
import re

# What it was when this was written. Lower it when you fix one; never raise it.
ALLOWED = 28


def _offenders() -> list[str]:
    out = []
    for f in sorted(pathlib.Path("src").rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(
                r'def (\w+)\([^)]*dealer_id:\s*str\s*=\s*"D-[A-Z]+"[^)]*\)[^:]*:\n'
                r'((?:.|\n){0,600})', src):
            if "the_desk(" not in m.group(2):
                out.append(f"{f.name}:{m.group(1)}")
    return out


def test_the_hardcoded_tenant_default_does_not_spread():
    found = _offenders()
    assert len(found) <= ALLOWED, (
        f"{len(found)} functions default dealer_id to a named tenant, up from "
        f"{ALLOWED}. New ones must take dealer_id='' and call the_desk(). "
        f"Added: {sorted(set(found))[-3:]}")


def test_nothing_reachable_from_a_call_carries_the_default():
    """The line that makes the ratchet safe. A tool a customer can trigger
    must never be able to answer about the wrong business."""
    from src import agents
    from src.console_agent import console_agent

    tools = set()
    for a in (agents.front_agent, agents.desk_agent, agents.advice_agent,
              agents.supply_agent, console_agent):
        for t in a.tools:
            tools.add(getattr(t, "__name__", getattr(t, "name", "")))

    offending = {o.split(":")[1] for o in _offenders()}
    assert not (tools & offending), (
        f"these are reachable as tools AND default to a named tenant: "
        f"{tools & offending}")


def test_the_desk_itself_takes_no_literal():
    """The one function that is allowed to decide, and it reads configuration
    rather than naming anybody."""
    import inspect

    from src.tenancy import the_desk

    sig = inspect.signature(the_desk)
    assert sig.parameters["dealer_id"].default == ""
    assert "D-REF" not in inspect.getsource(the_desk)
