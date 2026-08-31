"""The day's calling list, for a person rather than for the desk.

WHY THIS EXISTS AT ALL

The desk may only ring a published business landline: an AI-generated voice is
an artificial voice under the TCPA and there is no business carve-out for a
wireless number. Most small restaurants publish a mobile, so most prospects
this system finds are ones the desk must leave alone.

A person has no such restriction. A salesperson ringing a business mobile is an
ordinary B2B call, exempt under the TSR, with no artificial voice to trigger
anything. So the prospects the desk refuses are precisely the ones worth
handing to a human, and this list must include them rather than hide them.

TWO STAGES, FOLLOWING THE RESEARCH

"Profiling before scoring: a two-stage predictive model for B2B lead
prioritization" (Journal of Personal Selling and Sales Management, 2026) argues
for establishing what KIND of lead something is before ranking it, and names
the failure this addresses: reps lacking the initial information to judge
viability, so contacts happen late and on intuition.

Stage one asks which fault the public text describes, matched against this
company's own closed repairs. Stage two ranks. A single blended number would
hide the first question, which is the one a salesperson needs answered.

WHAT THESE TESTS PROTECT

That a claim about a cause is backed by real repairs, that the customer's own
words are never paraphrased, and above all that a prospect the DESK cannot ring
still reaches the human list. Dropping those would quietly turn a legal
restriction on a voice into a restriction on the business.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def prospects_on_file(dbfile):
    from src import db

    with db.txn() as c:
        c.executemany(
            """INSERT INTO prospects
                 (id,dealer_id,name,kind,address,phone_e164,line_type,source,
                  found_on,signal,signal_kind,signal_score,signal_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [("P-H1", "D-REF", "Riverbend Diner", "restaurant", "1 River Dr",
              "+15635550101", "mobile", "test", "2026-08-30",
              "cold, water, pooling", "public_complaint", 1.0,
              "the salads were not cold and there was water pooling by the "
              "freezer door"),
             ("P-H2", "D-REF", "Brady Street Cafe", "cafe", "2 Brady St",
              "+15635550102", "landline", "test", "2026-08-30",
              "display, error", "public_complaint", 0.6,
              "the display fridge is showing an error code")])


def test_a_prospect_the_desk_cannot_ring_still_reaches_the_person(
        prospects_on_file):
    """THE WHOLE POINT. The restriction is on an artificial voice, not on the
    business. Filtering mobiles out of a human's list would throw away the
    majority of real prospects for a reason that does not apply to them."""
    from src import hunting

    out = hunting.todays_list("D-REF", at="2026-08-30T11:00:00")
    names = [L["name"] for L in out["leads"]]

    assert "Riverbend Diner" in names, (
        "a mobile prospect was dropped from a list meant for a person")

    lead = next(L for L in out["leads"] if L["name"] == "Riverbend Diner")
    assert lead["desk_may_call"] is False
    assert lead["desk_blocked_by"]


def test_the_row_says_which_calls_the_desk_may_make(prospects_on_file):
    """So nobody has to remember the rule, and nobody hands a mobile to the
    dialler by mistake."""
    from src import hunting

    out = hunting.todays_list("D-REF", at="2026-08-30T11:00:00")
    for lead in out["leads"]:
        assert isinstance(lead["desk_may_call"], bool)


def test_their_own_words_are_quoted_never_paraphrased(prospects_on_file):
    """The value of the opener is that it is theirs. "Our system believes you
    have a problem" is a cold call; "you posted this" is not."""
    from src import hunting

    out = hunting.todays_list("D-REF", at="2026-08-30T11:00:00")
    lead = next(L for L in out["leads"] if L["name"] == "Riverbend Diner")

    assert "water pooling by the freezer door" in lead["they_said"]
    assert any("THEIR words" in s for s in lead["sticky"])


