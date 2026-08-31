"""The forecast, as an operational input rather than small talk.

WHY A REFRIGERATION DESK READS THE WEATHER

A condenser rejects heat into the air around it, so the hotter that air is,
the harder it works to hold the same box temperature. A machine that is
marginal at 70F is often fine until the first properly hot afternoon. Every
service manager in the trade knows the phone rings on the hot days; the desk
did not know it in time to do anything.

So a machine already showing symptoms is a different proposition on the
Tuesday before a 92F weekend than it is in October, and outreach now says so:
"that is the reason this call is happening this week rather than at some
point".

WHAT IS TESTED HERE AND WHAT IS NOT

Not the National Weather Service. Their forecast is theirs and it changes
hourly, so asserting on the numbers would be a test of the weather. What is
asserted is the JUDGEMENT: which temperatures count as hard on a machine,
that a failure to reach the service is survived quietly, and above all that
the desk never tells a customer the weather will break their equipment.

That last one is the point. Heat is a stressor, not a cause. "It is hot so
your freezer will fail" is exactly the confident nonsense this project exists
to avoid, and it would be an easy sentence for a model to produce.
"""

from __future__ import annotations

import pytest


def test_ninety_is_where_it_stops_being_ordinary(dbfile):
    """US commercial refrigeration is generally rated to hold at 90F ambient,
    so a forecast at or above it is outside design conditions. The threshold
    is defensible rather than chosen for effect."""
    from src.weather import HARD_ON_MACHINES_F, WARM_F

    assert HARD_ON_MACHINES_F == 90
    assert WARM_F < HARD_ON_MACHINES_F


def test_no_coordinates_is_answered_plainly(dbfile):
    from src.weather import forecast

    out = forecast(0, 0)
    assert out["ok"] is False
    assert "coordinates" in out["why"]


def test_it_never_says_the_weather_will_break_the_machine(dbfile, monkeypatch):
    """The sentence that must never be produced."""
    from src import weather

    monkeypatch.setattr(weather, "forecast", lambda lat, lon: {
        "ok": True, "peak_f": 96, "hard_days": ["Sunday", "Monday"],
        "periods": [], "source": "test"})

    out = weather.pressure_on_machines(41.5, -90.5)
    assert out["level"] == "high"
    assert "stressor, not a cause" in out["say"]
    assert "Never tell a customer the heat will break" in out["say"]


@pytest.mark.parametrize("peak,hard,expected", [
    (96, ["Sunday"], "high"),
    (86, [], "raised"),
    (68, [], "normal"),
])
def test_how_hard_the_week_is(dbfile, monkeypatch, peak, hard, expected):
    from src import weather

    monkeypatch.setattr(weather, "forecast", lambda lat, lon: {
        "ok": True, "peak_f": peak, "hard_days": hard,
        "periods": [], "source": "test"})

    assert weather.pressure_on_machines(41.5, -90.5)["level"] == expected


def test_the_service_being_down_is_survived(dbfile, monkeypatch):
    """A desk that cannot reach a forecast carries on without one. The
    weather is a reason to ring sooner, never a dependency."""
    from src import weather

    monkeypatch.setattr(weather, "_get", lambda url: None)
    weather._CACHE.clear()

    out = weather.forecast(41.5, -90.5)
    assert out["ok"] is False
    assert "did not answer" in out["why"]


def test_a_site_we_hold_coordinates_for_can_be_asked(dbfile):
    from src import db
    from src.weather import where_they_are

    with db.txn() as c:
        c.execute("UPDATE sites SET lat=41.5236, lon=-90.5776 "
                  "WHERE id = (SELECT id FROM sites LIMIT 1)")
    with db.connect() as c:
        sid = c.execute("SELECT id FROM sites LIMIT 1").fetchone()["id"]

    lat, lon = where_they_are(sid)
    assert lat and lon


def test_a_site_without_coordinates_returns_nothing_rather_than_guessing(dbfile):
    from src import db
    from src.weather import where_they_are

    with db.txn() as c:
        c.execute("UPDATE sites SET lat=NULL, lon=NULL")
    with db.connect() as c:
        sid = c.execute("SELECT id FROM sites LIMIT 1").fetchone()["id"]

    assert where_they_are(sid) == (0.0, 0.0)


def test_a_prediction_says_why_this_week(dbfile, monkeypatch):
    """The forecast earns its place by answering the question a customer
    actually asks when an engineer rings them unprompted."""
    from src import outreach, weather

    monkeypatch.setattr(weather, "pressure_on_machines", lambda lat, lon: {
        "ok": True, "level": "high", "peak_f": 92,
        "hard_days": ["Monday"], "why": "1 day at or above 90F (Monday)",
        "source": "test", "say": "x"})
    monkeypatch.setattr(weather, "where_they_are", lambda site: (41.5, -90.5))

    found = outreach.sweep_predictions("D-REF")
    for p in found:
        if p.get("weather"):
            assert "why now" in p["say"]
            assert "Do NOT say the weather will break it" in p["say"]
            break
