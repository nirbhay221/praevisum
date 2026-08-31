"""Every public webhook is signed, and the voice one was not.

WHAT WAS OPEN

`/sms`, `/whatsapp` and `/call-status` all verified Twilio's request
signature. `/voice` and `/fallback` did not, and `/voice` is the one that
matters:

    curl -X POST https://<host>/voice -d "From=+15555550000" -d "To=..."
    -> <Stream url="wss://<host>/stream/<VALID TICKET>">

The websocket guard exists to stop a stranger opening a live model session on
our billing. The front door was handing out tickets to anybody who asked, so
the guard protected nothing. That was proven against the live host, not
reasoned about.

And the money is the smaller half. `From` comes out of that request body and
decides who we think is calling. Unsigned, anyone could claim to be any
customer's number and be greeted by name, told which machines they run, and
read their own account history back to them.

This test walks the routes rather than naming them, so a webhook added
tomorrow is covered by default instead of being remembered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# Every POST route that Twilio or Telegram calls from the public internet.
PUBLIC_WEBHOOKS = ["/voice", "/sms", "/whatsapp", "/call-status", "/fallback",
                   "/outbound-voice"]


@pytest.fixture
def client(dbfile, monkeypatch):
    from src import main

    # A token must be held, or signature_ok returns False for a different
    # reason and the test would pass without proving anything. Settings is a
    # frozen dataclass, so the whole object is swapped where it is read.
    import dataclasses

    from src import whatsapp

    monkeypatch.setattr(
        whatsapp, "settings",
        dataclasses.replace(whatsapp.settings, twilio_auth_token="test-token"))
    monkeypatch.delenv("PRAEVISUM_OPEN_WHATSAPP", raising=False)
    return TestClient(main.app)


@pytest.mark.parametrize("route", PUBLIC_WEBHOOKS)
def test_an_unsigned_request_is_refused(client, route):
    r = client.post(route, data={"From": "+15555550000", "To": "+18573617165"})
    assert r.status_code == 403, f"{route} accepted an unsigned request"


def test_an_unsigned_caller_cannot_get_a_stream_ticket(client):
    """The bug, exactly as it was proven live."""
    r = client.post("/voice", data={"From": "+15555550000"})
    assert r.status_code == 403
    assert "stream/" not in r.text


def test_a_signed_request_is_served(client):
    """The guard must not be so tight that Twilio itself is refused."""
    import base64
    import hashlib
    import hmac

    from src import main

    form = {"From": "+13095550101", "To": "+18573617165"}
    url = "http://testserver/voice"
    payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    sig = base64.b64encode(
        hmac.new(b"test-token", payload.encode(), hashlib.sha1).digest()).decode()

    r = client.post("/voice", data=form, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200
    assert "/stream/" in r.text


def test_every_public_post_route_is_covered_by_this_test():
    """A webhook added tomorrow must not quietly escape the list.

    Two are authenticated by something other than a Twilio signature and get
    their own cases below rather than an exemption: /telegram echoes
    Telegram's secret header, and /carrier/delivered takes a shared secret
    because carriers do not sign anything.
    """
    from src import main

    posts = {r.path for r in main.app.routes
             if "POST" in (getattr(r, "methods", None) or set())}

    public = {p for p in posts
              if not p.startswith("/api/")
              and p not in ("/telegram", "/carrier/delivered")}

    assert public <= set(PUBLIC_WEBHOOKS), (
        f"unlisted public POST routes: {public - set(PUBLIC_WEBHOOKS)}")


def test_telegram_is_authenticated_by_its_own_secret(client):
    r = client.post("/telegram", json={"message": {"text": "hello"}})
    assert r.json() == {"ok": False}


def test_the_carrier_hook_is_authenticated_by_a_shared_secret(client,
                                                              monkeypatch):
    """It marks orders delivered and starts warranty clocks, so an open one
    lets a stranger do both. Carriers do not sign their callbacks, so this
    uses a shared secret rather than a signature."""
    monkeypatch.setenv("CARRIER_WEBHOOK_SECRET", "s3cret")

    assert client.post("/carrier/delivered",
                       json={"order": "PO-1"}).json()["ok"] is False
    assert client.post("/carrier/delivered", json={"order": "PO-1"},
                       headers={"x-carrier-secret": "wrong"}
                       ).json()["ok"] is False


def test_the_carrier_hook_fails_closed_with_no_secret_set(client, monkeypatch):
    """Absent configuration must refuse, not wave everything through."""
    monkeypatch.delenv("CARRIER_WEBHOOK_SECRET", raising=False)

    out = client.post("/carrier/delivered", json={"order": "PO-1"}).json()
    assert out["ok"] is False
    assert "CARRIER_WEBHOOK_SECRET" in out["why"]
