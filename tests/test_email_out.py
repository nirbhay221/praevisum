"""Sending email, and refusing to send the kind that would break the law.

WHY EMAIL EXISTS HERE AT ALL

Two reasons, and the second is the real one.

A2P 10DLC. US business SMS must be registered with the carriers and this
number is not, so every SMS reply the desk sent came back error 30034,
undelivered. On 28 August a customer texted in, the desk answered, and the
carrier dropped it: one-sided to them, complete to us.

AND A TECHNICIAN IS NOT A CUSTOMER. `desk.py` routes on whether the sender is
in the technicians table, so one phone number cannot be both. Email gives the
crew an identity of their own, which is what lets a briefing go out and be
replied to without competing with the customer channel.

THE RULE BEING ENFORCED

CAN-SPAM exempts transactional and relationship messages -- an ongoing
transaction, an employment relationship, an account update -- and exempts
nothing whose primary purpose is promoting the business. A message mixing both
is judged on its primary purpose.

    briefing to an engineer       employment relationship   exempt
    "your part arrives Tuesday"   ongoing transaction       exempt
    "would you leave a review"    promoting the business    NOT exempt

So a commercial message carries a working unsubscribe and a real physical
postal address, or it does not go. Enforced in code rather than trusted to
whoever writes the copy, for the same reason guards.py exists.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# which rules apply
# --------------------------------------------------------------------------

def test_a_transactional_message_needs_nothing_attached(dbfile):
    """An engineer cannot opt out of being told where the job is, and adding
    an unsubscribe to a work instruction would be worse than useless."""
    from src.email_out import TRANSACTIONAL, check_before_sending

    assert check_before_sending(
        TRANSACTIONAL, "your part arrives Tuesday", "", "") is None


def test_a_commercial_message_without_an_unsubscribe_is_refused(dbfile):
    from src.email_out import COMMERCIAL, check_before_sending

    out = check_before_sending(COMMERCIAL, "leave us a review", "",
                               "12 Brady Street, Davenport IA 52801")
    assert out is not None
    assert "unsubscribe" in out["why"]


def test_a_commercial_message_without_a_postal_address_is_refused(dbfile):
    """CAN-SPAM requires a real physical postal address on commercial mail.
    Not a placeholder."""
    from src.email_out import COMMERCIAL, check_before_sending

    out = check_before_sending(COMMERCIAL, "leave us a review",
                               "https://example.test/stop", "")
    assert out is not None
    assert "postal address" in out["why"]


def test_a_placeholder_address_does_not_count(dbfile):
    """A blank is caught by anybody. "address here" is the one that ships,
    because it looks filled in."""
    from src.email_out import COMMERCIAL, check_before_sending

    assert check_before_sending(COMMERCIAL, "x", "https://example.test/stop",
                                "address here") is not None


def test_a_complete_commercial_message_is_allowed(dbfile):
    from src.email_out import COMMERCIAL, check_before_sending

    assert check_before_sending(
        COMMERCIAL, "leave us a review", "https://example.test/stop",
        "12 Brady Street, Davenport IA 52801") is None


def test_an_unknown_kind_is_refused_rather_than_assumed(dbfile):
    """Defaulting an unrecognised kind to transactional would make the whole
    check optional: anybody could skip it by inventing a word."""
    from src.email_out import check_before_sending

    out = check_before_sending("marketing-ish", "buy things", "", "")
    assert out is not None
    assert "not a kind of message" in out["why"]


# --------------------------------------------------------------------------
# what actually goes out
# --------------------------------------------------------------------------

def test_the_footer_is_added_here_not_trusted_to_the_copy(dbfile,
                                                          monkeypatch):
    """A footer that is sometimes present is a footer that will be missing on
    the message somebody complains about."""
    from src import email_out

    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent["body"] = msg.get_content()
            sent["headers"] = dict(msg.items())

    # settings is a frozen dataclass, so it is replaced wholesale rather than
    # mutated. Trying to assign a field raises FrozenInstanceError, which is
    # the config being immutable on purpose.
    from types import SimpleNamespace

    monkeypatch.setattr(email_out.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_out, "configured", lambda: True)
    monkeypatch.setattr(email_out, "settings", SimpleNamespace(
        email_from="desk@example.test", smtp_host="x", smtp_port=587,
        smtp_user="u", smtp_password="p"))

    out = email_out.send("them@example.test", "Nice job",
                         "Glad that sorted it.",
                         kind=email_out.COMMERCIAL,
                         unsubscribe="https://example.test/stop",
                         postal_address="12 Brady Street, Davenport IA 52801")

    assert out["ok"] is True
    assert "https://example.test/stop" in sent["body"]
    assert "12 Brady Street" in sent["body"]
    assert "List-Unsubscribe" in sent["headers"]


def test_a_briefing_carries_no_unsubscribe(dbfile, monkeypatch):
    """It is an employment relationship. An engineer opting out of job
    details is not a thing that should be offered."""
    from src import email_out

    captured = {}

    def fake_send(to, subject, body, kind=email_out.TRANSACTIONAL, **kw):
        captured.update({"kind": kind, "body": body, "kw": kw})
        return {"ok": True}

    monkeypatch.setattr(email_out, "send", fake_send)

    email_out.brief_the_engineer("tech@example.test", {
        "work_order_id": "WO-1", "customer": "Coriander House",
        "machine": "True TUC-27F", "reported": "not holding overnight",
        "safety": "R-290, flammable refrigerant",
        "load_these": [{"name": "Door gasket", "sku": "P-DOORGASKET",
                        "why": "54% of these"}],
    })

    assert captured["kind"] == email_out.TRANSACTIONAL
    assert not captured["kw"].get("unsubscribe")
    assert "SAFETY" in captured["body"]
    assert "Door gasket" in captured["body"]
    assert "Reply to this email when the job is done" in captured["body"]


def test_nothing_is_sent_without_credentials(dbfile):
    from src import email_out

    out = email_out.send("them@example.test", "hi", "hello")
    if out["ok"] is False:
        assert "credentials" in out["why"] or "mail server" in out["why"]


def test_a_bad_address_is_refused_before_the_server_is_touched(dbfile):
    from src import email_out

    assert email_out.send("not-an-address", "hi", "hello")["ok"] is False
