"""Check we can staff it before promising it, and mean it when we cannot.

FROM A REAL CALL, 26 AUGUST

A restaurant rang with a reach-in freezer sitting at fifteen degrees. The desk
identified the machine, registered it, quoted $217.22, opened a work order,
and only THEN asked the scheduler, which answered:

    {'ok': False, 'why': 'nobody is qualified on None'}

So the customer had been given a price and a job number for a visit that was
never going to happen. What they were offered instead was "I can arrange for a
supervisor to call you back", said three times, with no name attached, nothing
recorded anywhere, and nobody who was actually going to ring.

Two separate faults, and both were ours:

  THE ORDER. Whether we can put a certified person in front of a machine is
  one query. Asking it after the quote turns a price into an apology.

  THE FALLBACK. An escalation that is not written down is not an escalation,
  it is a way of ending an awkward conversation.

And underneath both: thirteen technicians and zero EPA 608 certificates in the
table. The gate was built, wired in and tested, and nobody ever seeded it. A
guard with an empty allow-list is not a safe system, it is a closed one.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def certified(dbfile):
    from scripts.seed_certs import load
    return load()


@pytest.fixture
def on_a_call(dbfile):
    from src import db, trace

    with db.txn() as c:
        c.execute("""UPDATE dealers SET manager_name='Dale Brenner',
                     manager_phone='+13095550100' WHERE id='D-REF'""")
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-E','+13095550101','CT-1','2026-08-26T15:11:00')")
    trace.call_context("CALL-E")
    yield
    trace.call_context("")


# The gate that refused everybody.


def test_certificates_are_actually_on_file(certified):
    """The whole reason the live call dead-ended: the table was empty."""
    from src import db

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM technician_certs").fetchone()["n"]
    assert n > 0
    assert certified["certs"] == n


def test_a_certified_shop_can_serve_a_reach_in(certified):
    from src import cover, db

    with db.connect() as c:
        a = c.execute("SELECT id FROM assets WHERE family='reach-in freezer' "
                      "LIMIT 1").fetchone()

    out = cover.can_we_serve(a["id"])
    assert out["ok"] is True
    assert out["qualified"] > 0


def test_type_one_holders_are_not_counted_for_a_walk_in(dbfile):
    """The types are not interchangeable, and the seed deliberately puts some
    people on Type I who genuinely cannot be sent to a walk-in.

    Seeded with a full crew rather than the fixture's single technician,
    because the point is the spread across the shop.
    """
    from scripts.seed_certs import load

    from src import cover, db

    with db.txn() as c:
        for i in range(20, 28):
            c.execute("INSERT INTO technicians (id,name,dealer_id) VALUES (?,?,?)",
                      (f"T-{i}", f"Technician {i}", "D-REF"))
    load()

    with db.connect() as c:
        techs = c.execute("SELECT id FROM technicians WHERE dealer_id='D-REF'").fetchall()

    reach = sum(cover.can_work_on(t["id"], "reach-in freezer")["allowed"] for t in techs)
    walk = sum(cover.can_work_on(t["id"], "walk-in cooler")["allowed"] for t in techs)
    assert reach == len(techs), "every type covers a reach-in"
    assert walk < reach, "a walk-in needs Type II or Universal"


# The order of the questions.


def test_an_unknown_family_is_our_gap_not_a_refusal(certified, dbfile):
    """It reached the customer as "nobody is qualified on None", which reads as
    a refusal and is really an empty column on our side."""
    from src import cover, db

    with db.txn() as c:
        c.execute("UPDATE assets SET family=NULL WHERE id='AS-FREEZER'")

    out = cover.can_we_serve("AS-FREEZER")
    assert out["ok"] is False
    assert out["unknown_family"] is True
    assert "Do NOT tell them nobody is qualified" in out["say"]


def test_nobody_qualified_refuses_to_offer_a_slot(dbfile):
    """With no certificates on file, which is exactly how it shipped."""
    from src import cover, db

    with db.connect() as c:
        a = c.execute("SELECT id FROM assets WHERE family='reach-in freezer' "
                      "LIMIT 1").fetchone()

    out = cover.can_we_serve(a["id"])
    assert out["ok"] is False
    assert out["qualified"] == 0
    assert "do NOT take a booking you cannot staff" in out["say"]


def test_the_desk_is_told_to_check_before_it_quotes(dbfile):
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    assert "BEFORE YOU QUOTE OR PROMISE ANYTHING, call can_we_serve" in rules
    assert "do NOT say \"a supervisor will call you back\"" in rules
    assert "SAY IT ONCE" in rules
    assert "ASK FOR A MODEL NUMBER ONCE" in rules


def test_the_scheduler_no_longer_offers_a_nameless_supervisor(dbfile):
    """The exact string a customer heard three times."""
    import inspect

    from src import scheduling

    src = inspect.getsource(scheduling)
    assert "offer to have a supervisor call back" not in src
    assert "escalate.raise_it" in src


# What an honest escalation looks like.


def test_an_escalation_has_a_name_and_a_time(certified, on_a_call):
    from src import db, escalate

    with db.connect() as c:
        a = c.execute("SELECT id FROM assets WHERE family='reach-in freezer' "
                      "LIMIT 1").fetchone()

    out = escalate.raise_it("no_qualified_technician", a["id"],
                            detail="needs EPA608 Type II")
    assert out["ok"] is True
    assert "Dale Brenner" in out["promised"]
    assert out["urgent"] is True, "a failing freezer is not a next-week problem"
    assert "Do NOT say 'a supervisor will call you'" in out["say"]


def test_an_escalation_is_written_down(certified, on_a_call):
    """One that is not is a way of ending an awkward conversation."""
    from src import db, escalate

    out = escalate.raise_it("no_qualified_technician", "AS-FREEZER")

    with db.connect() as c:
        row = c.execute("SELECT * FROM escalations WHERE id=?",
                        (out["escalation_id"],)).fetchone()
    assert row["state"] == "open"
    assert row["promised"] == out["promised"]
    assert row["call_id"] == "CALL-E", "tied to the call it came from"


def test_it_goes_on_the_queue_that_actually_delivers(certified, on_a_call):
    """The same machinery as every other promise this system makes, rather
    than a line in a transcript nobody opens."""
    from src import db, escalate

    escalate.raise_it("no_qualified_technician", "AS-FREEZER")

    with db.connect() as c:
        row = c.execute("SELECT kind, status, context FROM followups "
                        "WHERE kind='escalation'").fetchone()
    assert row is not None
    assert row["status"] == "queued"
    assert "Dale Brenner" in row["context"]


def test_a_slower_promise_for_something_that_is_not_spoiling(certified, on_a_call):
    """Two hours for a freezer. An oven can wait until the morning."""
    from src import db, escalate

    with db.txn() as c:
        c.execute("UPDATE assets SET family='oven' WHERE id='AS-FREEZER'")

    out = escalate.raise_it("no_qualified_technician", "AS-FREEZER")
    assert out["urgent"] is False
    assert "hours" not in out["promised"]


def test_an_escalation_must_be_taken_by_somebody_named(certified, on_a_call):
    from src import escalate

    out = escalate.raise_it("no_qualified_technician", "AS-FREEZER")
    assert escalate.take(out["escalation_id"], by="")["ok"] is False
    assert escalate.take(out["escalation_id"], by="Dale Brenner")["state"] == "picked_up"


def test_an_overdue_promise_is_visible(certified, on_a_call):
    """A promise that quietly lapses is worse than one never made."""
    from src import db, escalate

    out = escalate.raise_it("no_qualified_technician", "AS-FREEZER")
    with db.txn() as c:
        c.execute("UPDATE escalations SET promised_by='2020-01-01T00:00:00' "
                  "WHERE id=?", (out["escalation_id"],))

    waiting = escalate.open_escalations()
    assert waiting[0]["overdue"] is True


# The null that caused all of it.


def test_a_registered_machine_gets_a_family(dbfile):
    """A null family produced "nobody is qualified on None" to a customer AND
    sent the quote to an assumed 1.5 hours with 114 comparable jobs in the
    table. One empty column, two wrong answers."""
    from src import caller, db, trace

    who = caller.resolve("+13095557777")
    with db.txn() as c:
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-F','+13095557777',?,'2026-08-26T15:00:00')",
                  (who["contact_id"],))
    trace.call_context("CALL-F")
    caller.confirm_details(name="Arjun Raman", account_name="Coriander House",
                           site_label="Bettendorf")
    out = caller.register_asset(manufacturer="Traulsen", model_number="G12010")
    trace.call_context("")

    with db.connect() as c:
        row = c.execute("SELECT family FROM assets WHERE id=?",
                        (out["asset_id"],)).fetchone()
    assert row["family"], "inferred from our own machines of the same model"


def test_unknown_family_uses_real_jobs_rather_than_an_assumption(dbfile, corpus):
    """Falling back to every job on the book is a wider answer, but it is
    still made of jobs we actually did, and the basis says so."""
    from src import pricing

    out = pricing.hours_for("")
    assert out["jobs"] > 0
    assert "we do not know what kind of machine this is" in out["basis"]
