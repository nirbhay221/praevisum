"""Three bugs a real phone call found, and nothing else would have.

On 26 August a new customer rang the desk for the first time. The call ran
three minutes forty and produced no appointment. Everything below is from that
call's journal, not from imagination.

  A STRANGER'S MACHINE. At 13:47:00 the model called should_send_someone with
  AST-7EA68C. That is a real asset: a True Refrigeration reach-in belonging to
  Rockvale Convenience, a different account at a different site. It had read
  the id out of a load_memory result, which returns past repairs from the
  whole corpus and quite properly carries their asset ids. So the first-line
  advice given to this caller was computed against a stranger's freezer, and
  nothing anywhere noticed.

  A CUSTOMER WHO COULD NEVER BE BOOKED. The site created mid-call had no
  coordinates, because confirm_details stored the address as text and nothing
  turned it into a point. next_available_slot refuses a site it cannot place.
  Every seeded site had coordinates because the seed wrote them, so no test
  had ever seen this.

  NINETY SECONDS OF SILENCE. Having been refused, the desk asked the scheduler
  the same question six times, said nothing out loud between attempts, and the
  caller said hello into the gap before the line dropped.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def two_customers(dbfile):
    """Our caller, and somebody else who owns a machine."""
    from src import db, trace

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,kind,name) "
                  "VALUES ('A-OTHER','business','Rockvale Convenience')")
        c.execute("INSERT INTO sites (id,account_id,label,lat,lon) "
                  "VALUES ('S-OTHER','A-OTHER','Rockvale',41.52,-90.57)")
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AST-THEIRS','S-OTHER','True Refrigeration',
                             'TUC-27F','reach-in freezer')""")
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-X','+13095551111','CT-1','2026-08-26T13:46:14')")
    trace.call_context("CALL-X")
    yield
    trace.call_context("")


class _Tool:
    def __init__(self, name):
        self.__name__ = name


class _Ctx:
    state = {"intent": "service", "language": ""}


def _on_call(phone="+13095557777", call_id="CALL-N"):
    """Register a first-time caller and put them on a live call."""
    from src import caller, db, trace

    who = caller.resolve(phone)
    with db.txn() as c:
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES (?,?,?,'2026-08-26T13:00:00')",
                  (call_id, phone, who["contact_id"]))
    trace.call_context(call_id)
    return who


# A stranger's machine.


def test_another_customers_machine_is_refused(two_customers):
    """The bug, exactly as it happened."""
    from src import guards

    out = guards.guard_tool(
        _Tool("should_send_someone"),
        {"symptom": "not holding temp", "asset_id": "AST-THEIRS"}, _Ctx())

    assert out is not None and out["blocked"] is True
    assert "another customer's account" in out["why"]
    assert "Do NOT try another id" in out["do_this"]


def test_our_own_machine_is_allowed(two_customers):
    """The guard must not close the door on the ordinary case."""
    from src import db, guards

    with db.connect() as c:
        mine = c.execute("SELECT id FROM assets WHERE site_id='S-1'").fetchone()

    assert guards.guard_tool(_Tool("should_send_someone"),
                             {"asset_id": mine["id"]}, _Ctx()) is None


def test_an_invented_id_is_refused(two_customers):
    """An id nobody has is never acted on. It is also no longer fatal.

    THE CONTRACT CHANGED, DELIBERATELY.

    This used to block the call outright. On a live call the desk invented
    asset_id="AST-037" for a chair it had sold that customer an hour before,
    which matches the shape of ours exactly, and blocking meant the work order
    failed four times and the desk fell back to asking the customer for a
    model number.

    A made-up id carries no information, so it is now treated like every other
    unusable value: thrown away, and the machine resolved from what THIS
    caller owns. The safety property is unchanged and is what this asserts --
    the invented id must never reach the tool. What replaces it can only ever
    be one of their own machines, and a stranger's id is still blocked
    outright by the test above, because that one exists and belongs to
    somebody.
    """
    from src import db, guards

    args = {"asset_id": "AST-NOTREAL", "reported_symptom": "not holding temp"}
    out = guards.guard_tool(_Tool("open_work_order"), args, _Ctx())

    assert out is None, "an invented id must not end the call"
    assert args["asset_id"] != "AST-NOTREAL", "it must never reach the tool"

    if args["asset_id"]:
        with db.connect() as c:
            mine = {r[0] for r in c.execute(
                "SELECT id FROM assets WHERE site_id = 'S-1'")}
        assert args["asset_id"] in mine, "only ever one of their own machines"


