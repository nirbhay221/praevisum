"""Opting in on a call, and what spoken consent does not buy.

THE ASYMMETRY

`take_us_off_your_list` was a live tool from the start, so a caller could opt
OUT by saying so. Nothing could record them opting IN: only a seed script ever
wrote a consent row. Somebody could say "yes, ring me if something comes up"
and it went nowhere.

THE PART THAT MUST NOT BE LOST WHILE FIXING IT

Recording spoken consent must NOT unlock marketing. An AI voice is an
artificial or prerecorded voice under the TCPA, and a marketing call using one
needs prior express WRITTEN consent. Oral consent is real and it is enough for
a call about their own equipment.

So the row this writes has to permit a service call and still refuse an offer.
A fix that made opting in possible by quietly widening what it permits would
be worse than the gap.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, state):
        self.state = state


@pytest.fixture
def a_caller(dbfile):
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-C','D-REF','business','Consent Cafe','2024-01-01')")
        c.execute("INSERT INTO calls (id,started_at,from_e164,dealer_id) "
                  "VALUES ('CALL-C','2026-08-30T10:00:00','+15558880001',"
                  "'D-REF')")
    return _Ctx({"dealer_id": "D-REF", "call_id": "CALL-C",
                 "caller": {"account_id": "A-C", "phone": "+15558880001"}})


def test_a_caller_can_now_opt_in(a_caller):
    from src import db
    from src.tools import they_agreed_we_may_call

    out = they_agreed_we_may_call("yes, ring me if anything comes up", a_caller)
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT granted, consent_form, granted_via, "
                        "evidence_ref FROM outreach_consent "
                        "WHERE account_id='A-C'").fetchone()
    assert row["granted"] == 1
    assert row["consent_form"] == "oral"
    assert "ring me if anything" in row["granted_via"]


def test_what_they_said_is_kept_as_the_evidence(a_caller):
    """A consent row with no provenance is worse than none, because it looks
    like proof."""
    from src import db
    from src.tools import they_agreed_we_may_call

    they_agreed_we_may_call("sure, you can call me", a_caller)

    with db.connect() as c:
        row = c.execute("SELECT evidence_ref FROM outreach_consent "
                        "WHERE account_id='A-C'").fetchone()
    assert row["evidence_ref"] == "CALL-C"


def test_spoken_consent_permits_a_service_call(a_caller):
    from src import db
    from src.outreach import _consent
    from src.tools import they_agreed_we_may_call

    they_agreed_we_may_call("yes that is fine", a_caller)

    with db.connect() as c:
        assert _consent(c, "A-C", marketing=False)["may_call"] is True


def test_spoken_consent_still_refuses_a_marketing_call(a_caller):
    """The line that must survive the fix. An AI voice plus marketing needs
    written consent, and making opting in possible must not quietly widen
    what it permits."""
    from src import db
    from src.outreach import _consent
    from src.tools import they_agreed_we_may_call

    they_agreed_we_may_call("yes ring me about offers too", a_caller)

    with db.connect() as c:
        rule = _consent(c, "A-C", marketing=True)
    assert rule["may_call"] is False
    assert "not enough for a marketing call" in rule["why"]


def test_the_tool_does_not_overclaim_what_it_bought(a_caller):
    """The honest version, after the first one overclaimed.

    Spoken consent currently unlocks NOTHING: safety calls never needed it,
    and offers and predicted-failure calls both need written consent. Saying
    it "permits calls about their own equipment" was untrue and would have
    encouraged the agent to act on it.

    It is still worth recording. It is their stated wish, it is what a written
    form gets attached to later, and it is evidence if anybody asks."""
    from src.tools import they_agreed_we_may_call

    out = they_agreed_we_may_call("go ahead", a_caller)
    assert "nothing extra today" in out["permits"]
    assert "offers" in out["does_not_permit"]
    assert out["why_record_it_then"]
    assert "not now offer them" in out["say"]


def test_recording_it_changes_no_outcome_yet(a_caller):
    """Pinned deliberately. If a future change makes spoken consent unlock
    something, this fails and somebody has to look at whether that is legal
    rather than merely convenient."""
    from src import db
    from src.outreach import queue_outreach
    from src.tools import they_agreed_we_may_call

    with db.txn() as c:
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('C-CC','A-C','Pat','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) "
                  "VALUES ('+15558880001','C-CC','mobile',1)")

    they_agreed_we_may_call("yes, ring me", a_caller)

    for kind in ("offer", "prediction"):
        out = queue_outreach([{"kind": kind, "account_id": "A-C",
                               "reason": "r", "evidence": "-",
                               "asset_id": None}], "D-REF")
        assert out["queued"] == [], (
            f"{kind} became reachable on spoken consent alone")


def test_opting_in_after_opting_out_works(a_caller):
    """Somebody who changed their mind must not stay blocked by a stale
    revocation."""
    from src import db
    from src.tools import they_agreed_we_may_call

    they_agreed_we_may_call("yes fine", a_caller)
    with db.txn() as c:
        c.execute("UPDATE outreach_consent SET granted=0, "
                  "revoked_on='2026-08-01' WHERE account_id='A-C'")

    they_agreed_we_may_call("actually yes, go ahead", a_caller)
    with db.connect() as c:
        row = c.execute("SELECT granted, revoked_on FROM outreach_consent "
                        "WHERE account_id='A-C'").fetchone()
    assert row["granted"] == 1
    assert row["revoked_on"] is None


def test_it_refuses_before_anybody_is_identified(dbfile):
    from src.tools import they_agreed_we_may_call

    out = they_agreed_we_may_call("yes", _Ctx({"dealer_id": "D-REF"}))
    assert out["ok"] is False
