"""Every way we reach out to somebody, and what silently went wrong.

A CONSOLIDATION PASS, NOT A FEATURE

Escalations were added and hooked into the follow-up queue, which is the right
place: it is the machinery that already delivers every other promise this
system makes. What was NOT done was teaching the renderer what an escalation
says. render() handles missed_call and dropped_call and then falls through to
after_visit for anything else, so an escalation would have gone out as

    "Dale Brenner will ring you back within 2 hours. Is it holding now?"

to a customer we had just told we could not staff their job.

The fall-through is the dangerous shape. A new kind was not rejected, it was
quietly rendered as the wrong message.
"""

from __future__ import annotations

import pytest


def _row(kind, context=""):
    return {"kind": kind, "context": context, "id": "F-1", "phone": "+13095550101"}


def test_an_escalation_says_what_an_escalation_says(dbfile):
    from src import followup

    out = followup.render(_row("escalation",
                               "Dale Brenner will ring you back within 2 hours."))
    assert "Dale Brenner" in out
    assert "Is it holding now?" not in out, "that is the after-visit message"


def test_a_kind_with_no_wording_sends_nothing(dbfile):
    """Rather than borrowing the after-visit sentence."""
    from src import followup

    assert followup.render(_row("something_new", "whatever")) == ""


def test_the_known_kinds_still_read_correctly(dbfile):
    from src import followup

    assert "missed your call" in followup.render(_row("missed_call"))
    assert "cut off" in followup.render(_row("dropped_call", "the freezer"))
    assert "holding now" in followup.render(_row("after_visit", "We were out"))


def test_an_empty_message_is_never_delivered(dbfile, monkeypatch):
    """It would put a blank text in front of a customer."""
    from src import sender

    monkeypatch.setattr(sender, "_reachable", lambda phone: ["whatsapp"])
    monkeypatch.setattr(sender, "_deliver",
                        lambda *a: pytest.fail("must not try to send an empty message"))

    from src import followup

    monkeypatch.setattr(followup, "due", lambda dealer_id=None, ignore_timer=False: [
        {"id": "F-1", "kind": "mystery", "phone": "+13095550101", "message": ""}])

    out = sender.send_followups()
    assert out["sent"] == 0
    assert out["failed"] == 1
    assert "no message is written" in out["not_delivered"][0]["why"]


def test_an_undeliverable_followup_stays_queued(dbfile, monkeypatch):
    """A follow-up that could not be delivered is not one that happened."""
    from src import followup, sender

    monkeypatch.setattr(sender, "_reachable", lambda phone: [])
    monkeypatch.setattr(followup, "due", lambda dealer_id=None, ignore_timer=False: [
        {"id": "F-2", "kind": "missed_call", "phone": "+13095550101",
         "message": "Sorry we missed your call."}])

    out = sender.send_followups()
    assert out["sent"] == 0 and out["failed"] == 1


def test_every_followup_kind_the_schema_allows_has_wording(dbfile):
    """The schema and the renderer must not drift apart. This is what let the
    escalation kind be added on one side only."""
    import re
    from pathlib import Path

    from src import followup

    sql = Path("src/schema_followup.sql").read_text(encoding="utf-8")
    kinds = re.search(r"kind\s+TEXT NOT NULL\s*CHECK \(kind IN \(([^)]+)\)",
                      sql, re.S).group(1)
    allowed = re.findall(r"'([a-z_]+)'", kinds)

    assert allowed, "could not read the allowed kinds out of the schema"
    for kind in allowed:
        assert followup.render(_row(kind, "context here")).strip(), (
            f"the schema allows kind {kind!r} and render() writes no message "
            f"for it, so it would go out as the wrong sentence or as nothing")
