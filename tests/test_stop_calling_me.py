""""Stop calling me", which could not be recorded and did not stop the texts.

TWO BUGS IN THE ONE RULE THE SYSTEM SAYS NOTHING MAY OVERRIDE

`take_us_off_your_list` opens by calling this "the one sentence in this system
that no other rule may override".

FIRST, IT COULD NEVER FIND THE NUMBER. A live call seeds session state as
`caller_phone`. The tool read `caller_e164` and `from_number`, and neither is
written anywhere in the codebase; they were only ever read. So on every real
call it answered "I could not tell which number you are calling from" and
recorded nothing at all. The most important rule in the system, defeated by a
key name, and silent because the failure looked like a polite refusal.

SECOND, IT ONLY STOPPED THE CALLS. Opting out writes to the do-not-call list
and never touches outreach_consent. The follow-up messages checked only
outreach_consent. So somebody who said "never contact me again" stopped
getting phone calls and carried on getting texts, which is neither what they
asked for nor what they would describe to anybody who asked.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, state):
        self.state = state


# Exactly the shape a live call seeds. See telephony/twilio_bridge.py.
LIVE_CALL = {"caller_phone": "+15551119999", "caller": {"account_id": "A-X"},
             "call_id": "CALL-X", "dealer_id": "D-REF"}


def test_it_can_find_the_number_a_live_call_actually_gives_it(dbfile):
    """The bug. Any spelling now works, so a rename cannot silently disarm
    this again."""
    from src.tools import _caller_number

    assert _caller_number(_Ctx(LIVE_CALL)) == "+15551119999"
    assert _caller_number(_Ctx({"caller_e164": "+1555"})) == "+1555"
    assert _caller_number(_Ctx({"caller": {"phone": "+1666"}})) == "+1666"
    assert _caller_number(_Ctx({})) == ""


def test_asking_to_stop_is_actually_recorded(dbfile):
    """It used to answer "I could not tell which number you are calling from"
    and write nothing."""
    from src import linetype
    from src.tools import take_us_off_your_list

    out = take_us_off_your_list("do not ring me again", _Ctx(LIVE_CALL))

    assert "could not tell which number" not in str(out.get("say", ""))
    assert linetype.on_our_do_not_call("+15551119999")["listed"] is True


def test_it_stops_the_calls(dbfile):
    from src import db, linetype
    from src.outreach import queue_outreach

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-X','D-REF','business','Stop Me','2024-01-01')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('C-X','A-X','Pat','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) "
                  "VALUES ('+15551119999','C-X','mobile',1)")
    linetype.stop_calling("+15551119999", "said stop")

    for kind in ("offer", "prediction", "recall", "hazard"):
        out = queue_outreach([{"kind": kind, "account_id": "A-X",
                               "reason": "r", "evidence": "-",
                               "asset_id": None}], "D-REF")
        assert out["queued"] == [], f"{kind} still went out"


def test_it_stops_the_texts_too(dbfile):
    """The second bug. Calls stopped, texts did not, because the two paths
    read different tables."""
    from src import linetype
    from src.followup import _opted_out

    assert _opted_out("+15551119999") is False
    linetype.stop_calling("+15551119999", "said stop")
    assert _opted_out("+15551119999") is True


def test_an_unreadable_list_stops_the_texts(dbfile, monkeypatch):
    """Not knowing whether they opted out is not a reason to message them."""
    from src import followup

    def boom(*a, **k):
        raise RuntimeError("list unavailable")

    monkeypatch.setattr("src.linetype.on_our_do_not_call", boom)
    assert followup._opted_out("+15550000000") is True


def test_somebody_who_never_asked_still_gets_their_follow_ups(dbfile):
    """The gate must not become a blanket refusal: these messages finish a
    conversation the customer started."""
    from src.followup import _opted_out

    assert _opted_out("+15554443333") is False
