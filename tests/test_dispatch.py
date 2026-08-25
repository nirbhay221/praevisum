"""Did the parts actually leave the building, and was the desk right.

Two loops that were open.

The first is the sentence this project opens with: a technician drives an hour
and does not have the part. The desk works the part out, holds it, and texts a
briefing. Nothing then checked that anybody picked it up. `reservations` had
`reserved_at` and `released_at`, which is a claim on stock rather than a fact
about a van.

The second is that the desk says "44% evaporator fan motor" and a technician
later writes what it really was, and those two facts had never been compared,
because the prediction went into a dict and vanished.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


# The technician the shared fixture already seeds. Inventing one with
# INSERT OR IGNORE silently kept the existing T-1 and left the phone pointing
# at somebody else, so every lookup by number found nobody.
TECH = "T-1"
PHONE = "+13095551001"


def _visit(db, visit_id="V-1", tech=TECH, skus=("P-EVAPFAN",),
           starts_in_minutes=30, dealer="D-REF"):
    """A booked visit with parts held against it."""
    now = datetime.now()
    starts = (now + timedelta(minutes=starts_in_minutes)).isoformat(timespec="seconds")
    with db.connect() as c:
        site = c.execute("SELECT id, account_id FROM sites LIMIT 1").fetchone()
        asset = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
        loc = c.execute("SELECT id FROM stock_locations LIMIT 1").fetchone()
    if site is None or asset is None or loc is None:
        pytest.skip("fixture is missing a site, asset or stock location")

    with db.txn() as c:
        c.execute("""INSERT INTO work_orders
                     (id,account_id,site_id,asset_id,reported_symptom,status,
                      opened_at,dealer_id)
                     VALUES (?,?,?,?,'not holding','scheduled',?,?)""",
                  (f"WO-{visit_id}", site["account_id"], site["id"], asset["id"],
                   now.isoformat(timespec="seconds"), dealer))
        c.execute("""INSERT INTO visits
                     (id,work_order_id,seq,technician_id,promised_window,promised_at)
                     VALUES (?,?,1,?,?,?)""",
                  (visit_id, f"WO-{visit_id}", tech,
                   starts[:16].replace("T", " "), now.isoformat(timespec="seconds")))
        # promise_slot writes both rows in one transaction, and the real start
        # time lives here rather than on the visit.
        c.execute("""INSERT INTO appointments
                     (id,technician_id,visit_id,starts_at,ends_at,site_id)
                     VALUES (?,?,?,?,?,?)""",
                  (f"AP-{visit_id}", tech, visit_id, starts,
                   (now + timedelta(minutes=starts_in_minutes + 120)
                    ).isoformat(timespec="seconds"), site["id"]))
        for sku in skus:
            c.execute("""INSERT INTO reservations (sku,location_id,visit_id,qty,reserved_at)
                         VALUES (?,?,?,1,?)""",
                      (sku, loc["id"], visit_id, now.isoformat(timespec="seconds")))
    return visit_id


# The confirmation itself.


def test_a_held_part_is_not_a_loaded_part(dbfile):
    """The distinction the whole feature rests on. A reservation stops anybody
    else taking it; it says nothing about whether it is in a van."""
    from src import db, dispatch

    _visit(db)
    waiting = dispatch.wants_confirmation("V-1")
    assert len(waiting) == 1
    assert waiting[0]["sku"] == "P-EVAPFAN"


def test_the_briefing_names_the_parts_it_wants_confirmed(dbfile):
    """"Reply LOADED when you have the two parts" is a question you have to go
    and look to answer. Naming them is one you can answer from memory."""
    from src import db, dispatch

    _visit(db, skus=("P-EVAPFAN", "P-DEFROSTTHE"))
    line = dispatch.ask_line("V-1")
    assert "LOADED" in line
    assert "fan" in line.lower()


def test_confirming_marks_every_part_on_that_visit(dbfile):
    from src import db, dispatch

    _visit(db, skus=("P-EVAPFAN", "P-DEFROSTTHE"))
    out = dispatch.confirm_loaded(PHONE, "loaded")
    assert out["confirmed"] is True
    assert dispatch.wants_confirmation("V-1") == []


def test_a_denial_is_never_read_as_a_confirmation(dbfile):
    """The failure that matters. A reservation wrongly marked picked converts a
    question the desk could still ask into a fact nobody will check again."""
    from src import db, dispatch

    _visit(db)
    for said in ("not loaded", "no, do not have the fan motor",
                 "cannot find it", "the evap fan is missing",
                 "heading out without it"):
        out = dispatch.confirm_loaded(PHONE, said)
        assert out.get("confirmed") is not True, f"read as confirmation: {said}"
    assert dispatch.wants_confirmation("V-1"), "a denial marked the parts loaded"


def test_vague_agreement_is_not_a_confirmation(dbfile):
    """"ok" is a technician acknowledging a text, not saying they have parts."""
    from src import db, dispatch

    _visit(db)
    for said in ("ok", "thanks", "sure", "will do", "seen"):
        assert dispatch.confirm_loaded(PHONE, said).get("confirmed") is not True
    assert dispatch.wants_confirmation("V-1")


def test_the_words_a_technician_would_really_use_all_work(dbfile):
    """They are in a van park with grease on their hands, not filling a form."""
    from src import db, dispatch

    for i, said in enumerate(("loaded", "got them", "on the van",
                              "picked up", "all set")):
        _visit(db, visit_id=f"V-{i}")
        out = dispatch.confirm_loaded(PHONE, said, visit_id=f"V-{i}")
        assert out["confirmed"] is True, f"not understood: {said}"


def test_loaded_is_never_written_into_the_corpus_as_a_finding(dbfile):
    """close_by_text would otherwise parse it as what was wrong with the
    machine, and "loaded" would become a searchable found cause."""
    from src import db, textback

    _visit(db)
    out = textback.close_by_text(PHONE, "loaded")
    assert out.get("kind") == "parts_confirmed"

    with db.connect() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM repairs WHERE found_cause LIKE '%loaded%'"
        ).fetchone()[0]
    assert n == 0


# Knowing before the drive rather than after.


def test_a_visit_due_soon_with_nothing_confirmed_is_surfaced(dbfile):
    """The entire point: knowable while they are still at the depot."""
    from src import db, dispatch

    _visit(db, starts_in_minutes=20)
    waiting = dispatch.unconfirmed("D-REF")
    assert waiting, "a van about to leave unconfirmed was not flagged"
    assert "before they leave" in waiting[0]["say"]


def test_a_confirmed_visit_is_not_chased(dbfile):
    from src import db, dispatch

    _visit(db, starts_in_minutes=20)
    dispatch.confirm_loaded(PHONE, "loaded")
    assert dispatch.unconfirmed("D-REF") == []


def test_a_visit_far_in_the_future_is_not_chased_yet(dbfile):
    """Far enough ahead that the parts can still be fetched is not a problem."""
    from src import db, dispatch

    _visit(db, starts_in_minutes=600)
    assert dispatch.unconfirmed("D-REF") == []


def test_the_record_does_not_claim_a_part_was_left_behind(dbfile):
    """Never confirmed is proof nobody checked, not proof it was forgotten.

    Claiming the stronger thing would be inventing a fact, which is what this
    module exists to stop.
    """
    from src import db, dispatch

    _visit(db)
    out = dispatch.how_often_unloaded("D-REF")
    assert out["never_confirmed"] == 1
    assert "not proof it was left behind" in out["say"]


# Was the desk right.


def test_a_prediction_is_checked_against_what_it_really_was(dbfile):
    """Full-information feedback: the technician reports the truth about the
    machine, not a verdict on the part we sent."""
    from src.calibration import _same_fault

    assert _same_fault("evaporator fan motor seized, no air across the coil",
                       "evaporator fan motor had seized")
    assert not _same_fault("evaporator fan motor seized",
                           "defrost heater element open circuit")


def test_calibration_says_nothing_rather_than_something_thin(dbfile):
    """This module exists to stop the desk being confidently wrong, so it must
    not become the thing it is measuring."""
    from src import calibration

    out = calibration.reliability("D-REF")
    assert out["checked"] == 0
    assert "empty one" in out["say"]


def test_calibration_measures_and_never_corrects(dbfile):
    """These scores are normalised retrieval similarities and never were
    probabilities. Scaling them until they look right on a corpus this size
    produces a well calibrated number about a fiction."""
    import inspect

    from src import calibration

    body = inspect.getsource(calibration).split('"""', 2)[-1]
    for banned in ("platt", "isotonic", "sigmoid", "def correct", "recalibrat"):
        assert banned not in body.lower(), f"a correction crept in: {banned}"
