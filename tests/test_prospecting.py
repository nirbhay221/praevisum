"""Approaching a business we have never met, and mostly refusing to.

WHAT THESE TESTS ARE PROTECTING

Prospecting is the one feature in this system with a direct incentive to
misbehave. Every other tool gets more useful by being more careful; this one
gets more "productive" by dialling more numbers, and the constraints on it are
legal rather than technical, so nothing will crash if they are quietly
removed. That makes the gates worth more test coverage than the feature.

The three that matter, and the exact reason each exists:

  LINE TYPE. The Telemarketing Sales Rule exempts marketer-to-business calls
  and the DNC registry does not reach them, which is what makes any of this
  lawful. It stops at the handset: the FCC treats an AI-generated voice as an
  artificial voice, the TCPA treats every wireless number as residential
  whoever owns it, and there is no business carve-out for a mobile. So a
  landline may be rung and nothing else may, and an unknown line type has to
  count as a mobile.

  THE INTERNAL LIST. Separate from the federal registry, outlives the
  relationship, kept four years, and overrides everything.

  THE EVIDENCE. A prospect with no public reason attached is a cold call
  wearing a feature's clothes.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# the line type gate
# --------------------------------------------------------------------------

def test_unknown_line_type_is_treated_as_a_mobile(dbfile):
    """The permissive default is the expensive one. Every path that fails to
    learn what a number is must answer 'mobile', because that is the answer
    that forbids the call."""
    from src import linetype

    assert linetype.UNKNOWN == "mobile"
    assert linetype.line_type("not-a-number")["line_type"] == "mobile"
    assert linetype.line_type("+15551230000",
                              allow_lookup=False)["line_type"] == "mobile"


def test_only_a_landline_may_be_rung(dbfile):
    """VoIP is refused along with mobile. A VoIP number can terminate on a
    handset in somebody's pocket and the carrier data cannot say whether it
    does, so it is not a landline for this purpose."""
    from src import linetype

    assert linetype.MAY_RING == ("landline",)
    assert "voip" not in linetype.MAY_RING
    assert "mobile" not in linetype.MAY_RING


def test_a_mobile_is_refused_with_the_reason_said_out_loud(dbfile, monkeypatch):
    from src import linetype, prospect

    monkeypatch.setattr(linetype, "line_type",
                        lambda n, allow_lookup=True: {"line_type": "mobile"})

    out = prospect.may_we_approach("+15551230000", at="2026-08-28T11:00:00")
    assert out["may_call"] is False
    assert out["gate"] == "line_type"
    assert "no business exemption" in out["why"]


def test_a_landline_in_hours_may_be_rung(dbfile, monkeypatch):
    from src import linetype, prospect

    monkeypatch.setattr(linetype, "line_type",
                        lambda n, allow_lookup=True: {"line_type": "landline",
                                                      "carrier": "test"})

    out = prospect.may_we_approach("+15551230000", at="2026-08-28T11:00:00")
    assert out["may_call"] is True


# --------------------------------------------------------------------------
# the internal do not call list
# --------------------------------------------------------------------------

def test_asking_us_to_stop_outranks_everything(dbfile, monkeypatch):
    """Checked before the clock and before the line type, so a number on the
    list never even costs a lookup, and no other condition can let it through."""
    from src import linetype, prospect

    def explode(*a, **k):
        raise AssertionError("a number on the list must never be looked up")

    linetype.stop_calling("+15551230000", note="asked on a service call")
    monkeypatch.setattr(linetype, "line_type", explode)

    out = prospect.may_we_approach("+15551230000", at="2026-08-28T11:00:00")
    assert out["may_call"] is False
    assert out["gate"] == "do_not_call"


def test_the_record_of_a_stop_request_is_kept_for_four_years(dbfile):
    """The retention is the evidence the request was honoured, so the row is
    never deleted."""
    from datetime import date

    from src import db, linetype

    linetype.stop_calling("+15551230000")
    with db.connect() as c:
        row = c.execute("SELECT asked_on, keep_until FROM do_not_call "
                        "WHERE e164=?", ("+15551230000",)).fetchone()

    asked = date.fromisoformat(row["asked_on"])
    keep = date.fromisoformat(row["keep_until"])
    assert (keep - asked).days >= 365 * 4


def test_stopping_twice_does_not_restart_the_clock(dbfile):
    from src import db, linetype

    linetype.stop_calling("+15551230000", note="first")
    linetype.stop_calling("+15551230000", note="second")

    with db.connect() as c:
        rows = c.execute("SELECT note FROM do_not_call WHERE e164=?",
                         ("+15551230000",)).fetchall()
    assert len(rows) == 1
    assert rows[0]["note"] == "first"


# --------------------------------------------------------------------------
# the clock
# --------------------------------------------------------------------------

@pytest.mark.parametrize("when,allowed", [
    ("2026-08-28T08:30:00", False),
    ("2026-08-28T09:00:00", True),
    ("2026-08-28T14:00:00", True),
    ("2026-08-28T19:00:00", False),
    ("2026-08-28T22:30:00", False),
])
def test_calls_only_happen_during_the_working_day(dbfile, monkeypatch, when,
                                                  allowed):
    from src import linetype, prospect

    monkeypatch.setattr(linetype, "line_type",
                        lambda n, allow_lookup=True: {"line_type": "landline"})

    out = prospect.may_we_approach("+15551230000", at=when)
    assert out["may_call"] is allowed


# --------------------------------------------------------------------------
# the evidence
# --------------------------------------------------------------------------

def test_the_vocabulary_is_derived_from_what_customers_actually_said(dbfile):
    """Not a hand-written keyword list. The words come from the reported
    symptoms and complaints on file, so the matcher improves as the corpus
    grows rather than as somebody edits a constant."""
    from src import db, prospect

    with db.connect() as c:
        row = c.execute(
            """SELECT a.id account_id, s.id site_id
               FROM accounts a JOIN sites s ON s.account_id = a.id
               WHERE a.dealer_id='D-REF' LIMIT 1""").fetchone()

    with db.txn() as c:
        for wid, said in (
                ("WO-P1", "not cold enough, food spoiling on the shelf"),
                ("WO-P2", "water pooling underneath it overnight")):
            c.execute(
                """INSERT INTO work_orders
                     (id,account_id,site_id,dealer_id,reported_symptom,
                      status,opened_at)
                   VALUES (?,?,?,'D-REF',?,'open','2026-08-01')""",
                (wid, row["account_id"], row["site_id"], said))

    words = prospect.distress_words("D-REF")
    assert "spoiling" in words
    assert "pooling" in words


def test_one_matching_word_is_not_a_fault(dbfile):
    """"Cold beer" is a compliment. A single term co-occurring is a
    coincidence, which is why two are required before anybody is rung."""
    from src import prospect

    words = ["cold", "water", "down", "food", "pooling"]
    out = prospect.read_the_signal("Great tacos and lovely cold beer.", words)
    assert out["signal"] is False


def test_a_described_fault_is_caught_and_quoted_verbatim(dbfile):
    """The quote is the feature. Ringing somebody to say 'our model believes
    you have a problem' is worthless; reading their own sentence back is not."""
    from src import prospect

    words = ["cold", "water", "down", "food", "pooling", "freezer"]
    out = prospect.read_the_signal(
        "Lovely staff. Food was not cold enough and there was water pooling "
        "by the freezer.", words)

    assert out["signal"] is True
    assert "water pooling" in out["quote"]
    assert len(out["terms"]) >= 2


