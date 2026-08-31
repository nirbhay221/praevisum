"""Who pays, who is allowed to do the work, and when the customer can be there.

Three questions nothing in this system ever asked.

There was no warranty anywhere: not a table, not a column, not a tool. So the
desk would quote four hundred dollars for a board on an eleven month old
machine that was covered, and be confidently wrong in the direction that costs
the customer money.

`technician_skills` recorded that somebody works on reach-in freezers. EPA
Section 608 is what legally permits opening a refrigerant circuit at all, and
nothing checked it. The briefing already warned that R-290 is flammable and
could not say whether the person being sent was licensed to touch it.

And the diary knew when a technician was free while nothing ever asked when
the restaurant could take one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest


# Warranty.


def test_not_knowing_is_not_the_same_as_expired(dbfile):
    """The distinction that matters. Telling somebody their machine is out of
    warranty when we simply have no date is a claim we cannot support, and it
    is the one they are most likely to be able to check."""
    from src import db, cover

    with db.connect() as c:
        a = c.execute("SELECT id FROM assets LIMIT 1").fetchone()

    out = cover.warranty_status(a["id"])
    assert out["known"] is False
    assert "Do NOT tell them it is out of warranty" in out["say"]


def test_a_covered_machine_is_flagged_before_any_price(dbfile):
    from src import db, cover

    with db.connect() as c:
        a = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
    later = (date.today() + timedelta(days=90)).isoformat()
    with db.txn() as c:
        c.execute("""UPDATE assets SET warranty_until=?, warranty_provider=?
                     WHERE id=?""", (later, "Traulsen", a["id"]))

    out = cover.warranty_status(a["id"])
    assert out["covered"] is True
    assert "BEFORE quoting" in out["say"]
    assert "Traulsen" in out["say"]


def test_an_expired_warranty_is_said_plainly(dbfile):
    """Letting them assume either way is worse than telling them."""
    from src import db, cover

    with db.connect() as c:
        a = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
    past = (date.today() - timedelta(days=30)).isoformat()
    with db.txn() as c:
        c.execute("UPDATE assets SET warranty_until=? WHERE id=?", (past, a["id"]))

    out = cover.warranty_status(a["id"])
    assert out["covered"] is False
    assert "chargeable" in out["say"]


def test_the_desk_is_told_to_check_before_quoting(dbfile):
    """The rule used to be "call warranty_status before quoting any price",
    which left the desk to assemble the price itself afterwards. It now has a
    tool that does both, and the tool checks the cover first, per line: see
    tests/test_pricing.py. The guarantee is stronger, so the assertion moved
    to the stronger thing rather than being dropped."""
    import inspect

    from src import agents, pricing

    rules = " ".join(agents.DESK_RULES.split())
    assert "Call quote_visit for ANY question about what a visit will cost" in rules
    assert "never guess an hourly rate" in rules

    # And the tool genuinely consults the cover rather than the rule merely
    # saying it does.
    assert "covers(" in inspect.getsource(pricing.quote_visit)


# Certification, which is not skill.


def test_an_uncertified_technician_is_refused(dbfile):
    """Somebody can be the best refrigeration engineer in the state and still
    not hold the certificate that permits opening a sealed system."""
    from src import db, cover

    with db.connect() as c:
        t = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()

    out = cover.can_work_on(t["id"], "walk-in cooler")
    assert out["allowed"] is False
    assert "EPA608-II" in out["needs"]
    assert "not an inefficiency" in out["say"]


def test_a_certified_technician_is_allowed(dbfile):
    from src import db, cover

    with db.connect() as c:
        t = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()
    with db.txn() as c:
        c.execute("""INSERT INTO technician_certs (technician_id,cert,number)
                     VALUES (?, 'EPA608-UNIVERSAL', 'U-123')""", (t["id"],))

    out = cover.can_work_on(t["id"], "walk-in cooler")
    assert out["allowed"] is True
    assert "EPA608-UNIVERSAL" in out["holds"]


def test_the_types_are_not_interchangeable(dbfile):
    """Type I is small appliances. It does not permit work on a walk-in,
    however experienced the person holding it is."""
    from src import db, cover

    with db.connect() as c:
        t = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()
    with db.txn() as c:
        c.execute("""INSERT INTO technician_certs (technician_id,cert)
                     VALUES (?, 'EPA608-I')""", (t["id"],))

    assert cover.can_work_on(t["id"], "reach-in cooler")["allowed"] is True
    assert cover.can_work_on(t["id"], "walk-in cooler")["allowed"] is False


def test_a_certificate_expiring_before_the_visit_is_not_valid(dbfile):
    """Valid today is not valid for a job next month."""
    from src import db, cover

    with db.connect() as c:
        t = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()
    soon = (date.today() + timedelta(days=5)).isoformat()
    with db.txn() as c:
        c.execute("""INSERT INTO technician_certs (technician_id,cert,expires_on)
                     VALUES (?, 'EPA608-UNIVERSAL', ?)""", (t["id"], soon))

    assert cover.can_work_on(t["id"], "walk-in cooler")["allowed"] is True
    later = (date.today() + timedelta(days=40)).isoformat()
    out = cover.can_work_on(t["id"], "walk-in cooler", on=later)
    assert out["allowed"] is False
    assert "expired" in out["why"]


def test_a_laptop_needs_no_refrigerant_certificate(dbfile):
    """Certification is asked about circuits, not about competence in general."""
    from src import db, cover

    with db.connect() as c:
        t = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()

    out = cover.can_work_on(t["id"], "laptop")
    assert out["allowed"] is True
    assert "no refrigerant certification is required" in out["why"]


def test_a_flammable_refrigerant_is_carried_into_the_answer(dbfile):
    from src import db, cover

    with db.connect() as c:
        t = c.execute("SELECT id FROM technicians LIMIT 1").fetchone()
    with db.txn() as c:
        c.execute("""INSERT INTO technician_certs (technician_id,cert)
                     VALUES (?, 'EPA608-UNIVERSAL')""", (t["id"],))

    out = cover.can_work_on(t["id"], "reach-in freezer", refrigerant="R-290")
    assert out["flammable"] is True
    assert "flammable" in out["say"]


# When the customer can take somebody.


def test_a_site_that_told_us_nothing_is_treated_as_available(dbfile):
    """Assuming a restaurant is shut on the strength of no evidence is worse
    than offering a window they can decline."""
    from src import db, cover

    with db.connect() as c:
        s = c.execute("SELECT id FROM sites LIMIT 1").fetchone()

    assert cover.suits_customer(s["id"], datetime(2026, 8, 26, 14, 0)) is True


def test_a_window_they_ruled_out_is_not_offered(dbfile):
    """A slot across a lunch service gets refused, or worse accepted and
    missed, which spends the truck roll and the relationship at once."""
    from src import db, cover

    with db.connect() as c:
        s = c.execute("SELECT id FROM sites LIMIT 1").fetchone()

    # mornings only, before service
    cover.record_availability(s["id"], from_min=8 * 60, to_min=11 * 60,
                              note="lunch service from midday")

    assert cover.suits_customer(s["id"], datetime(2026, 8, 26, 9, 30)) is True
    assert cover.suits_customer(s["id"], datetime(2026, 8, 26, 13, 0)) is False


def test_a_window_can_be_one_weekday(dbfile):
    from src import db, cover

    with db.connect() as c:
        s = c.execute("SELECT id FROM sites LIMIT 1").fetchone()

    cover.record_availability(s["id"], from_min=9 * 60, to_min=17 * 60,
                              weekday=2, note="Wednesdays are quiet")

    assert cover.suits_customer(s["id"], datetime(2026, 8, 26, 10, 0)) is True
    assert cover.suits_customer(s["id"], datetime(2026, 8, 27, 10, 0)) is False


def test_a_backwards_window_is_refused(dbfile):
    from src import db, cover

    with db.connect() as c:
        s = c.execute("SELECT id FROM sites LIMIT 1").fetchone()

    assert cover.record_availability(s["id"], from_min=17 * 60,
                                     to_min=9 * 60)["ok"] is False


def test_the_desk_is_told_to_ask_first(dbfile):
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    assert "ASK WHEN THEY CAN TAKE SOMEBODY before asking for a slot" in rules


def test_the_scheduler_says_why_a_slot_was_ruled_out(dbfile):
    """Telling somebody nobody is free, when really the only free slot was
    inside the hours they told us not to come, teaches them we were not
    listening."""
    import inspect

    from src import scheduling

    src = inspect.getsource(scheduling.next_available_slot)
    assert "suits_customer" in src
    assert "can_work_on" in src
    assert "ruled_out" in src
