"""Customers who come to us, and the ones we must never ask to.

The rule is the feature. A restaurant with nine machines is not carrying a
walk-in cooler to a trade counter, and offering it does more damage than
saying nothing: it tells them we never looked at their account, which is the
exact opposite of what this desk is for.

Everything here is additive. `appointments` and `stock_locations` are not
touched, because their CHECK constraints forbid the new values and a counter
booking has no technician anyway, which would break a NOT NULL. Those tables
work; a booking that is genuinely a different thing gets its own.
"""

from __future__ import annotations

from conftest import REF


def _branch(db, bid="B-TEST", counter=1, lat=41.51, lon=-90.52,
            opens=480, closes=1020, days="0,1,2,3,4", dealer=REF):
    with db.txn() as c:
        c.execute(
            """INSERT INTO branches
               (id,dealer_id,label,address,lat,lon,phone_e164,
                has_counter,opens_min,closes_min,open_days)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (bid, dealer, f"{bid} counter", "1 Test St", lat, lon,
             "+13095550190", counter, opens, closes, days))
    return bid


def _account(db, aid, kind, terms, machines=1, site="S-1"):
    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,kind,name,trade_terms,dealer_id) "
                  "VALUES (?,?,?,?,?)", (aid, kind, aid, terms, REF))
        c.execute("INSERT INTO sites (id,account_id,label,lat,lon) "
                  "VALUES (?,?,?,?,?)",
                  (f"{site}-{aid}", aid, "site", 41.52, -90.57))
        for i in range(machines):
            c.execute(
                """INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                   VALUES (?,?,?,?,?)""",
                (f"AS-{aid}-{i}", f"{site}-{aid}", "Traulsen", "G12010",
                 "reach-in freezer"))
    return aid


# --------------------------------------------------------------------------
# who gets offered the counter
# --------------------------------------------------------------------------

def test_a_residential_customer_with_one_machine_is_offered_it(dbfile):
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file", machines=1)
    r = counter.walk_in_suitable("A-HOME")
    assert r["offer"] is True


def test_a_trade_account_is_never_offered_it(dbfile):
    """The rule. Somebody on credit terms gets a van, not a suggestion."""
    from src import counter, db

    _account(db, "A-TRADE", "business", "net 30", machines=6)
    r = counter.walk_in_suitable("A-TRADE")
    assert r["offer"] is False
    assert "not mention" in r["say"]


def test_card_on_file_is_not_a_trade_account(dbfile):
    """The bug that made the rule refuse everybody.

    Every account in this book has some payment term, so treating any
    trade_terms value as a trade relationship offered the counter to nobody at
    all. "Card on file" means they pay at the point of sale, which describes
    exactly the customer a counter exists for.
    """
    from src import counter, db

    _account(db, "A-CARD", "person", "card on file", machines=1)
    assert counter.walk_in_suitable("A-CARD")["offer"] is True


def test_a_business_with_several_machines_is_not_offered_it(dbfile):
    from src import counter, db

    _account(db, "A-BIG", "business", None, machines=9)
    r = counter.walk_in_suitable("A-BIG")
    assert r["offer"] is False
    assert r["machines"] == 9


def test_an_unknown_account_is_not_offered_it(dbfile):
    from src import counter

    assert counter.walk_in_suitable("A-NOPE")["offer"] is False


# --------------------------------------------------------------------------
# which counter, and when
# --------------------------------------------------------------------------

def test_the_nearest_counter_is_returned_first(dbfile):
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-NEAR", lat=41.52, lon=-90.57)
    _branch(db, "B-FAR", lat=42.60, lon=-91.60)

    r = counter.nearest_branch("S-1-A-HOME", REF)
    assert r["ok"]
    assert r["nearest"]["branch_id"] == "B-NEAR"
    assert r["branches"][0]["distance_mi"] <= r["branches"][-1]["distance_mi"]


def test_a_site_with_no_counter_is_never_offered(dbfile):
    """A warehouse loading bay is not a trade counter."""
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-DEPOT", counter=0, lat=41.52, lon=-90.57)

    r = counter.nearest_branch("S-1-A-HOME", REF)
    assert not r["ok"]


def test_a_distant_counter_is_flagged_rather_than_hidden(dbfile):
    """Returned with the distance, so the agent can decline to push it."""
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-MILES", lat=42.60, lon=-91.60)

    r = counter.nearest_branch("S-1-A-HOME", REF)
    assert r["ok"]
    assert r["nearest"]["too_far"] is True
    assert "Do not push" in r["advice"]


def test_counter_slots_are_opening_hours_not_reservations(dbfile):
    """Nobody is booked out at a counter, so promising a slot would be a lie."""
    from src import counter, db

    _branch(db, "B-TEST")
    r = counter.counter_slots("B-TEST", days=7)
    assert r["ok"]
    assert r["open_windows"]
    assert "not reserved slots" in r["note"]
    assert all(w["day"] not in ("Saturday", "Sunday") for w in r["open_windows"])


# --------------------------------------------------------------------------
# booking
# --------------------------------------------------------------------------

def test_a_booking_on_a_closed_day_is_refused(dbfile):
    """A customer who drives to a locked door is worse off than one told nothing."""
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-TEST", days="0,1,2,3,4")

    r = counter.book_counter_slot("B-TEST", "A-HOME", "2026-08-29T10:00")
    assert not r["ok"]
    assert "closed on Saturdays" in r["why"]


def test_a_booking_outside_opening_hours_is_refused(dbfile):
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-TEST", opens=480, closes=1020)

    r = counter.book_counter_slot("B-TEST", "A-HOME", "2026-08-31T21:00")
    assert not r["ok"]
    assert "open" in r["why"]


def test_a_booking_at_a_site_without_a_counter_is_refused(dbfile):
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-DEPOT", counter=0)

    r = counter.book_counter_slot("B-DEPOT", "A-HOME", "2026-08-31T10:00")
    assert not r["ok"]
    assert "no trade counter" in r["why"]


def test_a_good_booking_is_recorded(dbfile):
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-TEST")

    r = counter.book_counter_slot(
        "B-TEST", "A-HOME", "2026-08-31T10:00",
        reason="bringing the under-counter fridge in")
    assert r["ok"]

    with db.connect() as c:
        row = c.execute("SELECT * FROM counter_bookings WHERE id=?",
                        (r["booking_id"],)).fetchone()
    assert row["status"] == "booked"
    assert row["branch_id"] == "B-TEST"
    assert row["reason"] == "bringing the under-counter fridge in"


def test_a_counter_booking_blocks_nobodys_diary(dbfile):
    """Nobody drives, so no technician is assigned and no slot is consumed.

    This is why it is not an appointment row. Booking one against the
    dispatch diary would quietly make a technician unavailable for a job that
    involves no technician at all.
    """
    from src import counter, db

    _account(db, "A-HOME", "person", "card on file")
    _branch(db, "B-TEST")

    with db.connect() as c:
        before = c.execute("SELECT COUNT(*) n FROM appointments").fetchone()["n"]

    counter.book_counter_slot("B-TEST", "A-HOME", "2026-08-31T10:00")

    with db.connect() as c:
        after = c.execute("SELECT COUNT(*) n FROM appointments").fetchone()["n"]
    assert after == before


def test_the_existing_tables_were_not_widened(dbfile):
    """Nothing already working was altered to make room for this.

    Both obvious homes refuse the new values, and rebuilding a table that
    works in order to add a row type is a bad trade.
    """
    from src import db

    with db.connect() as c:
        appt = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='appointments'").fetchone()[0]
        loc = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='stock_locations'").fetchone()[0]

    # the appointment kinds are exactly what they were
    assert "walk_in" not in appt and "counter" not in appt
    for kind in ("visit", "travel", "leave", "training", "hold"):
        assert f"'{kind}'" in appt

    # and stock locations never grew a 'store' kind
    assert "store" not in loc
    for kind in ("warehouse", "van", "consignment"):
        assert f"'{kind}'" in loc


def test_the_counter_is_a_scheduling_tool(dbfile):
    """It belongs with the other 'when can somebody be seen' decisions."""
    from src import agents

    names = {getattr(t, "__name__", getattr(t, "name", ""))
             for t in agents.scheduling_agent.tools}
    for tool in ("walk_in_suitable", "nearest_branch", "book_counter_slot"):
        assert tool in names
