"""Customers, crew, and the move that turns a lead into a customer.

WHAT WAS READ ONLY

111 customers written only when somebody rang in, 19 engineers written only by
the seed scripts, and a prospect table with no way out of it. The crew was the
worst: crew.py already reports a certification mismatch and there was no way
to correct it, hire anybody, or stand down somebody who left.

THE ONE WORTH READING

`win_the_lead`. Hunting finds a reason to ring, prospect.py rings, somebody
says yes, and before this that was the end: the prospect stayed a prospect and
`wishlist`, the table holding what a customer asked for, had zero rows.

The tests below pin the four things that conversion must not get wrong: it is
atomic, it does not invent consent, it does not duplicate a caller who already
has a phone row, and it takes the lead off the hunt list.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_lead(dbfile):
    """A lead found the way hunting.py finds them, with a reason attached."""
    from src import db

    with db.txn() as c:
        c.execute(
            "INSERT INTO prospects (id,dealer_id,name,kind,address,phone_e164,"
            "line_type,found_on,signal,signal_kind,signal_score,signal_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("P-TEST", "D-REF", "Riverbend Diner", "restaurant",
             "12 Mill St", "+15551230000", "landline", "2026-08-30",
             "walk-in cooler warm", "public_complaint", 0.8,
             "reviewer said the walk-in was warm for a week"))
    return "P-TEST"


# --------------------------------------------------------------------------
# customers
# --------------------------------------------------------------------------

def test_a_customer_can_be_added_and_corrected(dbfile):
    from src import book, db

    made = book.set_customer("D-REF", "Vasquez Catering", kind="business")
    assert made["ok"] is True

    book.set_customer("D-REF", "Vasquez Catering", trade_terms="net 30")

    with db.connect() as c:
        row = c.execute("SELECT kind, trade_terms FROM accounts WHERE id = ?",
                        (made["account_id"],)).fetchone()
    assert row["kind"] == "business"
    assert row["trade_terms"] == "net 30"


def test_adding_a_customer_needs_more_than_a_name(dbfile):
    """Without this a mistyped name silently opens a SECOND account for a
    customer who already exists, and their history splits in two."""
    from src import book

    out = book.set_customer("D-REF", "Totally New Cafe")
    assert out["ok"] is False
    assert "kind" in out["adding_needs"]


def test_correcting_terms_does_not_wipe_the_name(dbfile):
    """Only what is passed changes, the same rule the shop floor follows."""
    from src import book, db

    made = book.set_customer("D-REF", "Harbour Fish", kind="business",
                             trade_terms="net 30")
    book.set_customer("D-REF", "Harbour Fish", notes="pays late")

    with db.connect() as c:
        row = c.execute("SELECT name, trade_terms, notes FROM accounts "
                        "WHERE id = ?", (made["account_id"],)).fetchone()
    assert row["name"] == "Harbour Fish"
    assert row["trade_terms"] == "net 30", "the terms were wiped by a note"
    assert row["notes"] == "pays late"


def test_closing_a_customer_keeps_their_history(dbfile):
    """NOT a delete. Work orders point at accounts(id), and what somebody
    bought is how you answer a warranty claim two years later."""
    from src import book, db

    made = book.set_customer("D-REF", "Gone Bakery", kind="business")
    with db.txn() as c:
        c.execute(
            "INSERT INTO sites (id,account_id,label) VALUES ('S-G',?,'shop')",
            (made["account_id"],))
        c.execute(
            "INSERT INTO work_orders (id,account_id,site_id,reported_symptom,"
            "opened_at,dealer_id) VALUES ('W-G',?,'S-G','oven cold',?,'D-REF')",
            (made["account_id"], "2026-01-01"))

    out = book.close_customer("D-REF", "Gone Bakery", why="stopped trading")
    assert out["ok"] is True
    assert out["history_kept"] == 1

    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM work_orders "
                         "WHERE account_id = ?",
                         (made["account_id"],)).fetchone()["n"] == 1
        assert c.execute("SELECT closed_on FROM accounts WHERE id = ?",
                         (made["account_id"],)).fetchone()["closed_on"]

    assert not any(cu["id"] == made["account_id"]
                   for cu in book.the_book("D-REF")["customers"]), (
        "a closed customer is still on the book")


def test_an_ambiguous_customer_changes_nothing(dbfile):
    from src import book

    book.set_customer("D-REF", "Bridge Cafe North", kind="business")
    book.set_customer("D-REF", "Bridge Cafe South", kind="business")

    out = book.set_customer("D-REF", "Bridge Cafe", notes="x")
    assert out["ok"] is False
    assert len(out["which"]) == 2


# --------------------------------------------------------------------------
# the crew
# --------------------------------------------------------------------------

def test_an_engineer_can_be_hired(dbfile):
    from src import book, db

    out = book.set_engineer("D-REF", "Marta Quinn", phone="+15551239999",
                            email="marta@example.com", home_base="Depot")
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT email, active FROM technicians WHERE id = ?",
                        (out["engineer_id"],)).fetchone()
    assert row["email"] == "marta@example.com"
    assert row["active"] == 1


def test_hiring_needs_a_way_to_reach_them(dbfile):
    """An engineer the desk cannot reach cannot be dispatched and cannot be
    sent a briefing, but WOULD show on the crew list as available."""
    from src import book

    out = book.set_engineer("D-REF", "Unreachable Person")
    assert out["ok"] is False
    assert "phone or email" in out["adding_needs"]


def test_standing_down_keeps_the_jobs_they_did(dbfile):
    """Appointments, visits and certifications point at technicians(id).
    Somebody who left still did the repair a customer is ringing about."""
    from src import book, db

    made = book.set_engineer("D-REF", "Leaving Soon", phone="+15550001111")
    out = book.stand_down_engineer("D-REF", "Leaving Soon")
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT active FROM technicians WHERE id = ?",
                        (made["engineer_id"],)).fetchone()
    assert row is not None, "the engineer row was deleted"
    assert row["active"] == 0

    assert not any(m["id"] == made["engineer_id"]
                   for m in book.the_book("D-REF")["crew"])


def test_standing_down_says_what_is_still_in_their_diary(dbfile):
    """Said rather than silently reassigned. Moving somebody else's diary
    unasked is how two engineers arrive at one site."""
    from src import book, db

    made = book.set_engineer("D-REF", "Busy Diary", phone="+15550002222")
    with db.txn() as c:
        c.execute("INSERT INTO appointments (id,technician_id,starts_at,"
                  "ends_at) VALUES ('AP-1',?,?,?)",
                  (made["engineer_id"], "2099-01-01T09:00", "2099-01-01T11:00"))

    out = book.stand_down_engineer("D-REF", "Busy Diary")
    assert out["still_booked"] == 1
    assert "Reassign" in out["note"]


def test_rehiring_puts_them_back_on_the_crew(dbfile):
    """Stood down and then rehired must not stay invisible because the flag
    was never reset."""
    from src import book

    book.set_engineer("D-REF", "Came Back", phone="+15550003333")
    book.stand_down_engineer("D-REF", "Came Back")
    book.set_engineer("D-REF", "Came Back", phone="+15550004444")

    assert any(m["name"] == "Came Back" for m in book.the_book("D-REF")["crew"])


# --------------------------------------------------------------------------
# the lead becoming a customer
# --------------------------------------------------------------------------

def test_a_won_lead_becomes_a_customer_with_a_name_and_a_want(a_lead):
    """The whole chain in one call: the account, the person, the site, the
    phone and what they asked for."""
    from src import book, db

    out = book.win_the_lead("D-REF", a_lead, "Dana Ruiz",
                            wants="replacement walk-in cooler")
    assert out["ok"] is True

    with db.connect() as c:
        acct = c.execute("SELECT name, won_from_prospect, notes FROM accounts "
                         "WHERE id = ?", (out["account_id"],)).fetchone()
        contact = c.execute("SELECT name FROM contacts WHERE account_id = ?",
                            (out["account_id"],)).fetchone()
        site = c.execute("SELECT address FROM sites WHERE account_id = ?",
                         (out["account_id"],)).fetchone()
        phone = c.execute("SELECT e164 FROM phones WHERE contact_id = ?",
                          (out["contact_id"],)).fetchone()
        want = c.execute("SELECT want FROM wishlist WHERE account_id = ?",
                         (out["account_id"],)).fetchone()

    assert acct["name"] == "Riverbend Diner"
    assert acct["won_from_prospect"] == a_lead
    assert "warm for a week" in acct["notes"], (
        "the reason they were rung was lost the moment it paid off")
    assert contact["name"] == "Dana Ruiz"
    assert site["address"] == "12 Mill St"
    assert phone["e164"] == "+15551230000"
    assert want["want"] == "replacement walk-in cooler"


def test_winning_a_lead_does_not_grant_marketing_consent(a_lead):
    """Agreeing to become a customer is not agreeing to be marketed at. A
    conversion that granted it silently turns every won lead into a
    subscription nobody asked for."""
    from src import book, db

    out = book.win_the_lead("D-REF", a_lead, "Dana Ruiz")
    assert out["marketing_consent"] is False

    with db.connect() as c:
        row = c.execute("SELECT granted FROM outreach_consent "
                        "WHERE account_id = ?", (out["account_id"],)).fetchone()
    assert row is None or row["granted"] == 0


def test_consent_is_recorded_with_who_said_it(a_lead):
    """When they DO agree, what is stored is evidence, not a flag."""
    from src import book, db

    out = book.win_the_lead("D-REF", a_lead, "Dana Ruiz",
                            agreed_to_contact=True)

    with db.connect() as c:
        row = c.execute("SELECT granted, granted_via, evidence_ref FROM "
                        "outreach_consent WHERE account_id = ?",
                        (out["account_id"],)).fetchone()
    assert row["granted"] == 1
    assert "Dana Ruiz" in row["granted_via"]
    assert row["evidence_ref"] == a_lead


def test_a_won_lead_stops_being_hunted(a_lead):
    """hunting.py and prospect.py both select on `approached_on IS NULL`, so
    setting only the outcome leaves a won customer on tomorrow's call list."""
    from src import book, hunting, prospect

    book.win_the_lead("D-REF", a_lead, "Dana Ruiz")

    assert not any(r.get("id") == a_lead
                   for r in hunting.todays_list("D-REF").get("leads", []))
    ready = prospect.ring_the_worthwhile("D-REF")
    assert not any(r.get("id") == a_lead
                   for r in ready.get("ready", []) + ready.get("held", []))


