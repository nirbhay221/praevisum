"""The caller speaks whatever they speak. The corpus is in English.

One in five US restaurant workers speaks English as a second language, a
quarter of the workforce is Hispanic or Latino, and the person who finds the
walk-in at twelve degrees at six in the morning is whoever opened up rather
than the owner.

Gemini speaking Spanish is configuration. The problem is that 670 repairs are
written in English by technicians, and the retrieval that makes this desk worth
more than a clipboard searches those words. A symptom in Spanish retrieves
nothing, and the desk then falls back to having no history without saying so.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_leaking_language():
    """A context variable outlives the test that set it.

    Without this, one test calling set_language("es") makes every test after
    it run its retrieval through a Spanish normalisation, and the failure
    lands somewhere unrelated. It did: test_outreach stopped predicting.
    """
    from src.language import forget

    forget()
    yield
    forget()


# What must never be translated.


def test_a_model_number_survives_untouched(dbfile):
    """A model helpfully rendering HRP2HC as something more Spanish has
    destroyed the only identifier on the call."""
    from src.language import _protect, _restore

    said = "el congelador HRP2HC***S******** no enfria bien desde anoche"
    guarded, kept = _protect(said)

    assert "HRP2HC" not in guarded, "the model number was exposed to the model"
    assert _restore(guarded, kept) == said


def test_part_numbers_refrigerants_and_temperatures_are_all_held(dbfile):
    """None of these are words, and a helpful translation of one is a defect."""
    from src.language import _protect, _restore

    for said in ("necesito el P-EVAPFAN para el MT34-1",
                 "runs on R-290 and the coil is iced",
                 "the walk-in is sitting at 12 degrees",
                 "esta a 12 degrees desde ayer"):
        guarded, kept = _protect(said)
        assert _restore(guarded, kept) == said, f"round trip failed: {said}"


def test_an_identifier_that_changed_is_caught(dbfile):
    """The failure most likely to pass a casual read: the sentence still looks
    right and the model number is wrong."""
    from src.language import kept_intact

    assert kept_intact("the HRP2HC-1S is warm", "the HRP2HC-1S is warm")
    assert not kept_intact("the HRP2HC-1S is warm", "the HRP2HC-15 is warm")


# When nothing needs doing.


def test_english_never_reaches_a_model(dbfile, monkeypatch):
    """Putting a model call in front of every symptom on every call, to
    translate English into English, is latency spent on nothing."""
    from src import language

    monkeypatch.setattr(language, "_model",
                        lambda: pytest.fail("a model was called for English"))

    for code in ("en", "en-US", ""):
        out = language.for_retrieval("the walk-in is not holding", code)
        assert out["translated"] is False
        assert out["searchable"] == "the walk-in is not holding"


def test_an_empty_symptom_does_nothing(dbfile, monkeypatch):
    from src import language

    monkeypatch.setattr(language, "_model",
                        lambda: pytest.fail("a model was called for nothing"))
    assert language.for_retrieval("", "es")["searchable"] == ""


# When the translation fails.


def test_a_failed_translation_returns_their_words_and_says_so(dbfile, monkeypatch):
    """A symptom that could not be normalised retrieves badly, which is bad. A
    symptom silently mistranslated retrieves confidently against the wrong
    repairs, which is worse."""
    from src import language

    monkeypatch.setattr(language, "_model",
                        lambda: (_ for _ in ()).throw(RuntimeError("down")))

    out = language.for_retrieval("el congelador no enfria", "es")
    assert out["translated"] is False
    assert out["searchable"] == "el congelador no enfria"
    assert "Do not claim we have seen this before" in out["say"]


def test_their_own_words_are_always_kept(dbfile, monkeypatch):
    """The corpus keeps a technician's phrasing rather than a summary, for the
    same reason. A customer is quoted back in their words, not ours."""
    from src import language

    monkeypatch.setattr(language, "_model", lambda: _FakeModel(
        "the freezer is not cooling properly since last night"))

    out = language.for_retrieval("el congelador no enfria bien desde anoche", "es")
    assert out["said"] == "el congelador no enfria bien desde anoche"
    assert out["searchable"] != out["said"]
    assert "in their own words" in out["say"]


class _FakeModel:
    """Stands in for the vision client without reaching the network."""

    def __init__(self, reply):
        self._reply = reply

    @property
    def models(self):
        return self

    def generate_content(self, **_):
        class R:
            text = self._reply
        return R()


# The property that decides whether any of this was worth doing.


def test_a_spanish_symptom_finds_the_same_repairs_as_its_english_twin(dbfile,
                                                                     monkeypatch):
    """The whole point. If normalisation does not put a Spanish caller in front
    of the same evidence an English caller gets, it is decoration.
    """
    import src.memory as memory
    from src import db, language, reason

    _seed_corpus(db)
    memory.load_from_db()

    english = "the freezer is not cooling and there is ice on the coil"
    spanish = "el congelador no enfria bien y hay hielo en el serpentin"

    monkeypatch.setattr(language, "_model", lambda: _FakeModel(english))

    got_en = reason._fault_distribution("D-REF", english, "Traulsen",
                                        "reach-in freezer", "G12010")
    got_es = reason._fault_distribution("D-REF", spanish, "Traulsen",
                                        "reach-in freezer", "G12010",
                                        language="es")

    # Without this the assertion below is [] == [], which passes for the worst
    # possible reason: retrieval failed identically on both sides.
    assert got_en, "the English side retrieved nothing, so this proves nothing"

    assert [d["cause"] for d in got_en] == [d["cause"] for d in got_es], \
        "a Spanish caller was shown different evidence from an English one"


def test_without_normalisation_the_spanish_caller_gets_nothing(dbfile):
    """The gap this closes, asserted rather than assumed.

    If this ever stops being true the feature has become unnecessary, which is
    worth knowing.
    """
    import src.memory as memory
    from src import db, reason

    # A corpus, because the shared fixture has none. Without one both sides
    # return an empty list and the test passes for the worst possible reason:
    # retrieval failed equally on English and Spanish.
    _seed_corpus(db)
    memory.load_from_db()

    english = reason._fault_distribution(
        "D-REF", "the freezer is not cooling and there is ice on the coil",
        "Traulsen", "reach-in freezer", "G12010")
    assert english, "the English side retrieved nothing, so this proves nothing"

    spanish = reason._fault_distribution(
        "D-REF", "el congelador no enfria bien y hay hielo en el serpentin",
        "Traulsen", "reach-in freezer", "G12010")

    assert [d["cause"] for d in spanish] != [d["cause"] for d in english], \
        "untranslated Spanish already retrieves the same as English"


def test_the_translation_is_told_not_to_diagnose(dbfile):
    """It normalises a question. It must never add a cause or a part, because
    that would put an invented finding into the retrieval and the desk would
    then confirm its own guess."""
    from src.language import PROMPT

    low = PROMPT.lower()
    assert "do not diagnose" in low
    assert "do not add a cause" in low
    assert "do not add a part" in low


def _seed_corpus(db) -> None:
    """A handful of closed repairs, because the shared fixture has none.

    Written in a technician's English, which is the whole point: these are the
    words a Spanish symptom has to reach.
    """
    rows = [
        ("evaporator fan motor seized, no air across the coil", "P-EVAPFAN"),
        ("defrost heater element open circuit, coil iced solid", "P-DEFROSTTHE"),
        ("condenser packed with grease and lint, running flat out", "P-EVAPFAN"),
        ("termination thermostat failed, defrost never terminating", "P-DEFROSTTHE"),
    ]
    with db.txn() as c:
        for i, (cause, sku) in enumerate(rows * 3):
            c.execute(
                """INSERT INTO repairs
                   (id,dealer_id,asset_id,manufacturer,model_number,family,
                    reported_symptom,found_cause,parts_consumed,labor_hours,
                    closed_on,technician_id)
                   VALUES (?,'D-REF','AS-FREEZER','Traulsen','G12010',
                           'reach-in freezer',?,?,?,1.5,'2026-05-01','T-1')""",
                (f"RL-{i}",
                 "not cooling properly and ice building on the coil",
                 cause, sku))


# Detecting it, rather than being told.


def test_the_desk_opens_in_english_and_switches_when_told(dbfile):
    """It cannot know before they speak, so English is where it starts. That is
    not the same as English being the default."""
    from src import agents

    # Whitespace collapsed, because the instruction is wrapped prose and a
    # test that depends on where a line happens to break is testing the
    # formatter rather than the rule.
    rules = " ".join(agents.DESK_RULES.lower().split())
    assert "open in english, because you cannot know before they speak" in rules
    assert "call set_language and continue in it" in rules
    assert "do not ask them whether they would prefer it" in rules
    assert "read model numbers, part numbers, prices and times exactly" in rules


def test_both_channels_can_switch(dbfile):
    """A phone caller and somebody messaging get the same desk, so they get the
    same tool."""
    from src import agents

    names = lambda ts: {getattr(t, "__name__", None) or getattr(t, "name", "")
                        for t in ts}
    assert "set_language" in names(agents.front_agent.tools)
    assert "set_language" in names(agents.desk_agent.tools)


def test_switching_is_remembered_for_next_time(dbfile):
    """Same shape as took_two_trips: one fact from the database changing how
    the next call opens, so nobody has to switch it twice."""
    from types import SimpleNamespace

    from src import db, language

    with db.connect() as c:
        ct = c.execute("SELECT id FROM contacts LIMIT 1").fetchone()
    if ct is None:
        pytest.skip("fixture has no contact")

    ctx = SimpleNamespace(state={"caller": {"contact_id": ct["id"]}})
    out = language.set_language("es", ctx)
    assert out["ok"] is True
    assert ctx.state["language"] == "es"

    with db.connect() as c:
        assert c.execute("SELECT language FROM contacts WHERE id=?",
                         (ct["id"],)).fetchone()["language"] == "es"


def test_a_language_nobody_thought_about_is_refused(dbfile):
    """Each entry in SPOKEN means the retrieval normalisation was considered
    and the identifiers were checked against it. A code not on the list has
    had neither."""
    from types import SimpleNamespace

    from src import language

    ctx = SimpleNamespace(state={})
    out = language.set_language("xx", ctx)
    assert out["ok"] is False
    assert "do not pretend to switch" in out["say"]
    assert "language" not in ctx.state


def test_the_retrieval_picks_it_up_without_being_passed_it(dbfile, monkeypatch):
    """_fault_distribution has no business taking a language parameter to
    satisfy a retrieval detail, and every caller would have to learn about one.
    """
    import src.memory as memory
    from src import db, language, reason

    _seed_corpus(db)
    memory.load_from_db()
    monkeypatch.setattr(
        language, "_model",
        lambda: _FakeModel("the freezer is not cooling and there is ice on the coil"))

    token = language.SPEAKING.set("es")
    try:
        got = reason._fault_distribution(
            "D-REF", "el congelador no enfria bien y hay hielo en el serpentin",
            "Traulsen", "reach-in freezer", "G12010")
    finally:
        language.SPEAKING.reset(token)

    assert got, "the caller's language was set and retrieval still found nothing"


def test_the_switch_instruction_protects_identifiers(dbfile):
    """A model number read out translated is a model number destroyed, and it
    is the failure that still sounds fluent."""
    from types import SimpleNamespace

    from src import language

    out = language.set_language("es", SimpleNamespace(state={}))
    assert "exactly as they are" in out["say"]
    assert "not words" in out["say"]