def test_no_cause_is_claimed_without_repairs_behind_it(dbfile, monkeypatch):
    """A salesperson told the cause is probably a compressor, on one shared
    word, says something a chef knows is wrong. Silence is better."""
    from src import hunting

    monkeypatch.setattr(hunting, "_fault_profile",
                        lambda d, s, t: {"known": False})
    monkeypatch.setattr(hunting, "_what_it_would_take", lambda d, c, t: {})

    note = hunting._sticky_note(
        {"signal_seen": "something is wrong"}, {"known": False}, {},
        {"may_call": True})

    assert any("do not guess" in s for s in note)
    assert not any("came before" in s for s in note)


def test_a_known_pattern_is_stated_with_its_count(dbfile):
    """"We have seen this three times" is the sentence that makes the call
    land. It is also a checkable claim, which is why the number is carried."""
    from src import hunting

    note = hunting._sticky_note(
        {"signal_seen": "not cold"},
        {"known": True, "usually": "door gasket perished", "times": 5},
        {}, {"may_call": True})

    joined = " ".join(note)
    assert "door gasket perished" in joined
    assert "5 of our own jobs" in joined
    assert "Do not say the cause outright" in joined


def test_the_part_and_the_offer_ride_along(dbfile):
    """A salesperson who can say the price and that it is on the shelf is
    having a different conversation from one promising to ring back."""
    from src import hunting

    note = hunting._sticky_note(
        {"signal_seen": "not cold"},
        {"known": True, "usually": "door gasket perished", "times": 3},
        {"sku": "P-DOORGASKET", "part": "Door gasket", "price": 92.0,
         "offer_price": 78.2, "offer": "15% off door gaskets", "on_hand": 10},
        {"may_call": True})

    joined = " ".join(note)
    assert "Door gasket" in joined
    assert "78.20" in joined
    assert "10 on the shelf" in joined


def test_ranking_prefers_evidence_over_nothing(dbfile):
    """Stage two. A lead we have fixed before, with the part in stock, must
    outrank a louder complaint we know nothing about."""
    from src import hunting

    known = hunting._rank(0.6, {"known": True, "times": 3},
                          {"sku": "X", "on_hand": 4})["score"]
    unknown = hunting._rank(1.0, {"known": False}, {})["score"]

    assert known > unknown


def test_already_approached_prospects_drop_off_the_list(prospects_on_file):
    """A list that keeps coming round wastes the salesperson's morning."""
    from src import db, hunting

    with db.txn() as c:
        c.execute("UPDATE prospects SET approached_on='2026-08-29' "
                  "WHERE id='P-H1'")

    out = hunting.todays_list("D-REF", at="2026-08-30T11:00:00")
    assert all(L["name"] != "Riverbend Diner" for L in out["leads"])


def test_one_business_never_sees_anothers_prospects(prospects_on_file):
    from src import hunting

    out = hunting.todays_list("D-IT", at="2026-08-30T11:00:00")
    assert out["leads"] == []


def test_the_score_is_never_presented_as_a_probability(dbfile):
    """`calibration.reliability()` on this desk returns checked: 0, because no
    prediction has yet been followed by anybody saying what really happened. A
    percentage beside a lead reads as "72% likely to buy", and nothing here
    supports that claim.

    So the score carries a disclaimer in the payload, and it must stay in a
    range no one can mistake for a percentage.
    """
    from src import hunting

    r = hunting._rank(1.0, {"known": True, "times": 5},
                      {"sku": "X", "on_hand": 3, "offer": "10% off"})

    assert "not a probability" in r["is_not"]
    assert r["score"] > 1.0, (
        "the score must not sit in 0..1 where it reads as a probability")


def test_the_score_shows_what_it_is_made_of(dbfile):
    """"2.20" tells a salesperson nothing. Each clause is a row they can check,
    which is the same argument trace.py makes about the reasoning being
    visible while it happens."""
    from src import hunting

    r = hunting._rank(1.0, {"known": True, "times": 5},
                      {"sku": "X", "part": "Door gasket", "on_hand": 10,
                       "offer": "15% off door gaskets"})

    reasons = " ".join(b["what"] for b in r["because"])
    assert "fixed this before, 5 times" in reasons
    assert "on the shelf today" in reasons
    assert "live offer" in reasons

    # And the parts must actually add up to the score, or the breakdown is
    # decoration rather than an explanation.
    assert round(sum(b["points"] for b in r["because"]), 2) == r["score"]
