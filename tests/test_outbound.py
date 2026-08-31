"""The last mile, which did not exist.

`sweep_recalls` found customers who own a machine under an active federal
safety recall. `queue_outreach` put them at priority 10, above every
prediction and every offer. `take_next` claimed the highest-priority one that
was due, inside quiet hours, with an opening line written by a person.

Then nothing rang anybody. A safety notice was correctly identified, correctly
prioritised, and left in a queue, which is worse than not having the sweep at
all because the system reported having handled it.

And `close_by_text` was built for a technician replying to a briefing by SMS,
with no route anywhere that could reach it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture()
def twilio(monkeypatch):
    """Credentials present, and every REST call captured instead of placed."""
    from src import outbound

    monkeypatch.setattr(outbound, "settings", SimpleNamespace(
        twilio_account_sid="AC" + "0" * 32, twilio_auth_token="tok",
        public_ws_base="wss://example.test", twilio_from="+13095550000"))

    sent = []
    monkeypatch.setattr(outbound, "_post",
                        lambda path, fields: sent.append((path, fields)) or
                        {"ok": True, "response": {"sid": f"SM{len(sent)}"}})
    return sent


# Nothing goes out without the configuration to send it.


def test_nothing_is_sent_without_credentials(dbfile, monkeypatch):
    """A deployment with no Twilio details must fail loudly rather than
    appearing to have rung somebody."""
    from src import outbound

    monkeypatch.setattr(outbound, "settings", SimpleNamespace(
        twilio_account_sid="", twilio_auth_token="", public_ws_base="",
        twilio_from=""))

    assert outbound.send_sms("+13095550101", "hello")["ok"] is False
    assert outbound.place_call("+13095550101", "OUT-1")["ok"] is False


def test_a_call_with_no_number_is_not_attempted(dbfile, twilio):
    from src import outbound

    assert outbound.place_call("", "OUT-1")["ok"] is False
    assert outbound.place_call("+13095550101", "")["ok"] is False
    assert twilio == []


# Placing the call.


def test_the_queued_reason_travels_with_the_call(dbfile, twilio):
    """The agent has to know why it rang somebody who did not ring us. The
    first sentence of an outbound call decides whether they listen."""
    from src import outbound

    outbound.place_call("+13095550101", "OUT-ABC")
    path, fields = twilio[0]

    assert path == "Calls.json"
    assert "outreach=OUT-ABC" in fields["Url"]
    assert fields["To"] == "+13095550101"


def test_it_will_not_leave_a_recorded_message(dbfile, twilio):
    """An unattended AI voice leaving a recording about a safety recall, on a
    machine nobody may check, is worse than ringing again later."""
    from src import outbound

    outbound.place_call("+13095550101", "OUT-ABC")
    assert twilio[0][1]["MachineDetection"] == "Enable"


def test_the_status_callback_is_wired_so_a_missed_call_is_seen(dbfile, twilio):
    from src import outbound

    outbound.place_call("+13095550101", "OUT-ABC")
    assert "/call-status" in twilio[0][1]["StatusCallback"]


# Running the queue.


def test_nothing_here_decides_who_to_ring(dbfile):
    """Consent, quiet hours and the frequency cap all live in outreach.py and
    have already run. Re-deciding any of it here would put the rules in two
    places and they would drift."""
    import inspect

    from src import outbound

    body = inspect.getsource(outbound).split('"""', 2)[-1]
    for banned in ("_consent", "quiet_before", "quiet_after", "max_per_days",
                   "granted"):
        assert banned not in body, f"a consent rule leaked into outbound: {banned}"


def test_an_account_with_no_number_is_recorded_not_retried_forever(dbfile,
                                                                  twilio,
                                                                  monkeypatch):
    """Otherwise the same unreachable row is claimed on every sweep and blocks
    the queue behind it."""
    from src import outbound

    claimed = [{"ok": True, "call": {"outreach_id": "OUT-1", "kind": "recall",
                                     "phone": None, "reason": "x"}},
               {"ok": True, "call": None}]
    monkeypatch.setattr("src.outreach.take_next", lambda d="D-REF": claimed.pop(0))

    noted = []
    monkeypatch.setattr("src.outreach.record_outcome",
                        lambda oid, outcome, note="": noted.append((oid, outcome)))

    out = outbound.run_queue("D-REF")
    assert out["skipped"] == 1
    assert noted == [("OUT-1", "wrong_number")]


