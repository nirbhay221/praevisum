"""Getting a queued message to the person it was written for.

The same gap twice. `outreach.py` decided every night who was worth ringing
and nothing rang anybody until outbound.py existed. `followup.py` has been
doing it quietly since it was built: a missed call, a dropped call and an
after-visit check all get queued, rendered into a sentence assembled from
recorded facts, and left in a table that nothing read.

So a customer whose call dropped mid-sentence got a message written for them
that never left the building, and the desk recorded having followed up.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

PHONE = "+13095550101"


def _queued(db, kind="dropped_call", phone=PHONE, dealer="D-REF"):
    now = datetime.now().isoformat(timespec="seconds")
    with db.txn() as c:
        c.execute(
            """INSERT INTO followups
               (id,dealer_id,kind,phone,context,due_after,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (f"FU-{kind}-{phone[-4:]}", dealer, kind, phone,
             "the walk-in is sitting at twelve degrees", now, now))


def test_a_queued_followup_actually_goes_out(dbfile, monkeypatch):
    """The whole gap. It was rendered, queued, and read by nothing."""
    from src import db, sender

    _queued(db)
    sent = []
    monkeypatch.setattr(sender, "_deliver",
                        lambda ch, ph, txt: sent.append((ch, ph, txt)) or {"ok": True})

    out = sender.send_followups("D-REF")
    assert out["sent"] == 1
    assert "twelve degrees" in sent[0][2], "their own words were not carried"


def test_a_sent_followup_is_marked_and_not_sent_twice(dbfile, monkeypatch):
    from src import db, sender

    _queued(db)
    monkeypatch.setattr(sender, "_deliver", lambda ch, ph, txt: {"ok": True})

    assert sender.send_followups("D-REF")["sent"] == 1
    assert sender.send_followups("D-REF")["sent"] == 0, "sent twice"


def test_one_that_could_not_be_delivered_stays_queued(dbfile, monkeypatch):
    """A follow-up that did not arrive is not a follow-up that happened, and
    the desk recording otherwise is the failure this file exists to fix."""
    from src import db, sender

    _queued(db)
    monkeypatch.setattr(sender, "_deliver",
                        lambda ch, ph, txt: {"ok": False, "why": "no"})

    out = sender.send_followups("D-REF")
    assert out["sent"] == 0
    assert out["failed"] == 1

    # still there, so the next sweep tries again
    assert sender.send_followups("D-REF")["waiting"] == 1


def test_it_falls_through_when_a_channel_refuses(dbfile, monkeypatch):
    """A channel that will not take it must not swallow the message."""
    from src import db, sender

    _queued(db)
    tried = []

    def deliver(channel, phone, text):
        tried.append(channel)
        return {"ok": len(tried) >= 3}      # only the last one accepts

    monkeypatch.setattr(sender, "_deliver", deliver)
    out = sender.send_followups("D-REF")

    assert out["sent"] == 1
    assert len(tried) == 3, "it gave up before trying every channel"


def test_a_stated_preference_beats_our_own_costs(dbfile, monkeypatch):
    """What we pay to reach somebody is our problem, not theirs. A preference
    nobody honours is a field nobody should have collected, and channel_pref
    sat unread on every contact for exactly that reason once already."""
    from src import db, sender

    with db.txn() as c:
        c.execute("""UPDATE contacts SET channel_pref='sms'
                     WHERE id IN (SELECT contact_id FROM phones WHERE e164=?)""",
                  (PHONE,))
    assert sender._reachable(PHONE)[0] == "sms"

    with db.txn() as c:
        c.execute("""UPDATE contacts SET channel_pref='whatsapp'
                     WHERE id IN (SELECT contact_id FROM phones WHERE e164=?)""",
                  (PHONE,))
    assert sender._reachable(PHONE)[0] == "whatsapp"


def test_with_no_preference_the_cheap_channel_is_tried_first(dbfile):
    from src import db, sender

    with db.txn() as c:
        c.execute("""UPDATE contacts SET channel_pref=NULL
                     WHERE id IN (SELECT contact_id FROM phones WHERE e164=?)""",
                  (PHONE,))
    assert sender._reachable(PHONE)[0] == "telegram"


def test_a_linked_telegram_chat_is_tried_first(dbfile):
    """Evidence rather than assumption. A linked chat is proof they read
    things there; a preference nobody acted on is not."""
    from src import db, sender

    with db.txn() as c:
        c.execute(
            """INSERT INTO channel_links (channel,handle,phone)
               VALUES ('telegram','555',?)""", (PHONE,))

    assert sender._reachable(PHONE)[0] == "telegram"


def test_a_number_we_have_never_messaged_falls_back_to_sms(dbfile):
    """SMS is the one that works without them having joined anything."""
    from src import sender

    assert "sms" in sender._reachable("+15005559999")


def test_nothing_here_decides_whether_to_send(dbfile):
    """That was settled when the follow-up was queued, opt-out included.
    Re-deciding it would put the rule in two places and they would drift."""
    import inspect

    from src import sender

    body = inspect.getsource(sender).split('"""', 2)[-1]
    for banned in ("_opted_out", "outreach_consent", "granted", "revoked_on"):
        assert banned not in body, f"a consent rule leaked into sender: {banned}"


def test_nothing_here_composes_a_message(dbfile):
    """Every message was rendered from recorded facts by followup.render, for
    the same reason the briefing is assembled rather than narrated."""
    import inspect

    from src import sender

    src = inspect.getsource(sender)
    for banned in ("generate_content", "LlmAgent", "genai", "Runner"):
        assert banned not in src, f"sender reaches for a model: {banned}"


def test_whatsapp_can_start_a_conversation_now(dbfile, monkeypatch):
    """Every other WhatsApp path is a reply riding back in the TwiML. A
    follow-up has nobody to reply to."""
    from types import SimpleNamespace

    from src import whatsapp

    posted = []
    monkeypatch.setattr("src.outbound._post",
                        lambda path, fields: posted.append(fields) or
                        {"ok": True, "response": {"sid": "SM1"}})
    monkeypatch.setattr("src.outbound.settings", SimpleNamespace(
        twilio_account_sid="AC1", twilio_auth_token="t",
        public_ws_base="wss://x", twilio_from="+13095550000"))
    monkeypatch.setattr(whatsapp, "settings", SimpleNamespace(
        twilio_from="+13095550000", twilio_auth_token="t",
        twilio_account_sid="AC1"))

    out = whatsapp.send(PHONE, "we got cut off")
    assert out["ok"] is True
    assert posted[0]["To"] == f"whatsapp:{PHONE}"
    assert posted[0]["From"].startswith("whatsapp:")


def test_the_sweep_sends_them_whether_or_not_it_dials(dbfile):
    """A missed call and a dropped call are conversations the customer
    started. They are not marketing and are not gated by marketing consent."""
    import pathlib

    src = pathlib.Path("scripts/run_outreach.py").read_text(encoding="utf-8")
    assert "sender.send_followups" in src
    assert "not gated by marketing consent" in src
