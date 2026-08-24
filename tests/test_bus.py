"""The message bus, which must never be able to break a phone call.

The briefing is what this product is named for and it was computed in full and
then never sent, because `build_briefing` returned a dictionary to a caller
that had nothing to do with it. Publishing fixed that, and publishing is the
right shape rather than an inline SMS because the customer is still on the
line when the briefing is built.

Which makes the failure modes here the important part. A bus that raises, or
blocks, on the conversational path is worse than the bug it fixed.
"""

from __future__ import annotations

import pytest


def test_it_is_off_unless_asked_for(monkeypatch):
    """No env var, no publish, no bill, no behaviour change."""
    from src import bus

    monkeypatch.delenv("PRAEVISUM_BUS", raising=False)
    assert bus.enabled() is False
    assert bus.publish("t", {"a": 1})["published"] is False


def test_publishing_never_raises(monkeypatch):
    """A broken bus must not take a call with it."""
    from src import bus

    monkeypatch.setenv("PRAEVISUM_BUS", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")

    def explode():
        raise RuntimeError("pubsub is down")

    monkeypatch.setattr(bus, "_client", explode)
    got = bus.publish("t", {"a": 1})
    assert got["published"] is False


def test_a_missing_project_is_reported_not_raised(monkeypatch):
    from src import bus

    monkeypatch.setenv("PRAEVISUM_BUS", "1")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert bus.publish("t", {"a": 1})["published"] is False


def test_a_briefing_that_cannot_be_sent_is_still_returned(dbfile, monkeypatch):
    """The call carries on. The technician message is the bus's problem."""
    from src import bus, tools

    class Ctx:
        def __init__(self):
            self.state = {"dealer_id": "D-REF", "caller": {}}

    monkeypatch.setenv("PRAEVISUM_BUS", "1")
    monkeypatch.setattr(bus, "_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("down")))

    wo = tools.open_work_order("AS-FREEZER", "not holding temp", Ctx())
    tools.promise_slot(wo["work_order_id"], "T-1", "2026-09-01T09:00",
                       ["P-DEFROSTTHE"], Ctx())
    brief = tools.build_briefing(wo["work_order_id"], Ctx())

    assert brief["ok"] is True
    assert "dispatched" not in brief


# --------------------------------------------------------------------------
# the message a technician actually receives
# --------------------------------------------------------------------------

def test_the_text_is_assembled_not_narrated():
    """Written from computed facts, never by a model.

    This goes out unattended and nobody reads it before it is sent. A
    hallucinated part number in an SMS is a wasted trip nobody chose.
    """
    from src import bus

    text = bus.render_briefing({
        "window": "Tuesday 09:00", "customer": "Marino's",
        "site": "Main kitchen", "address": "12 Adams St",
        "machine": "Traulsen G12010", "reported": "not holding temp",
        "safety": "R-290 flammable", "where_on_site": "back wall",
        "access_note": "closed 3-4pm",
        "load_these": [{"name": "Defrost thermostat", "likelihood": 0.47}],
        "left_behind": [{"name": "Control board", "why": "9 day lead"}],
        "reasoning": "Our own jobs put this at 47%.",
    })

    assert "Traulsen G12010" in text
    assert "Defrost thermostat (47%)" in text
    assert "SAFETY: R-290 flammable" in text
    assert "closed 3-4pm" in text
    assert "Control board" in text


def test_no_history_says_so_rather_than_listing_nothing():
    """Silence is a worse message than an honest empty one."""
    from src import bus

    text = bus.render_briefing({"machine": "x", "load_these": []})
    assert "nothing specific" in text
    assert "no match" in text


@pytest.mark.parametrize("field", ["machine", "reported"])
def test_a_thin_briefing_still_renders(field):
    """Missing facts must not produce a crash on an unattended path."""
    from src import bus

    text = bus.render_briefing({field: "something"})
    assert isinstance(text, str) and text