def test_a_guard_that_cannot_read_does_not_allow(two_customers, monkeypatch):
    """A check whose failure mode is allow is not a check."""
    from src import guards

    def boom(*a, **k):
        raise RuntimeError("database gone")

    from src import db

    monkeypatch.setattr(db, "connect", boom)
    out = guards.guard_tool(_Tool("open_work_order"),
                            {"asset_id": "AST-THEIRS"}, _Ctx())
    assert out["blocked"] is True


def test_off_a_call_nothing_is_gated(dbfile):
    """Sweeps, the console and the tests are not on a phone call and must not
    be told they are impersonating somebody."""
    from src import db, guards, trace

    trace.call_context("")
    with db.connect() as c:
        mine = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
    assert guards.guard_tool(_Tool("open_work_order"),
                             {"asset_id": mine["id"]}, _Ctx()) is None


# The customer who could never be booked.


def test_a_site_with_no_address_cannot_be_booked_and_says_so(dbfile):
    """It used to return ok true and nothing else, and the desk went on to ask
    the scheduler for a window six times."""
    from src import caller, trace

    _on_call()
    out = caller.confirm_details(name="Dana Whitfield",
                                 account_name="Riverside Taphouse",
                                 site_label="Davenport")
    trace.call_context("")

    assert out["ok"] is True, "we still wrote down who they are"
    assert out["cannot_book_yet"] is True
    assert "ASK FOR THE STREET ADDRESS NOW" in out["do_this"]


def test_an_address_becomes_a_point_on_the_map(dbfile, monkeypatch):
    """The fix. Without coordinates the scheduler refuses every window."""
    from src import caller, db, geo, trace

    monkeypatch.setattr(geo, "locate", lambda *a, **k: {
        "ok": True, "lat": 41.5236, "lon": -90.5776, "matched": "2200 E 53rd St"})

    _on_call()
    out = caller.confirm_details(name="Dana Whitfield",
                                 account_name="Riverside Taphouse",
                                 site_label="Davenport",
                                 address="2200 E 53rd St")
    trace.call_context("")

    assert "cannot_book_yet" not in out
    with db.connect() as c:
        site = c.execute("SELECT lat, lon FROM sites WHERE id=?",
                         (out["site_id"],)).fetchone()
    assert site["lat"] == 41.5236


def test_a_geocoder_that_is_down_still_lets_us_write_them_down(dbfile, monkeypatch):
    """Losing the lookup must not lose the customer."""
    from src import caller, geo, trace

    def boom(*a, **k):
        raise RuntimeError("nominatim unreachable")

    monkeypatch.setattr(geo, "locate", boom)
    _on_call()
    out = caller.confirm_details(name="Dana Whitfield", address="2200 E 53rd St")
    trace.call_context("")

    assert out["ok"] is True
    assert out["cannot_book_yet"] is True


def test_the_desk_is_told_to_get_the_street_not_the_town(dbfile):
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    # The rule was rewritten after simulated calls showed it being read as a
    # gate on everything, including questions that need no address at all.
    # The property it protects is unchanged: get the street before booking.
    assert "GET THE STREET ADDRESS BEFORE YOU BOOK" in rules
    assert "A price needs no address" in rules


# The geocoder itself.


def test_a_town_is_not_appended_when_they_already_said_one(dbfile, monkeypatch):
    """A street given as 1401 River Dr, Moline became Moline, Davenport, IA
    and resolved to nothing at all."""
    from src import geo

    seen = {}

    def fake(req, timeout=0):
        seen["url"] = req.full_url
        raise RuntimeError("stop here")

    monkeypatch.setattr(geo.urllib.request, "urlopen", fake)

    geo.locate("1401 River Dr, Moline")
    assert "Davenport" not in seen["url"]

    geo.locate("2200 E 53rd St")
    assert "Davenport" in seen["url"]


def test_a_point_outside_the_service_area_is_refused(dbfile, monkeypatch):
    """A mis-heard street that resolves to another state would order the
    technicians by a drive nobody is going to make."""
    import io
    import json

    from src import geo

    monkeypatch.setattr(geo.urllib.request, "urlopen", lambda *a, **k: io.StringIO(
        json.dumps([{"lat": "34.0522", "lon": "-118.2437",
                     "display_name": "Los Angeles"}])))

    out = geo.locate("2200 E 53rd St")
    assert out["ok"] is False
    assert "outside the area" in out["why"]


def test_a_failed_lookup_is_not_cached(dbfile, monkeypatch):
    """A network failure is a bad minute, not a bad address. Caching it would
    make a customer permanently unbookable."""
    from src import db, geo

    def down(*a, **k):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(geo.urllib.request, "urlopen", down)
    assert geo.locate("2200 E 53rd St")["ok"] is False

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM geocodes").fetchone()["n"]
    assert n == 0