def test_a_lead_who_already_rang_in_is_not_duplicated(a_lead):
    """phones.e164 is the primary key. A lead who rang before being converted
    already has a row, and writing blind fails the whole transaction
    mid-conversion."""
    from src import book, caller, db

    known = caller.resolve("+15551230000", "D-REF")
    first = known.get("account_id")
    assert first, "the caller was not registered"

    out = book.win_the_lead("D-REF", a_lead, "Dana Ruiz", wants="a cooler")
    assert out["ok"] is True
    assert out["joined_an_existing_customer"] is True
    assert out["account_id"] == first, "a second account was opened"

    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM phones WHERE e164 = ?",
                         ("+15551230000",)).fetchone()["n"] == 1
        assert c.execute("SELECT name FROM contacts WHERE account_id = ?",
                         (first,)).fetchone()["name"] == "Dana Ruiz"


def test_a_lead_cannot_be_won_twice(a_lead):
    from src import book

    book.win_the_lead("D-REF", a_lead, "Dana Ruiz")
    again = book.win_the_lead("D-REF", a_lead, "Someone Else")
    assert again["ok"] is False
    assert "already" in again["why"]


def test_a_lead_needs_a_persons_name(a_lead):
    """A customer with no name is a row nobody can ring back."""
    from src import book

    assert book.win_the_lead("D-REF", a_lead, "")["ok"] is False