def test_a_dealer_with_no_history_refuses_to_prospect(dbfile):
    """No corpus means no idea what distress sounds like in this trade, and
    the honest response is to decline rather than to invent vocabulary."""
    from src import db, prospect

    with db.txn() as c:
        c.execute("DELETE FROM complaints")
        c.execute("DELETE FROM work_orders")

    out = prospect.sweep_prospects("D-REF", near="Davenport, IA")
    assert out["ok"] is False
    assert "too few complaints" in out["why"]


def test_finding_prospects_never_dials_and_never_spends_by_default(dbfile):
    """Finding, qualifying and calling are three decisions. The default for
    the two that cost money is no."""
    import inspect

    from src import prospect

    sig = inspect.signature(prospect.sweep_prospects)
    assert sig.parameters["allow_lookup"].default is False

    # THE SWEEP STILL MUST NOT DIAL. Dialling now exists, in
    # ring_this_prospect, and finding is still a separate decision from
    # ringing: a sweep that could start calling by accident is the whole
    # failure this separation prevents.
    src = inspect.getsource(prospect.sweep_prospects)
    for forbidden in ("place_call", "twilio.calls.create", ".dial("):
        assert forbidden not in src


# --------------------------------------------------------------------------
# dialling, which may only happen through the gate
# --------------------------------------------------------------------------

