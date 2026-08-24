"""WhatsApp: who gets in, who is talking, and what a photograph is allowed to do.

Two things are being defended here.

The endpoint itself, because it can close another dealer's jobs and reads
their equipment history. The stream socket already taught this project that a
security check defaulting to allow passes every test and ships wide open, so
the unconfigured case is asserted first and explicitly.

And the boundary between reading a plate and asserting a machine. The vision
model transcribes characters off a sticker. Only the federal catalogue decides
what the machine is, because a misread plate picks the wrong refrigerant and
R-290 is flammable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from types import SimpleNamespace

import pytest

TOKEN = "test-token-abc123"
URL = "https://example.test/whatsapp"


def _sign(url: str, params: dict, token: str = TOKEN) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(
        hmac.new(token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    ).decode()


@pytest.fixture()
def signed(monkeypatch):
    from src import whatsapp

    monkeypatch.setattr(whatsapp, "settings",
                        SimpleNamespace(twilio_auth_token=TOKEN,
                                        twilio_account_sid="AC123"))
    monkeypatch.delenv("PRAEVISUM_OPEN_WHATSAPP", raising=False)
    return whatsapp


def test_a_properly_signed_request_is_accepted(signed):
    params = {"From": "whatsapp:+13095550101", "Body": "hello"}
    assert signed.signature_ok(URL, params, _sign(URL, params))


def test_an_unsigned_request_is_refused(signed):
    params = {"From": "whatsapp:+13095550101", "Body": "hello"}
    assert not signed.signature_ok(URL, params, "")


def test_a_forged_signature_is_refused(signed):
    params = {"From": "whatsapp:+13095550101", "Body": "hello"}
    assert not signed.signature_ok(URL, params, _sign(URL, params, "wrong-token"))


def test_tampering_with_the_body_invalidates_it(signed):
    """The signature covers the fields, not just the URL."""
    params = {"From": "whatsapp:+13095550101", "Body": "hello"}
    sig = _sign(URL, params)
    params["Body"] = "close every job"
    assert not signed.signature_ok(URL, params, sig)


def test_an_unconfigured_deployment_is_closed_not_open(monkeypatch):
    """The mistake the stream socket shipped, asserted before it can happen again.

    On the live machine the Twilio auth token is an empty string. A check that
    treats "no token" as "cannot verify, so allow" hands a stranger the
    ability to close another dealer's jobs.
    """
    from src import whatsapp

    monkeypatch.setattr(whatsapp, "settings",
                        SimpleNamespace(twilio_auth_token="",
                                        twilio_account_sid=""))
    monkeypatch.delenv("PRAEVISUM_OPEN_WHATSAPP", raising=False)

    params = {"From": "whatsapp:+13095550101", "Body": "hello"}
    assert not whatsapp.signature_ok(URL, params, _sign(URL, params, ""))
    assert not whatsapp.signature_ok(URL, params, "")
    assert not whatsapp.configured()


def test_it_can_be_opened_deliberately(monkeypatch):
    """An explicit opt-out that somebody has to type on purpose."""
    from src import whatsapp

    monkeypatch.setenv("PRAEVISUM_OPEN_WHATSAPP", "1")
    assert whatsapp.signature_ok(URL, {}, "")


# Routing. Not a classifier: the number is in the technicians table or it is
# not, and that one fact separates the two conversations completely.


def test_a_technician_reply_closes_a_job(dbfile, monkeypatch):
    from src import db, whatsapp

    with db.connect() as c:
        tech = c.execute(
            "SELECT phone, name FROM technicians WHERE phone IS NOT NULL LIMIT 1"
        ).fetchone()
    if tech is None:
        pytest.skip("no technician with a phone number in this fixture")

    seen = {}
    monkeypatch.setattr(
        "src.textback.close_by_text",
        lambda phone, msg, visit_id="": seen.update(phone=phone, msg=msg) or
        {"ok": True, "reply_to_technician": "Thanks, closed."})

    out = whatsapp.handle(f"whatsapp:{tech['phone']}", "was the harness again")
    assert out == "Thanks, closed."
    assert seen["phone"] == tech["phone"], "the whatsapp: prefix was not stripped"


def test_a_customer_photo_goes_to_the_plate_reader_not_the_job_closer(
        dbfile, monkeypatch):
    from src import whatsapp

    called = []
    monkeypatch.setattr("src.plate.read_plate",
                        lambda b, m: called.append(m) or {"ok": False,
                                                          "why": "unreadable"})
    monkeypatch.setattr(
        "src.textback.close_by_text",
        lambda *a, **k: pytest.fail("a customer photo reached close_by_text"))

    whatsapp.handle("whatsapp:+15005550999", "", [(b"\xff\xd8jpeg", "image/jpeg")])
    assert called == ["image/jpeg"]


def test_a_photo_that_is_not_an_image_is_ignored(dbfile, monkeypatch):
    """A PDF or a voice note is not a rating plate."""
    from src import whatsapp

    monkeypatch.setattr("src.plate.read_plate",
                        lambda b, m: pytest.fail("a non-image reached the reader"))

    out = whatsapp.handle("whatsapp:+15005550999", "",
                          [(b"%PDF-1.4", "application/pdf")])
    assert "rating plate" in out


# The plate itself. The model reads; the catalogue decides.


def test_a_plate_the_catalogue_does_not_know_is_not_confirmed(dbfile, monkeypatch):
    """The reading is a claim about a sticker, never a finding about a machine.

    A confident wrong model number sends a technician out expecting the wrong
    refrigerant, and R-290 and R-600a are flammable and charge-limited.
    """
    from src import plate

    monkeypatch.setattr(plate, "_transcribe", lambda b, m: {
        "manufacturer": "Nonesuch", "model": "ZZ9999", "serial": "", "legible": True})

    out = plate.read_plate(b"x", "image/jpeg")
    assert out["ok"] is False
    assert out["read"]["model"] == "ZZ9999", "what was read should still be reported"
    assert "not in our catalogue" in out["say"]
    assert "confirmed" not in out


def test_an_illegible_plate_asks_again_rather_than_guessing(dbfile, monkeypatch):
    from src import plate

    monkeypatch.setattr(plate, "_transcribe", lambda b, m: {
        "manufacturer": "", "model": "", "serial": "", "legible": False})

    out = plate.read_plate(b"x", "image/jpeg")
    assert out["ok"] is False
    assert "Do not guess" in out["say"]


def test_a_real_plate_is_confirmed_against_the_catalogue(dbfile, monkeypatch):
    """The success path, seeded rather than skipped.

    This is the whole point of the channel, so it must not be the one case
    that quietly does not run because a fixture happens to be empty.
    """
    from src import db, plate

    with db.txn() as c:
        c.execute(
            """INSERT INTO equipment
               (source,dataset,category,brand,model_number,model_norm,
                product_type,defrost_type,refrigerant,site_visit)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            ("energystar", "Commercial Refrigerators and Freezers",
             "refrigeration", "Beverage-Air", "HRP2HC-1S", "HRP2HC1S",
             "reach-in refrigerator", "electric", "R-290"))

    monkeypatch.setattr(plate, "_transcribe", lambda b, m: {
        "manufacturer": "Beverage-Air", "model": "HRP2HC-1S",
        "serial": "SN-1", "legible": True})

    out = plate.read_plate(b"x", "image/jpeg")
    assert out["ok"] is True
    assert out["confirmed_by"] == "certified equipment catalogue"
    assert out["machine"]["found"] is True