def test_a_lost_lead_is_kept_so_it_is_not_found_again(a_lead):
    """The search that found them was billable, and a deleted lead gets found
    and rung a second time by the next hunt."""
    from src import book, db

    out = book.lose_the_lead("D-REF", a_lead, why="they use somebody else")
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT outcome, approached_on FROM prospects "
                        "WHERE id = ?", (a_lead,)).fetchone()
    assert row is not None, "the lead was deleted"
    assert row["outcome"].startswith("lost")
    assert row["approached_on"], "a lost lead is still on the hunt list"

    assert not any(l["id"] == a_lead for l in book.the_book("D-REF")["leads"])


def test_one_business_cannot_touch_anothers_book(a_lead):
    from src import book

    assert book.win_the_lead("D-IT", a_lead, "Dana Ruiz")["ok"] is False
    assert book.lose_the_lead("D-IT", a_lead)["ok"] is False
    assert book.close_customer("D-IT", "Riverbend")["ok"] is False


def test_the_phone_agent_can_never_edit_the_book(dbfile):
    """Structural, and the same rule the shop floor follows: the console is
    the only place customers and crew are changed. A desk that could close a
    customer or stand down an engineer mid-call is a desk that can be talked
    into it."""
    from src import agents

    for name in ("front_agent", "desk_agent", "supply_agent", "advice_agent"):
        tools = [getattr(t, "__name__", "")
                 for t in getattr(agents, name).tools]
        for banned in ("set_customer", "close_customer", "set_engineer",
                       "stand_down_engineer", "win_the_lead"):
            assert banned not in tools, f"{name} can {banned}"


