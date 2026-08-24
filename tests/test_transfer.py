"""Faults belong to components, not to model badges.

For a long time this system answered "we have never supplied one of those" for
32,730 of the 32,767 machines in the catalogue, and that was treated as a hard
ceiling on what a dealer's own history could tell you.

It was the wrong question. A defrost termination thermostat fits 49 different
manufacturers. In this book the same part was replaced across 17 makes and 23
models, and one symptom turned up across 15 makes. The federal catalogue
already records what kind of defrost every certified machine has and what
refrigerant it runs, which are the things that decide how a fridge fails.

Matching on that instead of the badge takes the reach from 37 models to 21,533,
using data that was already loaded and only ever used to spell model numbers.

What must NOT transfer is part numbers. A fault seen on another make is a hint.
A part number from another make is a technician holding something that does not
fit, which is worse than carrying nothing because it was believed.
"""

from __future__ import annotations

from conftest import REF


def _catalogue(db, brand, model, product_type="Vertical Solid Door Freezer",
               defrost="Automatic", refrigerant="R-290"):
    with db.txn() as c:
        c.execute(
            """INSERT OR IGNORE INTO equipment
               (source,dataset,category,brand,model_number,product_type,
                defrost_type,refrigerant,daily_kwh,site_visit)
               VALUES ('energystar','d','refrigeration',?,?,?,?,?,2.0,1)""",
            (brand, model, product_type, defrost, refrigerant))


def test_a_machine_never_serviced_is_not_a_mystery(corpus):
    """The whole point. An unfamiliar badge is not an absence of knowledge."""
    from src import db, ops

    _catalogue(db, "Traulsen", "G12010")          # what we do service
    _catalogue(db, "NeverHeardOf", "XYZ-1")       # same design, never touched

    r = ops.what_we_know_about("NeverHeardOf", "XYZ-1")
    assert r["known"] is False, "we must not claim experience of that badge"
    assert r.get("known_by_profile") is True
    assert r["comparable_models"] >= 1
    assert r["what_goes_wrong"]


def test_it_never_claims_to_know_the_badge(corpus):
    """Honesty is the feature. Comparable is not the same as experience."""
    from src import db, ops

    _catalogue(db, "Traulsen", "G12010")
    _catalogue(db, "NeverHeardOf", "XYZ-1")

    r = ops.what_we_know_about("NeverHeardOf", "XYZ-1")
    assert "never supplied that exact model" in r["say"]
    assert "comparable machines, not" in r["caveat"]


def test_a_machine_of_a_different_design_gets_nothing(corpus):
    """A dishwasher is not a freezer, whatever the retrieval score says."""
    from src import db, ops

    _catalogue(db, "Traulsen", "G12010")
    _catalogue(db, "OtherThing", "DW-9", product_type="Commercial Dishwasher",
               defrost="")

    r = ops.what_we_know_about("OtherThing", "DW-9")
    assert not r.get("known_by_profile")


def test_a_model_we_do_service_still_answers_from_itself(corpus):
    """Transfer is a fallback, never a replacement for real experience."""
    from src import db, ops

    _catalogue(db, "Traulsen", "G12010")
    with db.txn() as c:
        for i in range(4):
            c.execute(
                """INSERT INTO repairs (id,dealer_id,asset_id,manufacturer,
                   model_number,found_cause,parts_consumed,closed_on)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (f"RT-{i}", REF, "AS-FREEZER", "Traulsen", "G12010",
                 "door gasket perished", "P-DOORGASKET", "2026-05-01"))

    r = ops.what_we_know_about("Traulsen", "G12010")
    assert r["known"] is True
    assert "known_by_profile" not in r


# --------------------------------------------------------------------------
# the van load, where the guard matters most
# --------------------------------------------------------------------------

def test_the_distribution_says_where_its_evidence_came_from(corpus):
    """A briefing built on comparable machines must admit it."""
    from src.reason import _fault_distribution

    dist = _fault_distribution(REF, "not holding temp overnight",
                               "Traulsen", "reach-in freezer", "G12010")
    assert dist
    assert all("evidence_from" in d for d in dist)


def test_parts_still_never_cross_makes(corpus):
    """Recall crosses brands. Part numbers do not, ever.

    This is the guard that stopped a Whirlpool board being sent to a Traulsen,
    and widening the fault evidence must not widen the parts with it.
    """
    from src import db
    from src.reason import what_to_load

    r = what_to_load(REF, "AS-FREEZER", "not holding temp overnight")
    assert r["ok"]

    with db.connect() as c:
        allowed = {row["sku"] for row in c.execute(
            """SELECT p.sku FROM parts p JOIN fitments f ON f.sku=p.sku
               JOIN assets a ON a.manufacturer=f.manufacturer
                            AND a.model_number LIKE f.model_pattern
               WHERE a.id='AS-FREEZER'""")}

    for part in r["load"] + r["left_behind"]:
        assert part["sku"] in allowed, \
            f"{part['sku']} does not fit this machine but was suggested"


def test_profile_lookup_handles_an_unknown_make(corpus):
    """Never raise on a machine the catalogue has never heard of."""
    from src.reason import _models_sharing, _profile_of

    assert _profile_of("CompletelyMadeUp", "ZZZ") is None
    assert _models_sharing(None) == set()
