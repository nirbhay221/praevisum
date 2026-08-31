"""The delivery leg, which was written and tested and reachable from nothing.

WHAT WAS ALREADY TRUE

`carrier_delivered` and `close_order` existed, worked, and had tests. The
docstring even called the first one "the webhook end: UPS, or whoever, telling
us a tracking number was delivered". There was no webhook. Walking the call
graph across every function found both of them reachable from no route, no
tool list, and no scheduled job.

THREE THINGS HAD TO BE ADDED, NOT ONE

  the endpoint a carrier can actually post to
  `delivery_check_in` as an allowed follow-up kind, because the CHECK
    constraint would have rejected the queue insert inside a try/except and
    the whole thing would have failed silently
  wording for that kind, because `render` refuses a kind nobody wrote a
    message for and would have queued a message that never sent

AND ONE BUG THE CHAIN EXPOSED

The context stored for the check-in is a brief for the agent, ending "do not
argue on the phone: record what they say and raise it." Every other follow-up
kind stores context written for the customer, so reusing it read as correct
and would have texted our own instructions to the person who took delivery.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def an_order(dbfile, monkeypatch):
    monkeypatch.setenv("CARRIER_WEBHOOK_SECRET", "test-secret")
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-D','D-REF','business','Demo Diner','2026-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-D','A-D','kitchen')")
        c.execute("INSERT INTO contacts (id,account_id,name,role,channel_pref) "
                  "VALUES ('C-D','A-D','Sam Reed','owner','sms')")
        c.execute("INSERT INTO phones (e164,contact_id,label,verified) "
                  "VALUES ('+15551112222','C-D','mobile',1)")
        c.execute("INSERT INTO purchase_orders (id,account_id,site_id,"
                  "contact_id,status,placed_at) VALUES "
                  "('PO-D','A-D','S-D','C-D','shipped','2026-08-25')")
    return "PO-D"


def _client():
    from fastapi.testclient import TestClient

    from src.main import app
    return TestClient(app)


def test_the_carrier_can_actually_reach_it(an_order):
    """The gap. The handler was written as a webhook and had no webhook."""
    from src import db

    r = _client().post("/carrier/delivered",
                       json={"order": an_order, "carrier": "UPS",
                             "tracking": "1Z999AA"},
                       headers={"x-carrier-secret": "test-secret"})
    assert r.json()["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT carrier, carrier_ref FROM deliveries "
                        "WHERE po_id = ?", (an_order,)).fetchone()
    assert row["carrier"] == "UPS"
    assert row["carrier_ref"] == "1Z999AA"


def test_it_refuses_without_the_shared_secret(an_order):
    """An open endpoint here lets a stranger mark somebody's order delivered
    and start a warranty running against them."""
    from src import db

    cl = _client()
    assert cl.post("/carrier/delivered", json={"order": an_order}).json()["ok"] is False
    assert cl.post("/carrier/delivered", json={"order": an_order},
                   headers={"x-carrier-secret": "wrong"}).json()["ok"] is False

    with db.connect() as c:
        assert not c.execute("SELECT 1 FROM deliveries").fetchone()


def test_it_refuses_when_no_secret_is_configured(an_order, monkeypatch):
    """Absent configuration must fail closed, not open."""
    monkeypatch.delenv("CARRIER_WEBHOOK_SECRET", raising=False)

    out = _client().post("/carrier/delivered", json={"order": an_order}).json()
    assert out["ok"] is False
    assert "CARRIER_WEBHOOK_SECRET" in out["why"]


def test_a_resent_webhook_does_not_deliver_twice(an_order):
    """Carriers resend. A duplicate must not produce a second delivery, a
    second warranty correction, or a second call to the customer."""
    from src import db

    cl, hdr = _client(), {"x-carrier-secret": "test-secret"}
    cl.post("/carrier/delivered", json={"order": an_order}, headers=hdr)
    again = cl.post("/carrier/delivered", json={"order": an_order},
                    headers=hdr).json()

    assert again.get("already") is True
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM deliveries").fetchone()["n"] == 1
        assert c.execute("SELECT COUNT(*) n FROM followups "
                         "WHERE kind='delivery_check_in'").fetchone()["n"] == 1


def test_the_check_in_survives_the_kind_constraint(an_order):
    """`followups.kind` carries a CHECK. Without widening it, the insert fails
    inside a try/except and the whole chain goes quiet."""
    from src import db

    _client().post("/carrier/delivered", json={"order": an_order},
                   headers={"x-carrier-secret": "test-secret"})

    with db.connect() as c:
        # NAMED, not "whichever row comes back". Confirming an order also
        # queues the offers-consent question on this account now, so an
        # unfiltered read was asserting against a different message entirely.
        row = c.execute("SELECT kind, phone, status FROM followups "
                        "WHERE account_id = 'A-D' "
                        "AND kind = 'delivery_check_in'").fetchone()
    assert row is not None, "the check-in was never queued"
    assert row["kind"] == "delivery_check_in"
    assert row["phone"] == "+15551112222"


def test_the_customer_never_receives_our_internal_brief(an_order):
    """The context stored for this kind is written for the AGENT and ends
    'do not argue on the phone: record what they say and raise it'. Every
    other kind stores customer-facing context, so reusing it looked right."""
    from src import db
    from src.followup import render

    _client().post("/carrier/delivered", json={"order": an_order},
                   headers={"x-carrier-secret": "test-secret"})

    with db.connect() as c:
        # BY KIND, because confirming an order now also queues the
        # offers-consent question against the same account, and this test is
        # about what the DELIVERY check-in stores. Without the filter it read
        # whichever row came back first and asserted against the wrong one.
        row = c.execute("SELECT * FROM followups WHERE account_id='A-D' "
                        "AND kind='delivery_check_in'").fetchone()
        stored = row["context"] or ""

    assert "do not argue" in stored.lower(), (
        "the fixture no longer reproduces the brief this guards against")

    said = render(row)
    assert said, "no message was rendered, so nothing would send"
    for leak in ("do not argue", "close the order", "raise it"):
        assert leak not in said.lower(), f"internal brief leaked: {leak!r}"
    assert "arrive undamaged" in said


def test_an_order_cannot_be_closed_before_it_is_delivered(an_order):
    """Closing an order that may still be on a van is how somebody stops
    looking for it."""
    from src.tools import confirm_delivery

    out = confirm_delivery(an_order, "ok", "it came")
    assert out["ok"] is False
    assert "no delivery recorded" in out["why"]
    assert "Check where it actually is" in out["say"]


def test_the_customer_confirming_is_what_closes_it(an_order):
    """An order is finished when the person who paid says the right thing
    arrived, not when a van drives away."""
    from src import db
    from src.tools import confirm_delivery

    _client().post("/carrier/delivered", json={"order": an_order},
                   headers={"x-carrier-secret": "test-secret"})

    out = confirm_delivery(an_order, "ok", "yes, arrived fine")
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT checked_in_at, condition FROM deliveries "
                        "WHERE po_id = ?", (an_order,)).fetchone()
    assert row["checked_in_at"]
    assert row["condition"] == "ok"


def test_the_desk_can_reach_both_ends(dbfile):
    """Structural. The handler existing is not the same as anybody being able
    to call it, which is the whole reason this file exists."""
    from src import agents

    for a in (agents.front_agent, agents.desk_agent):
        names = [getattr(t, "__name__", "") for t in a.tools]
        assert "confirm_delivery" in names
        assert "orders_on_the_way" in names
