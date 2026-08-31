"""The guards keep a record of what they did.

WHY THIS MATTERS MORE THAN IT LOOKS

guards.py is where this product's central claim lives. It fills in identifiers
so nobody is asked for an Asset ID, it sends tools to the vendor the call was
routed to rather than the default one, it refuses to touch a machine belonging
to another customer, and it stops the desk escalating over a fact a tool had
just disproved.

Until this table existed, every one of those was printed to stdout and thrown
away. The file contained no INSERT of any kind. So "it refuses rather than
inventing" was true in the code and uncountable everywhere else: nobody could
answer how often it happened, whether it was getting better, or whether a given
guard had ever fired on a real call rather than only in a test.

A guard nobody can count is indistinguishable from a guard that does not work,
and these fire on the rarest calls.

THE TWO PROPERTIES WORTH PROTECTING

That interventions are recorded at all, and that recording one can never break
a call. The second is the more important: a guard that throws while writing
down that it worked has made things worse than having no guard.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, **state):
        self.state = state


def _tool(name):
    def f():
        pass
    f.__name__ = name
    return f


def test_a_write_before_the_call_is_understood_is_recorded(dbfile):
    from src import guards

    guards.guard_tool(_tool("promise_slot"), {"when": "tomorrow"}, _Ctx())

    out = guards.what_the_guards_did()
    assert out["blocked"] == 1
    assert any(k["kind"] == "no_intent" for k in out["by_kind"])


def test_an_action_from_the_wrong_kind_of_call_is_recorded(dbfile):
    from src import guards

    guards.guard_tool(_tool("promise_slot"), {"when": "tomorrow"},
                      _Ctx(intent="order"))

    out = guards.what_the_guards_did()
    assert any(k["kind"] == "wrong_intent" for k in out["by_kind"])


def test_each_kind_is_reported_in_words_an_owner_would_use(dbfile):
    """The table stores the code's vocabulary. A console reading "wrong_intent"
    to a business owner has not told them anything."""
    from src import guards

    guards.guard_tool(_tool("promise_slot"), {}, _Ctx(intent="order"))
    out = guards.what_the_guards_did()

    means = [k["means"] for k in out["by_kind"]]
    assert any("different kind of call" in m for m in means)
    assert all(k["means"] != k["kind"] for k in out["by_kind"])


def test_blocked_and_corrected_are_counted_separately(dbfile):
    """A substitution means the customer never noticed. A block means the
    model was told no and had to do something else. Collapsing them would hide
    the more interesting number."""
    from src import guards

    guards.guard_tool(_tool("promise_slot"), {}, _Ctx())
    out = guards.what_the_guards_did()

    assert out["blocked"] >= 1
    assert out["corrected"] == 0
    assert "put right without the customer noticing" in out["say"]


def test_argument_values_are_never_stored(dbfile):
    """A tool call carries names, addresses and phone numbers. This table
    counts interventions; it is not a second copy of the call record."""
    from src import db, guards

    guards.guard_tool(_tool("promise_slot"),
                      {"customer_name": "Marie Dubois",
                       "phone": "+15551230000"},
                      _Ctx(intent="order"))

    with db.connect() as c:
        rows = c.execute("SELECT args_seen, detail FROM interventions").fetchall()

    assert rows
    blob = " ".join(f"{r['args_seen']} {r['detail']}" for r in rows)
    assert "Marie Dubois" not in blob
    assert "+15551230000" not in blob
    # The NAMES are kept, so a change in shape is still visible.
    assert "customer_name" in blob


def test_recording_failure_never_breaks_the_call(dbfile, monkeypatch):
    """The guard is the job and the record is the evidence. If writing the
    evidence fails, the interception must still happen."""
    from src import guards

    def broken(*a, **k):
        raise RuntimeError("the database is gone")

    monkeypatch.setattr(guards, "_record", broken)

    with pytest.raises(RuntimeError):
        guards._record("x", "y", "z")

    # And with the real _record over a broken database, the guard still returns
    # its refusal rather than propagating.
    monkeypatch.undo()
    monkeypatch.setattr("src.db.txn", broken)

    out = guards.guard_tool(_tool("promise_slot"), {}, _Ctx(intent="order"))
    assert out is not None
    assert out["blocked"] is True


def test_a_permitted_tool_records_nothing(dbfile):
    """Looking things up is always allowed, and an empty table is the correct
    result for a call where nothing went wrong."""
    from src import guards

    assert guards.guard_tool(_tool("lookup_product"), {"query": "gasket"},
                             _Ctx(intent="product")) is None

    out = guards.what_the_guards_did()
    assert out["blocked"] == 0
    assert out["corrected"] == 0


def test_every_kind_the_code_emits_has_plain_english(dbfile):
    """A console reading "unasked_fitness" to a business owner has told them
    nothing. This caught exactly that: a new guard was added and its entry in
    the owner-facing vocabulary was not, so the raw code word would have gone
    on screen.
    """
    import inspect
    import re

    from src import guards

    emitted = set(re.findall(r'_record\("([a-z_]+)"', inspect.getsource(guards)))
    assert emitted, "no _record calls found, the scan is broken"

    missing = emitted - set(guards._MEANS)
    assert not missing, f"no plain-English meaning for: {sorted(missing)}"
