"""A customer who has never called before.

`confirm_details` and `register_asset` existed in caller.py and were wired to
nothing. The opening brief told the agent to get the caller's name and their
business early on, and there was no tool to write either down, so a first-time
caller stayed a phone number named "unknown" and everything they said about
themselves died with the call.

`register_asset` was worse: it recorded `installed_on` as NULL, always. So a
new customer's machine could never be checked against the manufacturer's
warranty terms, and every one of them was told we could not see their cover.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def on_a_call(dbfile):
    """A live call from a number nobody has seen before."""
    from src import caller, db, trace

    who = caller.resolve("+13095557777")
    with db.txn() as c:
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES (?,?,?,?)",
                  ("CALL-NEW", "+13095557777", who["contact_id"],
                   "2026-08-25T18:00:00"))
    trace.call_context("CALL-NEW")
    yield who
    trace.call_context("")


def test_an_unknown_number_is_registered_before_the_greeting_ends(dbfile):
    from src import caller

    who = caller.resolve("+13095557777")
    assert who["known"] is False
    assert who["registered"] is True
    assert who["contact_id"], "nobody is left as a floating phone number"


def test_the_desk_can_write_down_who_they_are(on_a_call):
    """Without this they stay named unknown forever."""
    from src import caller, db

    out = caller.confirm_details(name="Marcus Bell",
                                 account_name="Bell Street Kitchen",
                                 site_label="the Davenport kitchen",
                                 role="owner")
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT name, role FROM contacts WHERE id=?",
                        (on_a_call["contact_id"],)).fetchone()
    assert row["name"] == "Marcus Bell"
    assert row["role"] == "owner"

    # And the next call greets them by name rather than as a stranger.
    again = caller.resolve("+13095557777")
    assert again["known"] is True
    assert again["contact_name"] == "Marcus Bell"


def test_no_identifier_ever_crosses_the_model(on_a_call):
    """A model carrying a contact id is a model that can invent one, and an
    invented contact id writes a stranger's name onto somebody else's
    account. It is read from the call row instead."""
    import inspect

    from src import caller

    sig = inspect.signature(caller.confirm_details)
    assert sig.parameters["contact_id"].default == ""
    assert "_on_this_call" in inspect.getsource(caller.confirm_details)


def test_a_new_machine_records_when_it_went_in(on_a_call):
    """It used to be NULL, always, so a new customer could never be told
    anything about their warranty."""
    from src import caller, cover, db

    caller.confirm_details(name="Marcus Bell", account_name="Bell Street Kitchen",
                           site_label="the Davenport kitchen")
    out = caller.register_asset(manufacturer="Traulsen", model_number="G12010",
                                family="reach-in freezer",
                                installed_on="2024-03-01")
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT installed_on FROM assets WHERE id=?",
                        (out["asset_id"],)).fetchone()
    assert row["installed_on"] == "2024-03-01"


def test_a_new_customers_date_becomes_a_claim_not_a_discount(on_a_call):
    """The whole point of asking when it went in, corrected.

    This test used to assert that a new customer's stated date granted cover
    outright. That was wrong, and it was the honour-system hole: anybody could
    ring, say the machine went in last year, and be quoted zero.

    Asking when it went in is still worth doing. It just produces a CLAIM the
    customer can prove, rather than a discount nobody has checked.
    """
    from scripts.load_warranties import load

    from src import caller, cover

    load()
    caller.confirm_details(name="Marcus Bell", account_name="Bell Street Kitchen",
                           site_label="the Davenport kitchen")
    out = caller.register_asset(manufacturer="Traulsen", model_number="G12010",
                                family="reach-in freezer",
                                installed_on="2024-03-01")

    cover_out = cover.covers(out["asset_id"], "Electronic control board")
    assert cover_out["known"] is True
    assert cover_out["claimed_parts"] is True, "on their date it would stand"
    assert cover_out["parts"] is False, "but it is not ours to grant"
    assert cover_out["needs_proof"] is True
    assert "Do NOT say it is covered" in cover_out["say"]


def test_a_machine_with_no_site_is_refused_rather_than_orphaned(dbfile):
    from src import caller

    assert caller.register_asset(manufacturer="Traulsen",
                                 model_number="G12010")["ok"] is False


def test_the_desk_is_told_to_write_it_down_as_they_say_it(dbfile):
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    assert "write down who they are AS SOON AS THEY SAY IT" in rules
    assert "roughly when it went in" in rules, (
        "the install date decides the warranty, so it has to be asked for")


def test_an_unknown_machine_is_registered_before_anything_needs_it(dbfile):
    """It used to be told this THREE STEPS TOO LATE.

    The instruction to register a machine we have never seen sat below the
    steps that need one, so the desk established the machine, ran
    should_send_someone, can_we_serve and quote_visit, and only then read the
    line telling it to put the thing on the account. All three ran with no
    asset. On a live call that found no qualified technician for a freezer
    eight people could have worked on and escalated it to a human callback two
    days out, twice, in two languages.
    """
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    at_registration = rules.index("call register_asset now")
    for later in ("should_send_someone", "can_we_serve", "quote_visit"):
        assert rules.index(later) > at_registration, (
            f"{later} is described before the machine is registered, and it "
            "cannot work without one")


def test_a_catalogue_id_is_not_an_asset_id(dbfile):
    """identify_equipment returns a CATALOGUE id. Passing it downstream passes
    a number belonging to a different table, which the guard then has to
    reject. On a live call it arrived as asset_id='68924'."""
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    assert "CATALOGUE id, not an asset id" in rules


def test_the_tools_are_actually_on_the_desk(dbfile):
    from src import agents

    names = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "confirm_details" in names
    assert "register_asset" in names
