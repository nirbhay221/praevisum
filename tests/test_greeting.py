"""What the caller hears in the first two seconds.

The old opening was:

    "This is the Midwest Commercial Refrigeration service line - you're
     speaking with an automated assistant. How can I help?"

That is an announcement, not a welcome. Nobody answers a phone that way. A
business says good evening, says who it is, and gets out of the way.

THREE THINGS IT WAS MISSING

  A GREETING AT ALL. No good morning, no good evening.

  THE TIME OF DAY, which the dealers table has carried a timezone for since
  the beginning and nothing ever read. A machine in us-central1 wishing
  somebody good morning at eight in the evening is a small thing that tells
  them nobody is really there.

  THEIR NAME. The number is resolved before the line opens. Greeting a nine
  year customer as a stranger is the thing this product exists to stop.

AND THE DISCLOSURE STAYS

Moved, not softened. An automated voice that lets somebody believe it is a
person has taken something from them, and in the United States it is also the
difference between a legal call and an illegal one. Making it sound like a
sentence rather than a legal notice is the point: a caller put off by the
first line never reaches the part where we help them.
"""

from __future__ import annotations

import pytest


class _Ctx:
    def __init__(self, dealer_id="D-REF", caller=None):
        self.state = {"dealer_id": dealer_id, "caller": caller or {}}


def test_it_greets_rather_than_announces(dbfile):
    from src import agents

    out = agents._greeting("Midwest Commercial Refrigeration", "America/Chicago")
    assert out.startswith(("Good morning", "Good afternoon", "Good evening"))
    assert "You have reached Midwest Commercial Refrigeration" in out


@pytest.mark.parametrize("hour,expected", [
    (7, "Good morning"), (11, "Good morning"),
    (12, "Good afternoon"), (17, "Good afternoon"),
    (18, "Good evening"), (23, "Good evening"), (3, "Good evening"),
])
def test_the_time_of_day_is_right(dbfile, monkeypatch, hour, expected):
    """And it is the DEALER's clock, not the server's."""
    import datetime as real_datetime

    from src import agents

    class Fixed(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.datetime(2026, 8, 27, hour)

    monkeypatch.setattr(agents, "datetime", real_datetime, raising=False)
    monkeypatch.setattr(real_datetime, "datetime", Fixed)
    try:
        out = agents._greeting("A Business", "America/Chicago")
        assert out.startswith(expected)
    finally:
        monkeypatch.undo()


def test_a_customer_we_know_is_greeted_by_name(dbfile):
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago",
                           {"known": True, "contact_name": "Arjun Raman"})
    # Not "Good morning": this ran green all morning and failed at lunchtime.
    assert ", Arjun." in out
    assert out.startswith(("Good morning", "Good afternoon", "Good evening"))


def test_a_stranger_is_not_greeted_by_a_name_we_invented(dbfile):
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago", {})
    assert "You have reached Midwest" in out
    assert ", ." not in out and ", ," not in out, (
        "no dangling comma where a name would go")


def test_the_disclosure_is_still_there(dbfile):
    """Not negotiable, whatever the greeting sounds like."""
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago",
                           {"known": True, "contact_name": "Arjun"})
    assert "automated assistant" in out


def test_the_desk_is_told_why_the_disclosure_matters(dbfile):
    from src import agents

    front = " ".join(agents.front_agent.instruction(_Ctx()).split())
    assert "Never imply you are a person" in front
    assert "not negotiable" in front


def test_the_greeting_reaches_the_actual_instruction(dbfile):
    """It is composed per call and substituted in, so a template that still
    said "This is the X service line" would pass every test above and fail on
    the phone."""
    from src import agents, db

    from src.config import settings

    front = agents.front_agent.instruction(_Ctx("D-REF"))
    assert f"You have reached {settings.front_name}" in front
    assert "service line - you're" not in front, "the old announcement is gone"


def test_the_same_front_greets_whichever_vendor_applies(dbfile):
    """One number, one desk. The vendor behind it is never named."""
    from src.config import settings
    from src import agents, db

    with db.connect() as c:
        vendors = [(r["greeting_name"] or r["name"])
                   for r in c.execute("SELECT name, greeting_name FROM dealers")]

    for dealer in ("D-REF", "D-IT"):
        out = agents.front_agent.instruction(_Ctx(dealer))
        assert f"You have reached {settings.front_name}" in out
        for v in vendors:
            assert v not in out


def test_a_known_caller_in_state_is_used(dbfile):
    from src import agents

    ctx = _Ctx("D-REF", {"known": True, "contact_name": "Arjun Raman"})
    assert "Arjun." in agents.front_agent.instruction(ctx)