def test_a_call_that_could_not_be_placed_is_not_marked_done(dbfile, monkeypatch):
    """A recall that silently counts as handled is the exact failure this file
    exists to fix."""
    from src import outbound

    claimed = [{"ok": True, "call": {"outreach_id": "OUT-2", "kind": "recall",
                                     "phone": "+13095550101", "reason": "x"}},
               {"ok": True, "call": None}]
    monkeypatch.setattr("src.outreach.take_next", lambda d="D-REF": claimed.pop(0))
    monkeypatch.setattr(outbound, "place_call",
                        lambda to, oid, from_number="": {"ok": False,
                                                         "why": "carrier down"})
    monkeypatch.setattr("src.outreach.record_outcome",
                        lambda *a, **k: pytest.fail(
                            "a call that never happened was recorded as an outcome"))

    out = outbound.run_queue("D-REF")
    assert out["placed"] == 0
    assert out["not_placed"][0]["why"] == "carrier down"


def test_one_sweep_cannot_empty_the_queue_into_somebody_s_evening(dbfile,
                                                                 monkeypatch):
    from src import outbound

    monkeypatch.setattr("src.outreach.take_next", lambda d="D-REF": {
        "ok": True, "call": {"outreach_id": "OUT-X", "kind": "offer",
                             "phone": "+13095550101", "reason": "x"}})
    monkeypatch.setattr(outbound, "place_call",
                        lambda to, oid, from_number="": {"ok": True, "sid": "CA1"})

    assert outbound.run_queue("D-REF", limit=3)["placed"] == 3


# What the agent is told when we are the one calling.


def test_an_outbound_call_says_it_is_outbound(dbfile):
    """A caller who did not ring us must never be greeted as though they did."""
    from src import db
    from src.telephony.twilio_bridge import _outbound_brief

    with db.connect() as c:
        acct = c.execute("SELECT id FROM accounts LIMIT 1").fetchone()
    with db.txn() as c:
        c.execute(
            """INSERT INTO outreach_queue
               (id,account_id,reason,due_after,kind,evidence,dealer_id,priority)
               VALUES ('OUT-R','%s','a safety recall on their freezer',
                       '2026-01-01','recall','federal notice','D-REF',10)"""
            % acct["id"])

    brief = _outbound_brief("OUT-R")
    assert "OUTBOUND" in brief
    assert "did not ring us" in brief
    assert "automated assistant" in brief
    assert "taken off the list" in brief


def test_a_call_we_cannot_explain_is_ended_rather_than_improvised(dbfile):
    """Inventing a reason for ringing somebody is worse than apologising and
    hanging up."""
    from src.telephony.twilio_bridge import _outbound_brief

    brief = _outbound_brief("OUT-NONE")
    assert "cannot see why" in brief
    assert "Do not invent a reason" in brief


# SMS, the route close_by_text never had.


def test_a_technician_can_close_a_job_by_sms(dbfile, monkeypatch):
    """It was built for exactly this and only worked if they happened to be on
    WhatsApp."""
    from src import db, desk

    with db.connect() as c:
        tech = c.execute("SELECT phone FROM technicians WHERE phone IS NOT NULL "
                         "LIMIT 1").fetchone()
    if tech is None:
        pytest.skip("no technician with a phone")

    seen = []
    monkeypatch.setattr(
        "src.textback.close_by_text",
        lambda phone, msg, visit_id="": seen.append(phone) or
        {"ok": True, "reply_to_technician": "Thanks, closed."})

    assert desk.answer(tech["phone"], "was the harness again",
                       channel="sms") == "Thanks, closed."
    assert seen == [tech["phone"]]


def test_the_sms_route_refuses_an_unsigned_request(dbfile, monkeypatch):
    """It closes jobs and reads equipment history, like every other webhook."""
    import asyncio

    from src import main

    monkeypatch.delenv("PRAEVISUM_OPEN_WHATSAPP", raising=False)
    monkeypatch.setattr("src.whatsapp.settings",
                        SimpleNamespace(twilio_auth_token="tok",
                                        twilio_account_sid="AC1"))

    class _Req:
        headers: dict = {}
        url = "https://example.test/sms"

        async def form(self):
            return {"From": "+13095550101", "Body": "loaded"}

    out = asyncio.run(main.sms_webhook(_Req()))
    assert getattr(out, "status_code", 200) == 403
