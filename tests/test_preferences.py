"""A customer can say what they want, and we can actually look for it.

`recommend_equipment` filtered on family and budget. Nothing else. The
EnergyStar catalogue carries the real door type, capacity, refrigerant,
defrost type and daily running cost for 88,544 machines and not one of those
was searchable, so somebody who said "glass door, about twenty cubic feet, and
it cannot be propane because we have no ventilation" got whatever the ranking
happened to put first and had to be talked out of it afterwards.

The refrigerant one is not a preference. R-290 is flammable and charge
limited, which is the same fact this system already uses to decide who is
allowed to service one. It could not take it into account when SELLING one.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def catalogue(dbfile):
    """A handful of real-shaped catalogue rows, including a recalled brand.

    THE DATASET NAME IS PART OF THE SHAPE, not a label. ENERGY STAR publishes
    commercial equipment in kWh per DAY and residential in kWh per YEAR, into
    the same column, so the catalogue filter has to restrict itself to the
    commercial datasets or it compares a daily figure against an annual one.
    These five rows are the sort that live in "Commercial Refrigerators and
    Freezers", and saying so here is what keeps the fixture honest: with a
    made-up dataset of "t" this file was testing a query production never runs.
    """
    from src import db

    with db.txn() as c:
        c.executemany(
            """INSERT INTO equipment
               (source,dataset,category,brand,model_number,product_type,
                capacity,daily_kwh,refrigerant,defrost_type,site_visit,model_norm)
               VALUES ('energystar','Commercial Refrigerators and Freezers',
                       'refrigeration',?,?,?,?,?,?,?,1,?)""",
            [("Novum", "601R", "Vertical Transparent Door Refrigerator",
              "20.2", "1.11", "R-290", "Automatic", "601R"),
             ("Traulsen", "CLUC-60R-SD", "Vertical Transparent Door Refrigerator",
              "15.7", "1.17", "R-134a", "Automatic", "CLUC60RSD"),
             ("IDW", "TEQ-77", "Vertical Transparent Door Refrigerator",
              "2.3", "0.26", "R-600a", "Automatic", "TEQ77"),
             ("Delfield", "4524NP", "Vertical Solid Door Freezer",
              "24.0", "1.74", "R-449A", "Automatic", "4524NP"),
             ("Kelvinator Commercial", "KCCF073WS", "Vertical Solid Door Freezer",
              "7.0", "0.89", "R-600a", "Automatic", "KCCF073WS")])

        c.execute("""INSERT INTO recalls (brands,title,hazard,recall_date,url)
                     VALUES ('Kelvinator Commercial Freezers',
                             'Freezers recalled', 'fire hazard',
                             '2026-02-01','http://example.test')""")


# Numbers that were secretly strings.


def test_a_size_range_actually_filters_by_size(catalogue):
    """capacity is TEXT in the catalogue, so `>= 15` was a STRING comparison
    and "2.3" sorts after "15". Asking for fifteen cubic feet and up returned
    a 2.3 cubic foot countertop."""
    from src import buying

    out = buying.find_equipment(min_cuft=15, max_cuft=30)
    got = {m["model"] for m in out["matches"]}

    assert "TEQ-77" not in got, "2.3 cuft is not between 15 and 30"
    assert "601R" in got


def test_a_running_cost_ceiling_filters_by_number_too(catalogue):
    from src import buying

    out = buying.find_equipment(max_daily_kwh=1.0)
    assert all(float(m["daily_kwh"]) <= 1.0 for m in out["matches"])


# What they asked for.


def test_glass_door_means_glass_door(catalogue):
    from src import buying

    out = buying.find_equipment(door="glass")
    assert out["matches"]
    assert all("Transparent" in m["type"] for m in out["matches"])


def test_solid_door_means_solid_door(catalogue):
    from src import buying

    out = buying.find_equipment(door="solid")
    assert all("Solid" in m["type"] for m in out["matches"])


def test_a_kitchen_that_cannot_take_propane_is_respected(catalogue):
    """Not a preference. R-290 is flammable and charge limited."""
    from src import buying

    out = buying.find_equipment(no_flammable_refrigerant=True)
    got = {m["refrigerant"] for m in out["matches"]}

    assert "R-290" not in got
    assert "R-600a" not in got, "R-600a is isobutane and also flammable"
    assert got, "there are non-flammable options"


def test_a_named_refrigerant_is_matched(catalogue):
    from src import buying

    out = buying.find_equipment(refrigerant="R-134a")
    assert [m["model"] for m in out["matches"]] == ["CLUC-60R-SD"]


def test_nothing_matching_says_so_instead_of_dropping_a_requirement(catalogue):
    """Quietly relaxing a condition and offering something that does not do
    what they asked for is worse than saying we have nothing."""
    from src import buying

    out = buying.find_equipment(door="glass", max_daily_kwh=0.01)
    assert out["matches"] == []
    assert "which condition is the tight one" in out["say"]
    assert out["asked_for"]["door"] == "glass"


# Our own evidence still applies on top.


def test_a_recalled_machine_is_marked_and_pushed_last(catalogue):
    """A preference filter on its own is a catalogue search, and a catalogue
    search will happily offer a recalled machine because it has the right
    door. _recall_for needs the FAMILY to confirm a recall is about this kind
    of machine, and calling it with an empty one silently matched nothing."""
    from src import buying

    out = buying.find_equipment(family="reach-in freezer")
    by_model = {m["model"]: m for m in out["matches"]}

    assert by_model["KCCF073WS"]["recalled"] is True
    assert "SAFETY RECALL" in by_model["KCCF073WS"]["our_experience"]
    assert out["matches"][-1]["model"] == "KCCF073WS", "recalled goes last"


def test_it_says_what_we_know_rather_than_only_specs(catalogue):
    from src import buying

    out = buying.find_equipment(door="glass")
    assert all(m["our_experience"] for m in out["matches"])


# Wiring.


def test_the_desk_can_actually_call_it(dbfile):
    from src import agents

    names = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "find_equipment" in names

    advice = {getattr(t, "__name__", "") for t in agents.advice_agent.tools}
    assert "find_equipment" in advice


def test_the_desk_is_told_preferences_are_searchable(dbfile):
    from src import agents

    r = " ".join(agents.DESK_RULES.split())
    assert "A PREFERENCE IS SEARCHABLE" in r
    assert "REFRIGERANT IS NOT A PREFERENCE" in r