def test_an_unknown_caller_does_not_produce_a_blank_name(dbfile):
    from src import agents

    ctx = _Ctx("D-REF", {"known": False, "contact_name": ""})
    front = agents.front_agent.instruction(ctx)
    assert ", ," not in front and "Good morning, ," not in front


def test_a_broken_timezone_still_greets(dbfile):
    """Losing the clock must not lose the call."""
    from src import agents

    out = agents._greeting("Midwest", "Not/AZone")
    assert out.startswith(("Good morning", "Good afternoon", "Good evening"))


def test_no_desk_claims_to_know_nothing_about_refrigeration(dbfile):
    """It said "You know nothing about refrigeration" on BOTH desks, including
    the one that answers for an IT company. The same shape as the greeting:
    a trade baked into a shared instruction."""
    from src import agents

    for dealer in ("D-REF", "D-IT"):
        for agent in (agents.front_agent, agents.desk_agent):
            text = agent.instruction(_Ctx(dealer))
            assert "nothing about refrigeration" not in text
            assert "nothing about the trade itself" in text


def test_the_greeting_is_short_enough_to_say(dbfile):
    """It is the first thing a kitchen with a failing freezer hears at six in
    the evening, and every word before "how can I help" is a word they are
    waiting through. The first version ran to twenty-two words."""
    from src import agents

    out = agents._greeting("Midwest Commercial Refrigeration",
                           "America/Chicago", {"known": True,
                                               "contact_name": "Arjun"})
    assert len(out.split()) <= 26, f"too long to say: {out}"
    assert out.rstrip().endswith("?"), "it has to end by handing over"


# What it asks, which matters more than what it says.


def test_a_stranger_is_offered_the_two_reasons_anyone_rings(dbfile):
    """"How can I help you today" is the line current voice-agent guidance
    singles out as the one to avoid: it makes the caller re-explain themselves
    from nothing. There are two reasons to ring a dealer, so ask which."""
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago", {})
    assert "Is something broken, or are you looking to buy?" in out


def test_a_customer_with_one_machine_has_it_named_back(dbfile):
    """We resolved them from the number before the line opened. Their machines
    were sitting in memory and the greeting asked "how can I help" anyway."""
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago", {
        "known": True, "contact_name": "Arjun Raman",
        "assets": [{"manufacturer": "Traulsen", "family": "reach-in freezer",
                    "location_note": "back kitchen"}]})

    assert "Is this about the Traulsen in the back kitchen" in out


def test_naming_their_machine_does_not_presume_they_rang_about_it(dbfile):
    """Guessing too much is the same fault as guessing nothing.

    Naming the machine and stopping there presumed service, so a customer
    ringing to BUY opened on "is this about the Traulsen in the back kitchen"
    and had to work out how to say no before they could say what they wanted.
    Under one desk that is worse still: the thing they want to buy might be
    nothing like the thing they own.
    """
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago", {
        "known": True, "contact_name": "Arjun Raman",
        "assets": [{"manufacturer": "Traulsen", "family": "reach-in freezer",
                    "location_note": "back kitchen"}]})

    assert "Traulsen" in out, "we know what they own and should say so"
    assert "something new" in out, "and they might be ringing to buy"


def test_a_customer_with_several_is_asked_which_not_what(dbfile):
    """A far smaller thing to answer than "how can I help"."""
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago", {
        "known": True, "contact_name": "Arjun",
        "assets": [{"manufacturer": "Traulsen", "family": "reach-in freezer"},
                   {"manufacturer": "Beverage-Air", "family": "walk-in cooler"},
                   {"manufacturer": "Avantco", "family": "ice machine"}]})

    assert "playing up, or are you looking to buy" in out
    assert "ice machine" not in out, (
        "a caller with nine machines does not want an inventory read at them")
    assert "walk-in cooler" not in out


def test_several_of_the_same_kind_are_not_listed_twice(dbfile):
    from src import agents

    out = agents._greeting("Midwest", "America/Chicago", {
        "known": True, "contact_name": "Arjun",
        "assets": [{"manufacturer": "Traulsen", "family": "reach-in freezer"},
                   {"manufacturer": "True", "family": "reach-in freezer"}]})

    assert "one of your reach-in freezers playing up" in out


def test_the_whole_caller_record_reaches_the_greeting(dbfile):
    """It used to be handed only the first name, so the machines it needed
    were resolved, stored in session state, and never used."""
    from src import agents

    class _Full:
        state = {"dealer_id": "D-REF",
                 "caller": {"known": True, "contact_name": "Arjun Raman",
                            "assets": [{"manufacturer": "Traulsen",
                                        "family": "reach-in freezer"}]}}

    assert "Is this about the Traulsen" in agents.front_agent.instruction(_Full())
