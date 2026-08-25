"""One desk, several doors, and the ways that could go wrong.

Customers pick the channel and we do not, so the number of doors is a business
question. What is not negotiable is that they all reach the same desk. Two
channels answering the same question differently is worse than one channel,
because the desk has been caught contradicting itself and the customer cannot
tell which answer was true.

The bug these tests exist because of: the first WhatsApp build sent customers
to the advice agent, since the front agent runs on the Live audio model and
cannot serve text. Advice can talk about what to buy and nothing else. So on
that channel a customer could not register a complaint, get a delivery quote,
place an order or book a visit, and nothing said so.
"""

from __future__ import annotations

import pytest


def _names(tools) -> set:
    return {getattr(t, "__name__", None) or getattr(t, "name", "") for t in tools}


def test_the_text_desk_can_do_everything_the_phone_can(dbfile):
    """The regression that started this. A channel must not be a lesser product."""
    from src import agents

    phone = _names(agents.front_agent.tools)
    text = _names(agents.desk_agent.tools)

    assert phone == text, (
        "the text desk and the phone desk have drifted apart: "
        f"only on the phone {sorted(phone - text)}, "
        f"only in text {sorted(text - phone)}")


def test_a_text_customer_can_complain_quote_order_and_book(dbfile):
    """Named explicitly, because these are the four that were silently missing.

    Asserting equality with the phone agent would pass if both lost a tool.
    """
    from src import agents

    text = _names(agents.desk_agent.tools)
    for tool in ("register_complaint", "register_return", "open_work_order",
                 "promise_slot", "set_intent"):
        assert tool in text, f"a customer who types cannot {tool}"

    # Ordering and quoting live on the supply agent, booking on scheduling.
    assert {"supply", "scheduling", "advice", "assessment"} <= text


def test_the_text_desk_is_not_on_the_voice_model(dbfile):
    """The Live native-audio model is voice only and regional.

    Pointing a text channel at it is how the reduced-agent bug happened in the
    first place, so it is worth an assertion rather than a comment.
    """
    from src import agents
    from src.config import settings

    assert agents.desk_agent.model != settings.live_model


def test_both_channels_share_one_copy_of_the_rules(dbfile):
    """Not two texts that happen to agree today.

    A part quoted as in stock on WhatsApp and out of stock on the phone is the
    desk caught lying, whichever answer was right.
    """
    from src import agents

    assert agents.DESK_RULES in agents.FRONT_INSTRUCTION
    assert agents.DESK_RULES in agents.DESK_INSTRUCTION

    # and each still says the thing only its own medium needs
    assert "press a key" in agents.FRONT_INSTRUCTION
    assert "press a key" not in agents.DESK_INSTRUCTION
    assert "photograph" in agents.DESK_INSTRUCTION.lower()


# Routing, which is one fact rather than a classifier.


def test_a_technician_closes_a_job_from_any_channel(dbfile, monkeypatch):
    from src import db, desk

    with db.connect() as c:
        tech = c.execute(
            "SELECT phone FROM technicians WHERE phone IS NOT NULL LIMIT 1"
        ).fetchone()
    if tech is None:
        pytest.skip("no technician with a phone number in this fixture")

    seen = []
    monkeypatch.setattr(
        "src.textback.close_by_text",
        lambda phone, msg, visit_id="": seen.append(phone) or
        {"ok": True, "reply_to_technician": "Thanks, closed."})

    for channel in ("whatsapp", "telegram", "sms"):
        assert desk.answer(tech["phone"], "was the harness", channel=channel) \
            == "Thanks, closed."
    assert seen == [tech["phone"]] * 3


def test_a_customer_never_reaches_the_job_closer(dbfile, monkeypatch):
    from src import desk

    monkeypatch.setattr(
        "src.textback.close_by_text",
        lambda *a, **k: pytest.fail("a customer reached close_by_text"))
    monkeypatch.setattr("src.desk._converse",
                        lambda key, text, *a, **k: "advice")

    assert desk.answer("+15005550999", "what freezer should I buy") == "advice"


def test_each_person_gets_their_own_conversation(dbfile, monkeypatch):
    """Two customers must not share a session, and neither must two channels.

    The same human on WhatsApp and on Telegram is two threads as far as either
    app is concerned, and merging them would have the desk answer a question
    that was asked somewhere else.
    """
    from src import desk

    keys = []
    monkeypatch.setattr("src.desk._converse",
                        lambda key, text, *a, **k: keys.append(key) or "ok")

    desk.answer("+15005550999", "hello", channel="whatsapp")
    desk.answer("+15005550999", "hello", channel="telegram")
    desk.answer("+15005550111", "hello", channel="whatsapp")

    assert len(set(keys)) == 3, f"sessions collided: {keys}"


