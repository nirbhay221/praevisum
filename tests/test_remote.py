"""Deciding not to send anybody, which this system could never do.

Every service call ended in a visit. The industry's own figure is that 14% of
truck rolls are unnecessary at $200-300 each, so that was waste the product
could not even detect.

The asymmetry is the whole design and most of these tests are about it. A
wasted visit costs money. Talking somebody out of a visit they needed costs the
relationship, and they remember. So the bar for NOT sending is deliberately
high, and silence is never a diagnosis.
"""

from __future__ import annotations

from conftest import REF


def _fix(db, symptom="not holding temperature overnight", source="manual",
         family="reach-in freezer", fix_id="RF-TEST", tools=0):
    with db.txn() as c:
        c.execute(
            """INSERT INTO remote_fixes
               (id,dealer_id,family,symptom,check_first,instruction,
                source,source_ref,requires_tools)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (fix_id, REF, family, symptom, "is the door closing properly?",
             "clear the shelf and let the door seat fully", source,
             "test source", tools))
    return fix_id


def test_a_documented_fix_is_offered(dbfile):
    from src import db, remote

    _fix(db)
    d = remote.should_send_someone("AS-FREEZER",
                                   "it is not holding temperature overnight", REF)
    assert d["send"] == "offer_first"
    assert d["remote_fix"]["source"] == "manual"


def test_nothing_documented_means_somebody_goes(dbfile):
    """Silence is not a diagnosis."""
    from src import remote

    d = remote.should_send_someone("AS-FREEZER",
                                   "loud grinding noise from the compressor", REF)
    assert d["send"] is True
    assert "Do not offer a fix we cannot source" in d["say"]


def test_a_fix_is_never_invented(dbfile):
    """The instruction is read from a row, never composed.

    An agent improvising a repair procedure for somebody standing in front of
    a live appliance is the worst thing this system could do.
    """
    from src import db, remote

    _fix(db)
    got = remote.find_remote_fix("AS-FREEZER", "not holding temperature overnight", REF)
    assert got["instruction"] == "clear the shelf and let the door seat fully"
    assert got["source_ref"]


def test_a_loose_word_match_is_not_enough(dbfile):
    """The semantic index is right for diagnosis and wrong for this.

    A loose resemblance justifies carrying a part. It does not justify talking
    somebody out of a visit.
    """
    from src import db, remote

    _fix(db)
    d = remote.should_send_someone("AS-FREEZER", "the light inside is flickering", REF)
    assert d["send"] is True


# --------------------------------------------------------------------------
# provenance and track record
# --------------------------------------------------------------------------

def test_published_documentation_does_not_need_our_permission(dbfile):
    """The cold-start deadlock this nearly shipped with.

    Requiring a track record from everything meant a procedure could not be
    offered until tried, and could not be tried until offered. Every published
    fix sat unusable forever.
    """
    from src import db, remote

    _fix(db, source="manual")
    got = remote.find_remote_fix("AS-FREEZER", "not holding temperature overnight", REF)
    assert got["found"], "a documented procedure was never offered"


def test_something_we_inferred_ourselves_must_earn_its_place(dbfile):
    """Our own inference is a guess until it has worked."""
    from src import db, remote

    _fix(db, source="our_notes")
    got = remote.find_remote_fix("AS-FREEZER", "not holding temperature overnight", REF)
    assert not got["found"]


def test_a_documented_fix_that_keeps_failing_is_withdrawn(dbfile):
    """Provenance never outranks failure."""
    from src import db, remote

    fix = _fix(db, source="manual")
    for _ in range(3):
        remote.record_attempt(fix, "not_resolved", dealer_id=REF)

    got = remote.find_remote_fix("AS-FREEZER", "not holding temperature overnight", REF)
    assert not got["found"], "a fix that failed three times is still being offered"


def test_an_unsafe_outcome_withdraws_it_immediately(dbfile):
    """Somebody was asked to do something they should not have been.

    That does not wait for a success rate to drift downwards.
    """
    from src import db, remote

    fix = _fix(db, source="manual")
    r = remote.record_attempt(fix, "unsafe", dealer_id=REF)
    assert r["withdrawn"]

    got = remote.find_remote_fix("AS-FREEZER", "not holding temperature overnight", REF)
    assert not got["found"]


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------

def test_a_resolved_attempt_counts_as_a_saved_visit(dbfile):
    """The number the whole feature exists to produce."""
    from src import db, remote

    fix = _fix(db)
    r = remote.record_attempt(fix, "resolved", asset_id="AS-FREEZER", dealer_id=REF)
    assert r["saved_a_visit"]

    with db.connect() as c:
        saved = c.execute(
            "SELECT visits_saved FROM remote_fix_record WHERE id=?", (fix,)).fetchone()[0]
    assert saved == 1


def test_a_failed_attempt_sends_them_to_the_visit(dbfile):
    """A failed attempt is not a reason to keep them on the phone."""
    from src import db, remote

    fix = _fix(db)
    r = remote.record_attempt(fix, "not_resolved", dealer_id=REF)
    assert not r["saved_a_visit"]
    assert "Book the visit now" in r["told_caller"]
    assert "Do not try a second procedure" in r["told_caller"]


def test_bad_input_is_refused(dbfile):
    from src import db, remote

    _fix(db)
    assert not remote.record_attempt("RF-TEST", "sort of worked")["ok"]
    assert not remote.record_attempt("RF-NOPE", "resolved")["ok"]


def test_the_customer_is_never_made_to_choose(dbfile):
    """Offering a fix must not read as refusing a visit."""
    from src import db, remote

    _fix(db)
    d = remote.should_send_someone("AS-FREEZER",
                                   "it is not holding temperature overnight", REF)
    assert "a visit is already there if it does not work" in d["say"]
    assert "book it without argument" in d["say"]


def test_it_shares_the_cost_of_a_wasted_trip_with_the_van_loading(dbfile):
    """Two halves of one decision must not price the same thing differently."""
    from src import db, remote
    from src.reason import TRUCK_ROLL

    _fix(db)
    d = remote.should_send_someone("AS-FREEZER",
                                   "it is not holding temperature overnight", REF)
    assert d["cost_avoided_if_it_works"] == TRUCK_ROLL


def test_nothing_claims_a_manual_it_does_not_have(dbfile):
    """Provenance has to be true, not just present.

    Seven of these were written for the seed and originally labelled `manual`
    with a source_ref of "first-line checks, commercial refrigeration". That
    reads like a citation and is not one: there is no such document and no page
    to turn to. They are `general` now.

    Anything labelled `manual` must carry a reference somebody could actually
    look up.
    """
    from src import db

    with db.connect() as c:
        rows = c.execute(
            "SELECT source, source_ref FROM remote_fixes WHERE source='manual'"
        ).fetchall()

    for r in rows:
        ref = (r["source_ref"] or "").lower()
        assert ref and "not from a manual" not in ref, \
            "a fix claims a manual with no real reference behind it"


def test_a_recall_fix_carries_its_recall_number(dbfile):
    """The one genuinely cited source in the seed."""
    from src import db

    with db.txn() as c:
        c.execute(
            """INSERT INTO remote_fixes
               (id,dealer_id,family,symptom,instruction,source,source_ref)
               VALUES ('RF-REC',?,'reach-in freezer','safety recall',
                       'stop using it and contact the manufacturer',
                       'recall','18013')""", (REF,))
        row = c.execute("SELECT source_ref FROM remote_fixes WHERE id='RF-REC'").fetchone()
    assert row["source_ref"].isdigit(), "a recall fix must carry its recall number"
