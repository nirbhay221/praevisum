"""Who is allowed to open the audio socket.

The socket was reachable by anyone who knew the hostname. A scanner found the
host within minutes of the DNS record going live, and an open socket here is
not a defaced page: it is a live model session on our billing, wired to a real
dealer's customer data.

Twilio cannot send an Authorization header on a Media Streams connection, so
the ticket rides in the URL that /voice hands out moments earlier.
"""

from __future__ import annotations

import re
import time
from types import SimpleNamespace

import pytest


def _settings(monkeypatch, token, ws_base="wss://example.test"):
    """Swap the module's settings object and reset the cached signing key.

    Settings is a frozen dataclass, so the field cannot be assigned. Replacing
    the reference is both simpler and closer to the truth: configuration is
    read at use, not captured at import.
    """
    from src import main

    fake = SimpleNamespace(twilio_auth_token=token, public_ws_base=ws_base)
    monkeypatch.setattr(main, "settings", fake)
    monkeypatch.setattr(main, "_key_cache", None)
    monkeypatch.delenv("PRAEVISUM_OPEN_STREAM", raising=False)
    monkeypatch.setenv("PRAEVISUM_STREAM_SECRET", token or "generated-for-test")
    return fake


@pytest.fixture()
def signing(monkeypatch):
    """The ordinary production case: a secret exists and tickets are signed."""
    return _settings(monkeypatch, "test-token-abc123")



def _signed(client, path, form, monkeypatch):
    """POST as Twilio would, signature and all.

    /voice and /outbound-voice hand out stream tickets and are now signed, so
    a test that posts unsigned is testing the signature rather than the
    ticket. See tests/test_webhook_auth.py for the signature itself.
    """
    import base64
    import dataclasses
    import hashlib
    import hmac

    from src import whatsapp

    monkeypatch.setattr(
        whatsapp, "settings",
        dataclasses.replace(whatsapp.settings, twilio_auth_token="test-token"))

    url = f"http://testserver{path}"
    payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    sig = base64.b64encode(
        hmac.new(b"test-token", payload.encode(), hashlib.sha1).digest()).decode()
    return client.post(path, data=form, headers={"X-Twilio-Signature": sig})


def test_a_fresh_ticket_is_accepted(signing):
    from src import main

    assert main._ticket_ok(main._issue_ticket())


def test_no_ticket_is_refused(signing):
    from src import main

    assert not main._ticket_ok("")


def test_rubbish_is_refused(signing):
    """Most of what arrives at a public websocket is a scanner."""
    from src import main

    for junk in ("x", "....", "abc.def", "9999999999.", ".", "0.0",
                 "9999999999.deadbeef", "../../etc/passwd"):
        assert not main._ticket_ok(junk), junk


def test_an_expired_ticket_is_refused(signing, monkeypatch):
    """A captured URL has to stop working."""
    from src import main

    ticket = main._issue_ticket()
    monkeypatch.setattr(main.time, "time", lambda: (1 << 40))
    assert not main._ticket_ok(ticket)


def test_a_forged_ticket_is_refused(signing):
    """Knowing the format is not enough without the key."""
    from src import main

    expires = int(time.time()) + 60
    assert not main._ticket_ok(f"{expires}.{'0' * 32}")


def test_a_ticket_signed_with_another_key_is_refused(signing, monkeypatch):
    """The signature is actually checked against our key, not merely present."""
    from src import main

    ticket = main._issue_ticket()
    _settings(monkeypatch, "a-different-token")
    assert not main._ticket_ok(ticket)


def test_the_voice_handler_hands_out_a_usable_ticket(signing, monkeypatch):
    """End to end: whatever /voice puts in the URL must open the socket.

    Worth asserting rather than assuming. A signing change that broke this
    would not fail loudly in testing, it would fail as every inbound call
    being hung up on.

    Which is exactly what happened, and this test did not catch it, because it
    read the ticket out of the query string the way a Python client would.
    Twilio drops the query string. It now reads it out of the path, the way
    Twilio gets it.
    """
    import asyncio
    from urllib.parse import urlsplit

    from src import main

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    twiml = _signed(client, "/voice",
                    {"From": "+13095550101", "To": "+13095550000"},
                    monkeypatch).text
    url = twiml.split('<Stream url="')[1].split('"')[0]
    ticket = urlsplit(url).path.removeprefix("/stream/")

    assert main._ticket_ok(ticket)