@pytest.fixture
def one_prospect(dbfile):
    from src import db

    with db.txn() as c:
        c.execute(
            """INSERT INTO prospects
                 (id,dealer_id,name,kind,address,phone_e164,source,found_on,
                  signal,signal_kind,signal_score,signal_seen)
               VALUES ('P-T1','D-REF','Riverbend Diner','restaurant',
                       '1 River Drive','+15635550101','test','2026-08-30',
                       'cold, water','public_complaint',1.0,
                       'water pooling by the freezer door')""")
    return "P-T1"


def test_a_mobile_prospect_is_never_dialled(one_prospect, monkeypatch):
    """The whole feature in one test. The gate refuses and no call is placed,
    and the refusal names the rule rather than merely saying no."""
    from src import linetype, outbound, prospect

    monkeypatch.setattr(linetype, "line_type",
                        lambda n, allow_lookup=True: {"line_type": "mobile"})

    def must_not_dial(*a, **k):
        raise AssertionError("a mobile prospect was dialled")

    monkeypatch.setattr(outbound, "place_call", must_not_dial)

    out = prospect.ring_this_prospect("P-T1", "D-REF",
                                      at="2026-08-30T11:00:00")
    assert out["called"] is False
    assert out["refused_by"] == "line_type"
    assert "no business exemption" in out["why"]


def test_someone_who_asked_us_to_stop_is_never_dialled(one_prospect,
                                                       monkeypatch):
    from src import linetype, outbound, prospect

    linetype.stop_calling("+15635550101", note="asked us to stop")
    monkeypatch.setattr(outbound, "place_call",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("dialled someone on the list")))

    out = prospect.ring_this_prospect("P-T1", "D-REF",
                                      at="2026-08-30T11:00:00")
    assert out["called"] is False
    assert out["refused_by"] == "do_not_call"


def test_out_of_hours_is_never_dialled(one_prospect, monkeypatch):
    from src import linetype, outbound, prospect

    monkeypatch.setattr(linetype, "line_type",
                        lambda n, allow_lookup=True: {"line_type": "landline"})
    monkeypatch.setattr(outbound, "place_call",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("dialled at night")))

    out = prospect.ring_this_prospect("P-T1", "D-REF",
                                      at="2026-08-30T23:30:00")
    assert out["called"] is False
    assert out["refused_by"] == "hours"


def test_a_cleared_landline_is_dialled_and_opens_with_their_words(
        one_prospect, monkeypatch):
    from src import linetype, outbound, prospect

    monkeypatch.setattr(linetype, "line_type",
                        lambda n, allow_lookup=True: {"line_type": "landline",
                                                      "carrier": "test"})
    placed = {}

    def fake_call(to, ref, from_number=""):
        placed["to"] = to
        return {"ok": True, "sid": "CAtest"}

    monkeypatch.setattr(outbound, "place_call", fake_call)

    out = prospect.ring_this_prospect("P-T1", "D-REF",
                                      at="2026-08-30T11:00:00")
    assert out["called"] is True
    assert placed["to"] == "+15635550101"
    assert out["open_with"] == "water pooling by the freezer door"


def test_the_same_prospect_is_not_rung_twice(one_prospect, monkeypatch):
    """A list that keeps coming round is how a prospecting tool becomes a
    nuisance, which is a reputational problem before it is a legal one."""
    from src import linetype, outbound, prospect

    monkeypatch.setattr(linetype, "line_type",
                        lambda n, allow_lookup=True: {"line_type": "landline"})
    monkeypatch.setattr(outbound, "place_call",
                        lambda *a, **k: {"ok": True, "sid": "CAtest"})

    assert prospect.ring_this_prospect("P-T1", "D-REF",
                                       at="2026-08-30T11:00:00")["called"]
    again = prospect.ring_this_prospect("P-T1", "D-REF",
                                        at="2026-08-30T11:00:00")
    assert again["called"] is False
    assert "already approached" in again["why"]


def test_there_is_exactly_one_way_to_place_a_call(dbfile):
    """Structural. The gate is only load-bearing if it cannot be walked
    around, so the file must contain a single dial and it must sit inside
    ring_this_prospect, after may_we_approach."""
    import inspect

    from src import prospect

    whole = inspect.getsource(prospect)
    assert whole.count("outbound.place_call") == 1

    ring = inspect.getsource(prospect.ring_this_prospect)
    assert ring.index("may_we_approach") < ring.index("outbound.place_call")
