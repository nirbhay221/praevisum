"""Road distance, and behaving properly when there is no key for it.

WHY THIS EXISTS

domain/geo.py measures a great-circle line and converts it at a flat 32 mph.
This dealer's territory is the Quad Cities, which the Mississippi runs
straight through, and engineers are based on both banks. Measured on the live
book, a site on River Dr in Rock Island reads 2.3 miles from an engineer in
Rock Island and 2.7 miles from one in Davenport, a minute apart. One of them
has to drive to a bridge.

WHAT THESE PIN

The two things that decide whether this is safe to ship:

  with no key, nothing calls anything and the answers are exactly what they
  were before

  with a key, a pair already paid for is never bought twice, because Compute
  Route Matrix bills per element and the free tier is 10,000 a month
"""

from __future__ import annotations

import pytest


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)


@pytest.fixture
def a_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key-not-real")


# Two points either side of the Mississippi in Davenport / Rock Island.
IOWA = (41.5236, -90.5776)
ILLINOIS = (41.5095, -90.5787)


def test_with_no_key_it_never_calls_anything(dbfile, no_key, monkeypatch):
    """The whole reason this is safe to ship without a billing account."""
    from src import roads

    called = []
    monkeypatch.setattr(roads, "_ask_google",
                        lambda o, d: called.append(1) or {})

    assert roads.configured() is False
    legs = roads.legs_to(ILLINOIS, [IOWA])
    assert not called, "it reached for the network with no key configured"
    assert legs[0]["source"] == "straight-line"
    assert legs[0]["miles"] > 0


def test_the_fallback_is_the_old_answer_exactly(dbfile, no_key):
    """Falling back must not quietly become a different estimate."""
    from src import roads
    from src.domain.geo import drive_minutes, miles

    leg = roads.legs_to(ILLINOIS, [IOWA])[0]
    want = miles(IOWA[0], IOWA[1], ILLINOIS[0], ILLINOIS[1])
    assert leg["miles"] == want
    assert leg["minutes"] == drive_minutes(want)


def test_a_leg_is_bought_once_and_then_cached(dbfile, a_key, monkeypatch):
    """Billing is per element. The same engineer-to-site pair recurs every
    time that restaurant rings, and paying twice buys a road that has not
    moved."""
    from src import roads

    calls = []

    def fake(origins, destinations):
        calls.append(len(origins) * len(destinations))
        return {(0, 0): (7.4, 16)}

    monkeypatch.setattr(roads, "_ask_google", fake)

    first = roads.legs_to(ILLINOIS, [IOWA])[0]
    assert first["source"] == "road"
    assert first["minutes"] == 16
    assert calls == [1]

    second = roads.legs_to(ILLINOIS, [IOWA])[0]
    assert second["source"] == "road (cached)"
    assert second["minutes"] == 16
    assert calls == [1], "the same leg was bought twice"


def test_only_the_unknown_pairs_are_bought(dbfile, a_key, monkeypatch):
    """Half a van fleet already cached must not make us pay for all of it."""
    from src import roads

    monkeypatch.setattr(roads, "_ask_google",
                        lambda o, d: {(0, 0): (5.0, 12)})
    roads.legs_to(ILLINOIS, [IOWA])          # now cached

    asked = []

    def fake(origins, destinations):
        asked.append(len(origins))
        return {(i, 0): (9.0, 20) for i in range(len(origins))}

    monkeypatch.setattr(roads, "_ask_google", fake)
    other = (41.5400, -90.5100)
    legs = roads.legs_to(ILLINOIS, [IOWA, other])

    assert asked == [1], "a cached pair was included in the paid matrix"
    assert legs[0]["source"] == "road (cached)"
    assert legs[1]["source"] == "road"


def test_a_refusal_falls_back_rather_than_failing(dbfile, a_key, monkeypatch):
    """A routing service being down makes the ordering less precise. It must
    not stop a van being sent."""
    from src import roads

    monkeypatch.setattr(roads, "_ask_google", lambda o, d: {})

    leg = roads.legs_to(ILLINOIS, [IOWA])[0]
    assert leg["source"] == "straight-line"
    assert leg["minutes"] > 0


def test_a_runaway_matrix_is_refused_not_billed(dbfile, a_key, monkeypatch):
    """Origins times destinations is the bill. A bug that builds every
    technician against every site would spend the month in one request and
    look like a slow function rather than a charge."""
    from src import roads

    called = []
    monkeypatch.setattr(roads, "_ask_google",
                        lambda o, d: called.append(len(o)) or {})

    many = [(41.5 + i / 1000, -90.5) for i in range(roads.MAX_ELEMENTS + 1)]
    legs = roads.legs_to(ILLINOIS, many)

    assert not called, "a matrix over the cap was sent anyway"
    assert all(l["source"] == "straight-line" for l in legs)


def test_a_technician_with_no_position_is_last_not_nearest(dbfile, no_key):
    """Missing coordinates must never read as distance zero."""
    from src import roads

    legs = roads.legs_to(ILLINOIS, [None, IOWA])
    assert legs[0]["minutes"] == 999
    assert legs[0]["miles"] is None
    assert legs[1]["minutes"] < 999


def test_dispatch_uses_it_and_still_puts_certification_first(dbfile, a_key,
                                                             monkeypatch):
    """Road distance changes the ORDER of who may go. It must never change
    WHO may go."""
    from src import db, hazard, roads

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-R','D-REF','business','Rivertown','2020-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label,lat,lon) "
                  "VALUES ('S-R','A-R','shop',?,?)", ILLINOIS)
        c.execute("INSERT INTO technicians (id,dealer_id,name,lat,lon,active) "
                  "VALUES ('T-CLOSE','D-REF','Close Nocert',?,?,1)", ILLINOIS)
        c.execute("INSERT INTO technicians (id,dealer_id,name,lat,lon,active) "
                  "VALUES ('T-CERT','D-REF','Far Certified',?,?,1)", IOWA)
        c.execute("INSERT INTO technician_certs (technician_id,cert,expires_on)"
                  " VALUES ('T-CERT','EPA608-I','2099-01-01')")

    seen = []

    def fake(origins, destinations):
        seen.append(len(origins))
        return {(i, 0): (3.0, 9) for i in range(len(origins))}

    monkeypatch.setattr(roads, "_ask_google", fake)

    with db.connect() as c:
        site = dict(c.execute("SELECT id,lat,lon FROM sites WHERE id='S-R'"
                              ).fetchone())
        picked = hazard._nearest_engineer(c, "D-REF", site, "display cooler",
                                          "R-290")

    assert picked["name"] == "Far Certified"
    assert seen == [1], (
        "the uncertified engineer was measured, which is an element paid for "
        "on somebody who was never eligible")


def test_the_comparison_can_be_shown_rather_than_asserted(dbfile, a_key,
                                                          monkeypatch):
    """The point of the feature has to be visible on a screen."""
    from src import roads

    monkeypatch.setattr(roads, "_ask_google", lambda o, d: {(0, 0): (8.1, 19)})

    row = roads.compare(ILLINOIS, [IOWA])[0]
    assert row["road_minutes"] == 19
    assert row["straight_minutes"] < row["road_minutes"]
    assert row["minutes_understated_by"] == 19 - row["straight_minutes"]
