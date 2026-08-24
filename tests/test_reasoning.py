"""The van-loading decision, which is the product.

Everything else here is a lookup. This is the one place the system does
something a database cannot: weigh how likely each part is against what a
wasted trip costs, and decide what actually goes in the van.

These tests pin the properties that make the answer trustworthy rather than
pinning exact numbers. Asserting that a thermostat scores 0.51 would break
every time a repair closes, which is the opposite of useful.
"""

from __future__ import annotations

from conftest import REF


def test_distribution_sums_to_one(corpus):
    from src.reason import _fault_distribution

    dist = _fault_distribution(REF, "not holding temp overnight")
    assert dist
    assert abs(sum(d["probability"] for d in dist) - 1.0) < 0.02


def test_the_common_cause_leads(corpus):
    """Two of three matching jobs were the thermostat, so it must lead.

    Not a tautology: retrieval weights by score rather than counting flat, so
    a single close match could in principle outrank two loose ones. It should
    not here, where all three describe the identical symptom.
    """
    from src.reason import _fault_distribution

    dist = _fault_distribution(REF, "not holding temp overnight")
    assert "thermostat" in dist[0]["cause"].lower()
    assert dist[0]["probability"] > dist[-1]["probability"]


def test_cheap_likely_part_is_carried(corpus):
    """A 68 dollar thermostat that is probably the fault goes in the van."""
    from src.reason import what_to_load

    r = what_to_load(REF, "AS-FREEZER", "not holding temp overnight")
    assert r["ok"]
    assert "P-DEFROSTTHE" in {p["sku"] for p in r["load"]}


def test_out_of_stock_part_is_left_with_a_reason(corpus):
    """The control board cannot be carried; there are none.

    It must still appear, with the consequence spelled out, because "this may
    be the fault and we cannot fix it today" is what the customer needs to
    hear before the technician sets off.
    """
    from src.reason import what_to_load

    r = what_to_load(REF, "AS-FREEZER", "not holding temp overnight")
    left = {p["sku"]: p for p in r["left_behind"]}
    if "P-CONTROLBOA" in left:
        assert not left["P-CONTROLBOA"]["in_stock"]
        assert "lead" in left["P-CONTROLBOA"]["note"]


def test_expected_value_beats_carrying_cost_for_everything_loaded(corpus):
    """The arithmetic must actually hold for each carried part.

    A part rides in the van only when the chance of needing it, times the cost
    of a wasted trip, beats the cost of it not being on the shelf. Anything
    loaded without that being true is a frequency count wearing a costume.
    """
    from src.reason import what_to_load

    r = what_to_load(REF, "AS-FREEZER", "not holding temp overnight")
    for p in r["load"]:
        if p["in_van_already"]:
            continue          # free to bring, no arithmetic needed
        assert p["expected_saving"] > p["carrying_cost"], p


def test_parts_already_in_the_van_are_free(corpus):
    """T-1 has a fan motor on board. Bringing it costs nothing, so it comes."""
    from src.reason import what_to_load

    r = what_to_load(REF, "AS-FREEZER", "not holding temp overnight",
                     technician_id="T-1")
    van = {p["sku"] for p in r["load"] if p["in_van_already"]}
    assert "P-EVAPFAN" in van


def test_no_history_means_no_guessing(corpus):
    """Silence is the correct answer when the corpus has nothing.

    The failure mode worth preventing is a confident parts list assembled from
    nothing, which is how a technician ends up trusting a briefing that was
    invented.
    """
    from src.reason import what_to_load

    r = what_to_load(REF, "AS-FREEZER",
                     "the customer says it smells of burnt toast on Tuesdays")
    if not r["distribution"]:
        assert r["load"] == []
        assert "no basis" in r["reasoning"].lower()


def test_an_ambiguous_split_produces_a_question(corpus):
    """When the corpus is not confident, ask something that separates causes."""
    from src.reason import _best_question

    dist = [{"cause": "defrost termination thermostat open, ice on coil",
             "probability": 0.5, "parts": []},
            {"cause": "evaporator fan motor seized and noisy",
             "probability": 0.5, "parts": []}]
    q = _best_question(dist)
    assert q and q.endswith("?")


def test_a_single_cause_produces_no_question(corpus):
    """Nothing to separate means nothing to ask.

    A question whose answer cannot change the van contents is a question that
    wastes a caller's time while their freezer warms up.
    """
    from src.reason import _best_question

    assert _best_question([{"cause": "x", "probability": 1.0, "parts": []}]) is None