# --------------------------------------------------------------------------
# the two failures found by driving the real website
# --------------------------------------------------------------------------

def test_a_lead_is_found_by_name_like_everything_else(a_lead):
    """These were the ONLY console tools demanding an opaque id. Parts match
    on a name, machines on a model number, customers and engineers on a name.
    Told "Corner Grocers are not interested" the agent passed the name, the
    tool refused, and the owner was told it had been closed."""
    from src import book, db

    out = book.lose_the_lead("D-REF", "Riverbend", why="uses somebody else")
    assert out["ok"] is True

    with db.connect() as c:
        assert c.execute("SELECT outcome FROM prospects WHERE id = ?",
                         (a_lead,)).fetchone()["outcome"].startswith("lost")


def test_an_ambiguous_lead_name_changes_nothing(dbfile):
    from src import book, db

    with db.txn() as c:
        for n, nm in ((1, "Bridge Cafe North"), (2, "Bridge Cafe South")):
            c.execute("INSERT INTO prospects (id,dealer_id,name,found_on) "
                      "VALUES (?,?,?,?)",
                      (f"P-AMB{n}", "D-REF", nm, "2026-08-30"))

    out = book.lose_the_lead("D-REF", "Bridge Cafe")
    assert out["ok"] is False
    assert len(out["which"]) == 2

    with db.connect() as c:
        assert not c.execute(
            "SELECT 1 FROM prospects WHERE id LIKE 'P-AMB%' "
            "AND outcome IS NOT NULL").fetchone(), "an ambiguous name closed one"


def test_the_console_never_reports_a_change_it_did_not_make(dbfile):
    """OBSERVED LIVE. The agent called the right tool, the tool refused, and
    the reply was "Closed Corner Grocers lead." Nothing had been closed.

    A console that reports work it did not do is worse than one that cannot do
    the work, because the owner stops checking. The root cause is fixed above,
    but a confident false confirmation must not depend on a prompt rule
    holding: an instruction governs what the model is ASKED to do."""
    from src.main import _tell_the_truth

    lie = "Closed Corner Grocers lead."
    refused = [{"ok": False, "why": "no lead matching 'Corner Grocers'"}]
    assert _tell_the_truth(lie, refused).startswith("Nothing was changed")
    assert "Corner Grocers" in _tell_the_truth(lie, refused)

    # A real success is passed through untouched.
    assert _tell_the_truth(lie, [{"ok": True}]) == lie

    # So is a partial one. Some work done is work done.
    assert _tell_the_truth(lie, [{"ok": False, "why": "x"},
                                 {"ok": True}]) == lie

    # And a question is not a claim, so it is left alone.
    asked = "Is Testy McTest a business or a person?"
    assert _tell_the_truth(asked, [{"ok": False, "why": "needs a kind"}]) == asked


def test_the_refusal_carries_what_the_tool_asked_for(dbfile):
    """Passing on "it was refused" and dropping the reason makes the owner
    guess. The missing field is the whole point of the message."""
    from src.main import _tell_the_truth

    out = _tell_the_truth(
        "Added Testy McTest.",
        [{"ok": False, "why": "no customer matching it",
          "adding_needs": ["kind"]}])
    assert "kind" in out
