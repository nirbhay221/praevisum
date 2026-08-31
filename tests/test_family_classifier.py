"""Classifying a machine by its catalogue name, without matching inside words.

THE BUG

The fallback was `if "ice" in low: return "ice machine"`. Three real product
types on this catalogue contain those letters inside another word:

    Multifunction Devices (MFD)           dev-ICE-s
    Service Over Counter                  serv-ICE
    Voice over Internet Protocol (VoIP)   vo-ICE

Two of them are IT equipment, on a desk that serves an IT business as well as
a refrigeration one.

WHY IT IS NOT A LABEL PROBLEM

Family drives dispatch. EPA 608 certification is required to open a
refrigerant circuit and cover.py refuses anybody without it. A printer
classified as an ice machine means the desk either goes looking for a
refrigeration engineer to attend a photocopier, or tells the customer nobody
is qualified to fix it.

This is the same shape as the `"no" in "now"` bug that reached a live call.
"""

from __future__ import annotations

import inspect

import pytest


def _classifier():
    from src import caller

    for name in dir(caller):
        fn = getattr(caller, name)
        if callable(fn) and not inspect.isclass(fn):
            try:
                if "product_type" in str(inspect.signature(fn)):
                    return fn
            except (TypeError, ValueError):
                continue
    raise AssertionError("the classifier moved")


@pytest.mark.parametrize("product_type", [
    "Multifunction Devices (MFD)",
    "Voice over Internet Protocol (VoIP)",
    "Laptop",
    "Desktop Workstation",
])
def test_it_equipment_is_never_called_refrigeration(dbfile, product_type):
    """Each of these would have been dispatched as an ice machine, and then
    refused for want of an EPA 608 certificate."""
    assert _classifier()(product_type) != "ice machine"


@pytest.mark.parametrize("product_type,family", [
    ("Ice Maker", "ice machine"),
    ("Cube Ice Machine", "ice machine"),
    ("Vertical Solid Door Freezer", "reach-in freezer"),
    ("Reach-In Refrigerator", "reach-in cooler"),
])
def test_real_refrigeration_still_classifies(dbfile, product_type, family):
    """Fixing a false positive must not create false negatives: an unclassified
    machine reads to the customer as nobody here can fix your freezer."""
    assert _classifier()(product_type) == family


def test_a_serve_over_counter_is_a_display_case(dbfile):
    """It matched on serv-ICE and came back an ice machine. It is a display
    case, and it does hold a refrigerant circuit, so getting it wrong in
    either direction sends the wrong person."""
    assert _classifier()("Service Over Counter") == "display cooler"


def test_it_still_refuses_to_guess(dbfile):
    """An unmapped type is better recorded as nothing. A family nobody is
    skilled on reads to a customer as nobody here can fix your freezer."""
    assert _classifier()("Novelty Lava Lamp") == ""
    assert _classifier()("") == ""
