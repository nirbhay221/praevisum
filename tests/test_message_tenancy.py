"""A text message knows which business it was sent to, and we threw it away.

THE THIRD LEAK OF THE SAME SHAPE

On the phone, the number dialled decides whose customers, technicians and
repair corpus apply. Twilio sends exactly the same thing on every SMS and
WhatsApp webhook, as `To`, and the handler read only `From` and `Body`.

So the desk had one route left: look the sender up and take the dealer off
their account. That works for somebody we already know and fails for the
person who needs it most, a first-time customer, who fell through to a
hardcoded "D-REF". An IT customer texting the IT number was answered as a
refrigeration company.

Telegram genuinely has no dialled number, so the account remains the fallback
there rather than the primary route.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def numbers(dbfile):
    from src import db

    with db.connect() as c:
        return {r["id"]: r["phone_e164"]
                for r in c.execute("SELECT id, phone_e164 FROM dealers")}


def test_a_stranger_reaches_the_business_they_messaged(numbers):
    """The case that was broken: nobody we know, so no account to resolve."""
    from src import desk

    for dealer_id, number in numbers.items():
        assert desk._dealer_for({"account_id": None}, number) == dealer_id


def test_the_dialled_number_beats_the_account(numbers, dbfile):
    """Somebody can hold accounts with both businesses. What they messaged is
    the better evidence of which one they meant."""
    from src import db, desk

    with db.txn() as c:
        c.execute("UPDATE accounts SET dealer_id='D-REF' WHERE id='A-1'")

    assert desk._dealer_for({"account_id": "A-1"}, numbers["D-IT"]) == "D-IT"


def test_a_channel_with_no_dialled_number_uses_the_account(dbfile):
    """Telegram has no equivalent, so the old route is still the right one
    there rather than a bug."""
    from src import db, desk

    with db.txn() as c:
        c.execute("UPDATE accounts SET dealer_id='D-IT' WHERE id='A-1'")

    assert desk._dealer_for({"account_id": "A-1"}, "") == "D-IT"


def test_an_unrecognised_number_does_not_pick_a_business_at_random(dbfile):
    from src import desk

    assert desk._dealer_for({"account_id": None}, "+15550000000") == "D-REF"


# The plumbing that carries it.


def test_the_desk_accepts_the_dialled_number(dbfile):
    import inspect

    from src import desk

    assert "dialled" in inspect.signature(desk.answer).parameters


def test_the_sms_webhook_passes_it_on(dbfile):
    import inspect

    from src import main

    src = inspect.getsource(main._sms_reply)
    assert 'form.get("To"' in src, "the To field was being discarded"
    assert "dialled=" in src


def test_the_whatsapp_webhook_passes_it_on(dbfile):
    import inspect

    from src import main, whatsapp

    assert "to_number" in inspect.signature(whatsapp.handle).parameters
    assert "dialled=" in inspect.getsource(whatsapp.handle)

    # The webhook hands the form to a helper that runs in a worker thread,
    # because fetching an attachment blocks.
    helper = inspect.getsource(main._whatsapp_reply)
    assert 'to_number=form.get("To"' in helper
