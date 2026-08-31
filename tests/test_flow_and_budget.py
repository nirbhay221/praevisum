"""Three things a live call showed, all of them the caller's experience.

SILENCE. The caller sat through twenty-eight seconds of nothing, then sixty,
and said "hello?" into the gap because they thought the line had dropped. The
rule that was supposed to prevent this named assess_job, and assess_job was
not once what made anybody wait: it was advice, scheduling and supply.

A BUDGET IGNORED. They said five and a half thousand was too much and asked
for something cheaper. They were asked four separate times whether they would
like other options explored, and never given one, because no tool could answer
"what have you got under this price".

THE SESSION CLOSING. Gemini ended the call with "Input data processing failed.
This is likely due to the client sending data too fast. Please review your flow
control mechanism." We forwarded every frame Twilio sent, about fifty a second
for the whole call, nearly all of it room tone.
"""

from __future__ import annotations

import audioop

import pytest


def _frame(level: int) -> bytes:
    """One 20ms frame of 8kHz mu-law at roughly the given amplitude."""
    return audioop.lin2ulaw(level.to_bytes(2, "little", signed=True) * 160, 2)


QUIET = _frame(20)
LOUD = _frame(9000)


# Flow control.


def test_silence_is_not_forwarded():
    from src.telephony.speech import Gate

    g = Gate()
    for _ in range(40):          # get past warmup with speech
        g.open_for(LOUD)
    for _ in range(200):
        g.open_for(QUIET)

    assert g.dropped > 100, "a long pause must stop being forwarded"


def test_speech_is_always_forwarded():
    """Being too eager costs bandwidth. Being too strict clips a word, which
    costs the caller a repetition and this desk its credibility."""
    from src.telephony.speech import Gate

    g = Gate()
    for _ in range(300):
        assert g.open_for(LOUD) is True
    assert g.dropped == 0


def test_it_keeps_sending_briefly_after_speech_stops():
    """Gemini decides a turn has ended by hearing somebody stop. Cut the audio
    the instant the level drops and it never gets that signal, so the caller
    finishes a sentence and the desk waits for more."""
    from src.telephony import speech

    g = speech.Gate()
    for _ in range(40):
        g.open_for(LOUD)

    tail = sum(g.open_for(QUIET) for _ in range(200))
    assert tail == speech.HANGOVER_FRAMES
    assert speech.HANGOVER_MS >= 500, "too short and endpointing breaks"


def test_the_opening_of_a_call_is_never_gated():
    """The greeting and an immediate interruption are the worst possible place
    to be clever."""
    from src.telephony import speech

    g = speech.Gate()
    assert all(g.open_for(QUIET) for _ in range(speech.WARMUP_FRAMES))


def test_a_frame_it_cannot_measure_is_sent_anyway():
    """Never let the optimisation be the reason somebody is not heard."""
    from src.telephony.speech import Gate

    g = Gate()
    for _ in range(40):
        g.open_for(LOUD)
    for _ in range(60):
        g.open_for(QUIET)
    assert g.open_for(b"\x00") is True, "odd-length frame: unmeasurable"


def test_the_bridge_actually_uses_the_gate():
    import inspect

    from src.telephony import twilio_bridge

    src = inspect.getsource(twilio_bridge._handle_call)
    assert "gate.open_for" in src
    assert "send_realtime" in src


# The budget.


def test_a_budget_gets_a_list_not_a_question(dbfile, monkeypatch):
    from scripts.seed_product_stock import load

    from src import market, supply

    # Offline and deterministic. Seeding now looks up REAL market prices, so
    # without this the test result moves with what suppliers are charging
    # today, which is not something a test should depend on.
    monkeypatch.setattr(market, "price_for",
                        lambda *a, **k: {"ok": False, "why": "stubbed"})
    load()
    out = supply.options_under(6000, "reach-in freezer")
    assert out["ok"] is True
    assert out["options"], "there are machines under this price"
    assert "already asked" in out["say"]


def test_nothing_in_budget_still_gives_them_a_number(dbfile, monkeypatch):
    """Telling somebody we have nothing and stopping leaves them with no next
    move."""
    from scripts.seed_product_stock import load

    from src import market, supply

    monkeypatch.setattr(market, "price_for",
                        lambda *a, **k: {"ok": False, "why": "stubbed"})
    load()
    out = supply.options_under(50, "reach-in freezer")
    assert out["options"] == []
    assert out["nearest"] is not None
    assert "cheapest we carry" in out["say"]
    assert "asking a third time sounds like stalling" in out["say"]


def test_prices_scale_with_the_real_capacity(dbfile, monkeypatch):
    """They all came out at exactly $5,544, which makes "have you got anything
    cheaper" a question with no answer.

    The spread comes from the machine's real EnergyStar capacity in cubic
    feet, so this seeds two catalogue entries of different sizes and checks
    the price list follows them.
    """
    from scripts.seed_product_stock import load

    from src import db, market

    # The capacity scaling is the FALLBACK, used when no real listing exists,
    # so the listings are stubbed out to reach it.
    monkeypatch.setattr(market, "price_for",
                        lambda *a, **k: {"ok": False, "why": "stubbed"})

    with db.txn() as c:
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AS-BIG','S-1','Traulsen','G20010','reach-in freezer')""")
        c.executemany(
            """INSERT INTO equipment (source,dataset,category,brand,model_number,
                                      product_type,capacity,model_norm)
               VALUES ('energystar','test','refrigeration',?,?,?,?,?)""",
            [("Traulsen", "G12010", "reach-in freezer", 12.0, "G12010"),
             ("Traulsen", "G20010", "reach-in freezer", 48.0, "G20010")])

    load()
    with db.connect() as c:
        prices = {r["model_number"]: r["list_price"] for r in c.execute(
            "SELECT model_number, list_price FROM product_stock "
            "WHERE model_number IN ('G12010','G20010')")}

    assert len(set(prices.values())) > 1, (
        "a price list where a 12 cubic foot box costs the same as a 48 is not "
        "a price list")
    assert prices["G20010"] > prices["G12010"], "the bigger box costs more"


def test_a_budget_of_nothing_is_refused(dbfile):
    from src import supply

    assert supply.options_under(0)["ok"] is False


# What the desk is told.


def test_the_desk_must_speak_before_every_slow_tool(dbfile):
    from src import agents

    i = " ".join(agents.FRONT_INSTRUCTION.split())
    assert "BEFORE ANY OF THESE, EVERY TIME" in i
    for tool in ("advice", "scheduling", "supply", "options_under"):
        assert tool in i


def test_the_desk_must_act_on_a_budget_rather_than_ask(dbfile):
    from src import agents

    r = " ".join(agents.DESK_RULES.split())
    assert "A BUDGET IS AN INSTRUCTION, NOT AN OPENING BID" in r
    assert "Do not ask whether they would like you to look" in r
    assert "Never invent a cheaper machine to please somebody" in r


def test_options_under_is_on_the_desk(dbfile):
    from src import agents

    names = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "options_under" in names