def test_an_unconfigured_deployment_is_still_closed(monkeypatch, tmp_path):
    """The bug this file exists for, and the one the first version shipped.

    Keying on the Twilio auth token looked reasonable and passed every test,
    because every test supplied a token. On the live machine that value is an
    empty string, so tickets were verified against an empty key and the socket
    accepted anything on the public internet. Absent configuration must never
    mean an open door.
    """
    from src import main

    monkeypatch.setattr(main, "settings",
                        SimpleNamespace(twilio_auth_token="",
                                        public_ws_base="wss://example.test"))
    monkeypatch.setattr(main, "_key_cache", None)
    monkeypatch.setattr(main, "_KEY_FILE", tmp_path / ".stream_secret")
    monkeypatch.delenv("PRAEVISUM_STREAM_SECRET", raising=False)
    monkeypatch.delenv("PRAEVISUM_OPEN_STREAM", raising=False)

    assert not main._ticket_ok(""), "an unconfigured deployment is wide open"
    assert not main._ticket_ok(f"{int(time.time()) + 60}.{'0' * 32}")

    # and it must still work for the calls it is meant to serve
    assert main._ticket_ok(main._issue_ticket())


def test_the_generated_secret_survives_a_restart(monkeypatch, tmp_path):
    """A ticket issued before a restart should not be rejected after it."""
    from src import main

    key_file = tmp_path / ".stream_secret"
    monkeypatch.setattr(main, "settings",
                        SimpleNamespace(twilio_auth_token="",
                                        public_ws_base="wss://example.test"))
    monkeypatch.setattr(main, "_KEY_FILE", key_file)
    monkeypatch.delenv("PRAEVISUM_STREAM_SECRET", raising=False)

    monkeypatch.setattr(main, "_key_cache", None)
    first = main._stream_key()

    monkeypatch.setattr(main, "_key_cache", None)   # as if freshly started
    assert main._stream_key() == first


def test_the_socket_can_be_opened_deliberately(monkeypatch):
    """An explicit opt-out for poking at it by hand, which must be asked for."""
    from src import main

    monkeypatch.setenv("PRAEVISUM_OPEN_STREAM", "1")
    assert main._ticket_ok("")


# ---------------------------------------------------------------------------
# The way Twilio actually connects, which is not the way a Python client does.
# ---------------------------------------------------------------------------
#
# Every test above reaches the socket by handing it a ticket the way a script
# would. Twilio does not. It reads the <Stream url> out of the TwiML, THROWS
# THE QUERY STRING AWAY, and connects to the bare path.
#
# So the guard refused every real call, correctly, and the line stopped
# answering. The guard went in on 21 August, the last call that connected was
# on the 18th, and nobody rang in between. The journal said it in two lines:
#
#     "WebSocket /stream" 403                       <- Twilio, no query
#     "WebSocket /stream?t=1787718544..." accepted  <- everything else
#
# These tests reproduce Twilio's shape rather than a client's.


def test_the_ticket_survives_being_stripped_of_its_query_string(signing, monkeypatch):
    """The bug, as a test. Take the URL the TwiML hands out, drop the query
    string exactly as Twilio does, and it must still connect."""
    from urllib.parse import urlsplit

    from fastapi.testclient import TestClient

    from src import main

    monkeypatch.setattr(main.settings, "public_ws_base", "wss://example.test")
    client = TestClient(main.app)

    twiml = _signed(client, "/voice",
                    {"From": "+13095550101", "To": "+18573617165"},
                    monkeypatch).text
    url = re.search(r'url="([^"]+)"', twiml).group(1)

    parts = urlsplit(url)
    assert parts.query == "", (
        "the ticket must not live in the query string: Twilio drops it and "
        "the call is refused before anybody says a word")
    assert parts.path.startswith("/stream/"), "so it lives in the path"

    # And the path form is what the socket actually honours.
    with client.websocket_connect(parts.path) as ws:
        ws.close()


def test_the_bare_path_with_no_ticket_is_still_refused(signing):
    """Moving the ticket into the path must not have opened the socket to
    anyone who simply omits it."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from src import main

    client = TestClient(main.app)
    with pytest.raises((WebSocketDisconnect, Exception)):
        with client.websocket_connect("/stream") as ws:
            ws.receive_text()


def test_a_forged_ticket_in_the_path_is_refused(signing):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from src import main

    client = TestClient(main.app)
    with pytest.raises((WebSocketDisconnect, Exception)):
        with client.websocket_connect(f"/stream/{int(time.time()) + 60}.{'0' * 32}") as ws:
            ws.receive_text()


def test_the_outbound_twiml_carries_the_ticket_the_same_way(signing, monkeypatch):
    """The mirror of /voice had the identical bug and would have failed the
    first time it dialled anybody."""
    from urllib.parse import urlsplit

    from fastapi.testclient import TestClient

    from src import main

    monkeypatch.setattr(main.settings, "public_ws_base", "wss://example.test")
    client = TestClient(main.app)

    twiml = _signed(client, "/outbound-voice", {"outreach": "OUT-1"},
                    monkeypatch).text
    url = re.search(r'url="([^"]+)"', twiml).group(1)
    assert urlsplit(url).query == ""
    assert urlsplit(url).path.startswith("/stream/")