def test_the_desk_works_when_called_from_inside_an_event_loop(dbfile, monkeypatch):
    """The bug every unit test here missed, found by one real signed request.

    The webhooks are async, so they run inside a loop. `_converse` answers a
    model turn with asyncio.run, which raises outright when a loop is already
    running. Every customer message came back "ring the desk and somebody will
    pick up", and the bare except meant nothing said why.

    Calling desk.answer directly, as the other tests do, has no running loop
    and so cannot see it. This one goes through the endpoint.
    """
    import asyncio

    from src import main

    monkeypatch.setenv("PRAEVISUM_OPEN_WHATSAPP", "1")
    monkeypatch.setattr("src.desk._converse",
                        lambda key, text, *a, **k: "the real answer")

    class _Req:
        headers: dict = {}
        url = "https://example.test/whatsapp"

        async def form(self):
            return {"From": "whatsapp:+15005550999", "Body": "hello",
                    "NumMedia": "0"}

    out = asyncio.run(main.whatsapp_webhook(_Req()))
    body = getattr(out, "body", b"").decode() or str(out)

    assert "the real answer" in body
    assert "could not get to that" not in body, \
        "the desk failed inside the event loop, as it did in production"


# Telegram, whose identity model is genuinely weaker and says so.


def test_an_unlinked_telegram_chat_is_a_customer_not_a_technician(dbfile):
    """Telegram does not give out phone numbers, and nothing is guessed.

    Matching on a display name would write a repair against another
    technician's visit, silently, into the corpus every briefing is built on.
    """
    from src import telegram

    assert telegram._identity("123456") == "telegram:123456"


def test_linking_refuses_a_number_that_is_not_a_technician(dbfile):
    """The only way to become a technician on this channel, so it is strict."""
    from src import telegram

    out = telegram.link("123456", "+15005550999")
    assert out["ok"] is False
    assert "not a technician" in out["why"]


def test_a_linked_chat_resolves_to_the_technician(dbfile):
    from src import db, telegram

    with db.connect() as c:
        tech = c.execute(
            "SELECT phone FROM technicians WHERE phone IS NOT NULL LIMIT 1"
        ).fetchone()
    if tech is None:
        pytest.skip("no technician with a phone number in this fixture")

    assert telegram.link("999888", tech["phone"])["ok"] is True
    assert telegram._identity("999888") == tech["phone"]