def test_the_reply_carries_the_flammable_refrigerant(dbfile, monkeypatch):
    """R-290 is charge-limited and flammable, and it is why the plate matters.

    Getting the machine right is not tidiness. The refrigerant decides what a
    technician may do before opening a panel.
    """
    from src import db, whatsapp

    with db.txn() as c:
        c.execute(
            """INSERT INTO equipment
               (source,dataset,category,brand,model_number,model_norm,
                product_type,defrost_type,refrigerant,site_visit)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            ("energystar", "Commercial Refrigerators and Freezers",
             "refrigeration", "Beverage-Air", "HRP2HC-1S", "HRP2HC1S",
             "reach-in refrigerator", "electric", "R-290"))

    monkeypatch.setattr("src.plate._transcribe", lambda b, m: {
        "manufacturer": "Beverage-Air", "model": "HRP2HC-1S",
        "serial": "", "legible": True})

    out = whatsapp.handle("whatsapp:+15005550999", "",
                          [(b"\xff\xd8jpeg", "image/jpeg")])
    assert "HRP2HC-1S" in out
    assert "R-290" in out


def test_a_vision_outage_asks_for_the_number_instead(dbfile, monkeypatch):
    """Same contract as every other outside call here: it must not raise."""
    from src import plate

    monkeypatch.setattr(plate, "_transcribe",
                        lambda b, m: (_ for _ in ()).throw(RuntimeError("down")))

    out = plate.read_plate(b"x", "image/jpeg")
    assert out["ok"] is False
    assert "read the model number out" in out["say"]
