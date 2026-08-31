"""A technician asking is not a technician closing.

THE BUG THIS FIXES

`desk.py` routed every message from a known technician straight into
`close_by_text`. So an engineer standing in front of an open machine who
texted:

    any idea why this one keeps tripping the breaker?

had that sentence parsed for a found cause and a labour figure, and the corpus
every other technician reads is what gets written from it.

Meanwhile the company held 851 repairs it had actually done plus a set of
first-line procedures, and the only route to either was the CUSTOMER-facing
"should we send anybody" check. The one person qualified to act on that
knowledge, the one with their hands on the machine, could not reach it.

WHERE THE ASYMMETRY MATTERS

Mistaking a closure for a question costs one clarifying text. Mistaking a
question for a closure writes a fabricated repair into shared knowledge, where
it is read back to the next engineer as something this company established.
So the question test is deliberately generous in one direction.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# telling the two apart
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "any idea why this one keeps tripping the breaker?",
    "why is there frost building on the coil",
    "what usually causes this on a Traulsen",
    "has anyone seen this before",
    "is the defrost heater a common one here",
    "stuck on this, ideas?",
    "Should I be looking at the door heater",
])
def test_a_question_is_recognised(dbfile, text):
    from src.askback import looks_like_a_question

    assert looks_like_a_question(text) is True


@pytest.mark.parametrize("text", [
    "replaced the defrost thermostat, 2 hours",
    "door heater open circuit, swapped the harness, 1.5",
    "cleaned the condenser, all good now",
    "fitted P-EVAPFAN, 3 hours on site",
])
def test_a_closure_is_still_a_closure(dbfile, text):
    """The corpus depends on these being read as closures, so the generous
    question test must not swallow them."""
    from src.askback import looks_like_a_question

    assert looks_like_a_question(text) is False


def test_an_empty_message_is_neither(dbfile):
    from src.askback import looks_like_a_question

    assert looks_like_a_question("") is False
    assert looks_like_a_question("   ") is False


# --------------------------------------------------------------------------
# what comes back
# --------------------------------------------------------------------------

def test_a_technician_with_no_job_is_told_so_rather_than_guessed_at(dbfile):
    from src.askback import answer_for_technician

    out = answer_for_technician("+19999999999", "any ideas?")
    assert out["ok"] is False
    assert "which machine" in out["reply"]


def test_nothing_on_file_is_said_plainly_instead_of_invented(dbfile,
                                                             monkeypatch):
    """An engineer who drives back for a part we invented has lost an
    afternoon, so an empty answer has to read as empty."""
    from src import askback

    monkeypatch.setattr(askback, "_their_current_job",
                        lambda p: {"name": "Curtis Okafor",
                                   "manufacturer": "True",
                                   "model_number": "TUC-27F",
                                   "family": "reach-in freezer",
                                   "dealer_id": "D-REF",
                                   "reported_symptom": "x",
                                   "asset_id": "AST-1"})
    monkeypatch.setattr(askback, "_what_we_found_before", lambda j, q: [])
    monkeypatch.setattr(askback, "_what_the_trade_says", lambda j, q: [])

    out = askback.answer_for_technician("+1555", "why is it doing this?")
    assert out["found"] is False
    assert "not going to guess" in out["reply"]
    assert "text the cause when you close" in out["reply"]


def test_our_own_record_is_kept_separate_from_general_trade_knowledge(
        dbfile, monkeypatch):
    """The same distinction reviews.py draws between what we know and what the
    world says. A technician deciding what to do next is entitled to know
    which is which."""
    from src import askback

    monkeypatch.setattr(askback, "_their_current_job",
                        lambda p: {"name": "Curtis", "manufacturer": "True",
                                   "model_number": "TUC-27F",
                                   "family": "reach-in freezer",
                                   "dealer_id": "D-REF",
                                   "reported_symptom": "warm",
                                   "asset_id": "AST-1"})
    monkeypatch.setattr(askback, "_what_we_found_before", lambda j, q: [
        {"found_cause": "door heater open circuit", "closed_on": "2026-03-04",
         "times": 1, "model": "TUC-27F"}])
    monkeypatch.setattr(askback, "_what_the_trade_says", lambda j, q: [
        {"check_first": "Is the door actually closing", "instruction": "",
         "source_ref": "trade first-line check", "safety_note": None}])

    reply = askback.answer_for_technician("+1555", "any idea?")["reply"]

    assert "What we found before on this model" in reply
    assert "General trade check, not from our own jobs" in reply
    assert reply.index("What we found before") < reply.index("General trade")


def test_the_same_cause_three_times_is_reported_once_with_a_count(
        dbfile, monkeypatch):
    """Searching a recurring fault returns the same sentence repeatedly
    because it genuinely did recur. How OFTEN it was the answer is the useful
    part; printing it three times just buries whatever else matched."""
    from src import askback

    class FakeRepair:
        def __init__(self, cause, when):
            self.found_cause = cause
            self.closed_on = when
            self.model = "TUC-27F"

    class FakeHit:
        def __init__(self, r):
            self.repair = r

    class FakeIndex:
        def search(self, q, **kw):
            return [FakeHit(FakeRepair("scale on the evaporator plate", d))
                    for d in ("2024-09-01", "2025-04-02", "2026-07-03")]

    monkeypatch.setattr("src.memory.index_for", lambda d: FakeIndex())

    out = askback._what_we_found_before(
        {"dealer_id": "D-REF", "model_number": "TUC-27F",
         "reported_symptom": "cloudy cubes"}, "why")

    assert len(out) == 1
    assert out[0]["times"] == 3
    assert out[0]["closed_on"] == "2026-07-03"


def test_a_sealed_system_is_never_walked_through_over_text(dbfile,
                                                           monkeypatch):
    """Refrigerant circuits are pressurised and some of them are propane. The
    medium is wrong for that whoever is reading it."""
    from src import askback

    monkeypatch.setattr(askback, "_their_current_job",
                        lambda p: {"name": "Curtis", "manufacturer": "True",
                                   "model_number": "TUC-27F",
                                   "family": "reach-in freezer",
                                   "dealer_id": "D-REF",
                                   "reported_symptom": "warm",
                                   "asset_id": "AST-1"})
    monkeypatch.setattr(askback, "_what_we_found_before", lambda j, q: [
        {"found_cause": "low charge", "closed_on": "2026-03-04", "times": 1}])
    monkeypatch.setattr(askback, "_what_the_trade_says", lambda j, q: [])

    reply = askback.answer_for_technician(
        "+1555", "what should the refrigerant charge be?")["reply"]

    assert "sealed and pressurised" in reply
    assert "Ring the branch" in reply


def test_the_desk_routes_a_question_away_from_the_closer(dbfile):
    """The wiring, not just the parts. This is the actual bug: it lived in
    desk.py, not in either module it sits between."""
    import inspect

    from src import desk

    src = inspect.getsource(desk)
    assert "looks_like_a_question" in src
    assert src.index("looks_like_a_question") < src.index("close_by_text(identity")