def test_an_unsigned_telegram_webhook_is_refused(monkeypatch):
    """Same rule as everywhere: absent configuration closes the door."""
    from src import telegram

    monkeypatch.delenv("PRAEVISUM_OPEN_TELEGRAM", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    assert not telegram.secret_ok("anything")

    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    assert not telegram.secret_ok("")
    assert not telegram.secret_ok("wrong")
    assert telegram.secret_ok("s3cret")


def test_telegram_takes_the_largest_photo_offered(dbfile, monkeypatch):
    """Telegram sends several sizes, smallest first.

    Reading characters off a sticker from the thumbnail would fail for a
    reason nobody would ever guess from the output.
    """
    from src import telegram

    asked = []
    monkeypatch.setattr(telegram, "fetch_photo",
                        lambda fid: (asked.append(fid), (b"jpeg", "image/jpeg"))[1])
    monkeypatch.setattr("src.desk.answer", lambda *a, **k: "ok")

    telegram.handle({"message": {
        "chat": {"id": 42},
        "photo": [{"file_id": "thumb"}, {"file_id": "medium"},
                  {"file_id": "full"}]}})

    assert asked == ["full"]


def test_a_telegram_caption_is_read_as_the_message(dbfile, monkeypatch):
    """People send a photo and type the fault underneath it in one go."""
    from src import telegram

    seen = {}
    monkeypatch.setattr(telegram, "fetch_photo", lambda fid: (b"jpeg", "image/jpeg"))
    monkeypatch.setattr("src.desk.answer",
                        lambda ident, text, media, channel: seen.update(
                            text=text, media=len(media)) or "ok")

    telegram.handle({"message": {
        "chat": {"id": 42},
        "caption": "not holding temperature since last night",
        "photo": [{"file_id": "full"}]}})

    assert seen["text"] == "not holding temperature since last night"
    assert seen["media"] == 1


# Everything the phone line establishes before the caller speaks, which the
# text channels did not do at all. Not a cosmetic gap: it was a tenancy leak,
# an anonymous customer, and a conversation invisible to every measurement.


def test_a_customer_we_know_is_recognised_on_a_text_channel(dbfile, monkeypatch):
    """The phone greets them by name and knows their machines. A message
    thread greeted a customer of eight years as a stranger."""
    from src import db, desk

    with db.connect() as c:
        row = c.execute(
            """SELECT p.e164, ct.name FROM phones p
               JOIN contacts ct ON ct.id = p.contact_id LIMIT 1""").fetchone()
    if row is None:
        pytest.skip("fixture has no contact with a phone")

    seen = {}
    monkeypatch.setattr("src.desk._converse",
                        lambda key, text, who=None, dealer="", call_id="":
                        seen.update(who=who, dealer=dealer) or "ok")

    desk.answer(row["e164"], "the freezer is warm", channel="whatsapp")
    assert seen["who"]["known"] is True, "a known customer arrived as a stranger"
    assert seen["who"]["contact_name"] == row["name"]


def test_a_text_customer_reaches_their_own_dealer(dbfile, monkeypatch):
    """The leak. `dealer_id` was the literal string "D-REF", so an IT customer
    messaging in was answered out of the refrigeration book."""
    from src import db, desk

    with db.connect() as c:
        row = c.execute(
            """SELECT p.e164 FROM phones p
               JOIN contacts ct ON ct.id = p.contact_id
               JOIN accounts a ON a.id = ct.account_id
               WHERE a.dealer_id = 'D-IT' LIMIT 1""").fetchone()
    if row is None:
        pytest.skip("fixture has no IT customer with a phone")

    seen = {}
    monkeypatch.setattr("src.desk._converse",
                        lambda key, text, who=None, dealer="", call_id="":
                        seen.update(dealer=dealer) or "ok")

    desk.answer(row["e164"], "the laptop will not charge", channel="whatsapp")
    assert seen["dealer"] == "D-IT", \
        "an IT customer was answered out of the refrigeration book"


def test_a_message_thread_gets_a_call_row_like_a_call_does(dbfile, monkeypatch):
    """Without one, review.py cannot settle it and patterns.py cannot see it,
    so the desk measures only half of what it does."""
    from src import db, desk

    monkeypatch.setattr("src.desk._converse",
                        lambda key, text, *a, **k: "ok")
    desk.answer("+15005550777", "the walk-in is warm", channel="whatsapp")

    with db.connect() as c:
        row = c.execute(
            "SELECT id, from_e164 FROM calls WHERE from_e164='+15005550777'"
        ).fetchone()
    assert row is not None, "a message thread left no record"
    assert row["id"].startswith("MSG-"), "a message is not a phone call"


def test_a_continuing_thread_stays_one_conversation(dbfile, monkeypatch):
    """Three messages about one freezer is one conversation, not three."""
    from src import db, desk

    monkeypatch.setattr("src.desk._converse", lambda key, text, *a, **k: "ok")
    for msg in ("the walk-in is warm", "it started last night", "can you come"):
        desk.answer("+15005550888", msg, channel="whatsapp")

    with db.connect() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM calls WHERE from_e164='+15005550888'"
        ).fetchone()[0]
    assert n == 1, f"one thread became {n} conversations"


def test_a_thread_that_went_quiet_starts_a_new_conversation(dbfile, monkeypatch):
    """Somebody messaging about a freezer on Tuesday and a fryer on Friday is
    not continuing anything, and stapling them together would have the desk
    answer the second with the first one's context."""
    from datetime import datetime, timedelta

    from src import db, desk

    old = (datetime.now() - timedelta(hours=desk.CONVERSATION_HOURS + 1))
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,started_at,dealer_id)
               VALUES ('MSG-OLD','+15005550999',?,'D-REF')""",
            (old.isoformat(timespec="seconds"),))

    monkeypatch.setattr("src.desk._converse", lambda key, text, *a, **k: "ok")
    desk.answer("+15005550999", "different machine entirely", channel="whatsapp")

    with db.connect() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM calls WHERE from_e164='+15005550999'"
        ).fetchone()[0]
    assert n == 2, "a stale thread was resumed"


def test_a_channel_without_a_phone_number_resolves_to_nobody(dbfile, monkeypatch):
    """Telegram gives a chat id. Guessing who that is would be worse than
    knowing we do not know."""
    from src import desk

    seen = {}
    monkeypatch.setattr("src.desk._converse",
                        lambda key, text, who=None, dealer="", call_id="":
                        seen.update(who=who) or "ok")

    desk.answer("telegram:12345", "my freezer is warm", channel="telegram")
    assert seen["who"]["known"] is False
    assert "does not carry a phone number" in seen["who"]["why"]


def test_the_agent_session_carries_the_same_state_the_phone_builds(dbfile):
    """A work order opened from a message had no contact on it, so the
    after-visit check could never find a number to ask."""
    import inspect

    from src import desk

    src = inspect.getsource(desk._converse)
    for key in ('"dealer_id": dealer', '"caller": who', '"call_id": call_id'):
        assert key in src, f"the text session is missing {key}"

    # A default argument is a fallback and is fine. A literal inside the
    # session dict is the bug: it sends every text customer to one dealer's
    # book regardless of whose customer they are.
    assert '"dealer_id": "D-REF"' not in src, "the dealer is hardcoded again"
