""""Stop calling me" outranking everything, including a safety notice.

THE GAP

`take_us_off_your_list` writes a permanent row and its own docstring says
"every outbound path checks it before anything else, before the clock and
before it will even pay to look a number up".

That was true of prospecting and untrue of customer outreach. An internal
do-not-call request revokes consent; recall and hazard deliberately bypass
consent because a safety notice is not marketing. Put together, somebody who
had explicitly asked never to be contacted was still queued for an automated
hazard call.

That is the shape of complaint that starts an investigation: the request was
recorded, the code claimed to honour it, and an automated voice rang them
anyway.

WHAT REPLACES IT

The obligation does not vanish with the automated call. They still own a
machine we believe is dangerous. The notice is handed to a PERSON, which
honours what they asked for and puts a human on the one call that most needs
judgement.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def asked_us_to_stop(dbfile):
    from src import db, linetype

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-S','D-REF','business','Said Stop','2024-01-01')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('C-S','A-S','Pat','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) "
                  "VALUES ('+15551110099','C-S','mobile',1)")
    linetype.stop_calling("+15551110099", "said stop on a call")
    return "A-S"


def _cand(kind, account):
    return {"kind": kind, "account_id": account, "reason": f"a {kind}",
            "evidence": "x", "asset_id": None}


@pytest.mark.parametrize("kind", ["offer", "prediction", "recall", "hazard"])
def test_nothing_automated_reaches_them(asked_us_to_stop, kind):
    """Including the two kinds that deliberately bypass consent."""
    from src.outreach import queue_outreach

    out = queue_outreach([_cand(kind, asked_us_to_stop)], "D-REF")
    assert out["queued"] == []
    assert "never to contact them" in out["blocked"][0]["blocked_because"]


def test_a_safety_notice_is_handed_to_a_person_instead(asked_us_to_stop):
    """The duty of care survives the refusal. They still own the machine."""
    from src import db
    from src.outreach import queue_outreach

    queue_outreach([_cand("hazard", asked_us_to_stop)], "D-REF")

    with db.connect() as c:
        row = c.execute("SELECT detail FROM escalations "
                        "WHERE detail LIKE '%do-not-call%'").fetchone()
    assert row is not None, "the safety notice vanished instead of escalating"
    assert "Do not use the automated line" in row["detail"]
    assert "+15551110099" in row["detail"]


def test_an_offer_is_not_escalated_to_anybody(asked_us_to_stop):
    """Only safety survives the refusal. Escalating an offer to a person
    would be routing around the request rather than honouring it."""
    from src import db
    from src.outreach import queue_outreach

    queue_outreach([_cand("offer", asked_us_to_stop)], "D-REF")

    with db.connect() as c:
        assert not c.execute("SELECT 1 FROM escalations "
                             "WHERE detail LIKE '%do-not-call%'").fetchone()


def test_any_number_on_the_account_counts(dbfile):
    """A request made from the mobile is a request from the business."""
    from src import db, linetype
    from src.outreach import queue_outreach

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-T','D-REF','business','Two Lines','2024-01-01')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('C-T','A-T','Pat','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) VALUES "
                  "('+15552220001','C-T','landline',1)")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) VALUES "
                  "('+15552220002','C-T','mobile',1)")
    linetype.stop_calling("+15552220002", "asked from the mobile")

    out = queue_outreach([_cand("hazard", "A-T")], "D-REF")
    assert out["queued"] == []


def test_an_unreadable_list_fails_closed(asked_us_to_stop, monkeypatch):
    """If we cannot tell whether they asked us to stop, we do not ring. The
    error that costs money is ringing somebody who did."""
    from src import outreach

    def boom(*a, **k):
        raise RuntimeError("list unavailable")

    monkeypatch.setattr("src.linetype.on_our_do_not_call", boom)

    out = outreach.queue_outreach([_cand("offer", asked_us_to_stop)], "D-REF")
    assert out["queued"] == []


def test_somebody_who_never_asked_is_unaffected(dbfile):
    """The gate must not become a blanket refusal."""
    from src import db
    from src.outreach import queue_outreach

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-OK','D-REF','business','Fine','2024-01-01')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('C-OK','A-OK','Pat','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) "
                  "VALUES ('+15553330001','C-OK','mobile',1)")

    out = queue_outreach([_cand("hazard", "A-OK")], "D-REF")
    assert len(out["queued"]) == 1
