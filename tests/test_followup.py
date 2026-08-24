"""Calls that never connected, conversations cut off, and repairs worth checking.

The rule running through all three: we already know something, and making the
customer say it again is the cost. Somebody whose line dropped after reading
their model number out twice will not read it a third time, they will ring
somebody else.

The missed-call half exists because the call row is written inside the media
stream's start event. A caller who hung up before it connected produced no row
anywhere, so the most expensive thing that can happen to a service desk left
no trace at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _contact_with_phone(db, phone="+13095550101"):
    """A real account, contact and phone, since follow-ups hang off all three."""
    with db.connect() as c:
        row = c.execute(
            """SELECT ct.id contact_id, ct.account_id, p.e164
               FROM phones p JOIN contacts ct ON ct.id = p.contact_id
               LIMIT 1""").fetchone()
    if row is None:
        pytest.skip("fixture has no contact with a phone")
    return row


# 1. the call that never got through


def test_a_missed_call_creates_a_record_where_there_was_none(dbfile):
    """The gap this exists for. No media stream means no row, so a missed call
    simply did not happen as far as the system was concerned."""
    from src import db, followup

    who = _contact_with_phone(db)
    out = followup.record_call_status("CA-NEVER", "no-answer", who["e164"])

    assert out["missed"] is True
    with db.connect() as c:
        call = c.execute("SELECT * FROM calls WHERE twilio_sid='CA-NEVER'").fetchone()
    assert call is not None, "a missed call left no trace"
    assert call["connected"] == 0
    assert call["outcome"] == "missed_no-answer"


def test_a_call_we_actually_served_is_not_recorded_twice(dbfile):
    """Twilio posts a status for every call, including the ones that worked."""
    from src import db, followup

    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,started_at,dealer_id,twilio_sid)
               VALUES ('CALL-OK','+13095550101',?,'D-REF','CA-SERVED')""",
            (datetime.now().isoformat(timespec="seconds"),))

    out = followup.record_call_status("CA-SERVED", "completed", "+13095550101", 240)
    assert out["known"] is True
    assert out.get("missed") is not True

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM calls WHERE twilio_sid='CA-SERVED'"
                      ).fetchone()[0]
    assert n == 1


def test_a_redelivered_status_does_not_queue_two_messages(dbfile):
    """Webhooks arrive twice. Somebody must not get the same apology twice."""
    from src import db, followup

    who = _contact_with_phone(db)
    followup.record_call_status("CA-DUP", "busy", who["e164"])
    followup.record_call_status("CA-DUP2", "busy", who["e164"])

    with db.connect() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM followups WHERE kind='missed_call'").fetchone()[0]
    assert n == 1, "the same person was queued for two apologies"


def test_a_completed_call_we_never_saw_is_flagged_rather_than_swallowed(dbfile):
    """Twilio says it connected and no stream reached us. That is a real fault
    and reporting it as a missed call would hide it."""
    from src import followup

    out = followup.record_call_status("CA-ODD", "completed", "+13095559999", 95)
    assert out.get("odd") is True
    assert out.get("missed") is not True


# 2. the line that went mid-conversation


def test_a_dropped_call_carries_back_what_they_already_said(dbfile):
    """The whole point. They will not read the model number out a third time."""
    from src import db, followup

    who = _contact_with_phone(db)
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,contact_id,started_at,intent,
                                  transcript,dealer_id)
               VALUES ('CALL-CUT',?,?,?,'service',?,'D-REF')""",
            (who["e164"], who["contact_id"],
             datetime.now().isoformat(timespec="seconds"),
             "caller: the walk in cooler at the back is sitting at twelve "
             "degrees since last night\nagent: let me look that up"))

    assert followup.queue_dropped("CALL-CUT")["ok"] is True

    msgs = followup.due("D-REF", at=datetime.now() + timedelta(minutes=5))
    assert msgs, "nothing was queued"
    text = msgs[0]["message"]
    assert "cut off" in text
    assert "twelve degrees" in text, "their own words were not carried back"
    assert "No need to go through it again" in text


def test_a_call_that_dropped_before_anything_was_said_is_left_alone(dbfile):
    """A message about nothing is noise, and noise is how people learn to
    ignore a channel."""
    from src import db, followup

    who = _contact_with_phone(db)
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,contact_id,started_at,intent,
                                  transcript,dealer_id)
               VALUES ('CALL-SILENT',?,?,?,'service','caller: hello','D-REF')""",
            (who["e164"], who["contact_id"],
             datetime.now().isoformat(timespec="seconds")))

    assert followup.queue_dropped("CALL-SILENT")["ok"] is False


def test_settling_a_broken_call_queues_the_resume_by_itself(dbfile):
    """review.settle is the only place that knows a call had an intent and
    still produced nothing, so it is where this belongs."""
    from src import db, followup, review

    who = _contact_with_phone(db)
    with db.txn() as c:
        c.execute(
            """INSERT INTO calls (id,from_e164,contact_id,started_at,ended_at,
                                  intent,transcript,dealer_id)
               VALUES ('CALL-AUTO',?,?,?,?,'service',?,'D-REF')""",
            (who["e164"], who["contact_id"],
             datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds"),
             "caller: the freezer in the back is not holding temperature at all"))

    review.settle("CALL-AUTO")
    assert followup.due("D-REF", at=datetime.now() + timedelta(minutes=5))


# 3. did the repair hold


def test_the_only_feedback_question_is_whether_it_worked(dbfile):
    """Not a satisfaction score. Whether it is still working is the one thing
    about a repair the database cannot tell us for itself."""
    from src import db, followup

    who = _contact_with_phone(db)
    with db.connect() as c:
        site = c.execute("SELECT id FROM sites LIMIT 1").fetchone()
        asset = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
    if site is None or asset is None:
        pytest.skip("fixture has no site or asset")

    with db.txn() as c:
        c.execute(
            """INSERT INTO work_orders (id,account_id,site_id,asset_id,contact_id,
                                        reported_symptom,status,opened_at,dealer_id)
               VALUES ('WO-FU',?,?,?,?,'not holding','closed',?,'D-REF')""",
            (who["account_id"], site["id"], asset["id"], who["contact_id"],
             datetime.now().isoformat(timespec="seconds")))

    out = followup.queue_after_visit("WO-FU")
    assert out["ok"] is True

    msgs = followup.due("D-REF", at=datetime.now() + timedelta(hours=25))
    assert msgs
    text = msgs[0]["message"]
    assert "Is it holding now?" in text
    for word in ("rate", "satisfaction", "score", "out of", "stars"):
        assert word not in text.lower(), f"a satisfaction score crept in: {word}"

    # A whole sentence whichever parts are missing. With no technician on the
    # visit the first version began "to the Traulsen reach-in freezer", which
    # reads as a broken template rather than a message from a person.
    assert text[0].isupper(), f"the message starts mid-clause: {text}"
    assert not text.lower().startswith(("to ", "and ")), text
    assert " to you to " not in text, f"two prepositions in a row: {text}"
    assert "  " not in text, f"a missing part left a double space: {text}"


def test_the_after_visit_question_waits_a_day(dbfile):
    """Same day is too soon to know. A cabinet is cold an hour after a service
    because it was serviced, not because the repair held."""
    from src import db, followup

    who = _contact_with_phone(db)
    with db.connect() as c:
        site = c.execute("SELECT id FROM sites LIMIT 1").fetchone()
        asset = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
    if site is None or asset is None:
        pytest.skip("fixture has no site or asset")
    with db.txn() as c:
        c.execute(
            """INSERT INTO work_orders (id,account_id,site_id,asset_id,contact_id,
                                        reported_symptom,status,opened_at,dealer_id)
               VALUES ('WO-WAIT',?,?,?,?,'x','closed',?,'D-REF')""",
            (who["account_id"], site["id"], asset["id"], who["contact_id"],
             datetime.now().isoformat(timespec="seconds")))
    followup.queue_after_visit("WO-WAIT")

    assert followup.due("D-REF", at=datetime.now()) == []
    assert followup.due("D-REF", at=datetime.now() + timedelta(hours=25))


def test_nobody_is_asked_how_the_repair_went_on_the_call_itself(dbfile):
    """The moment is wrong and the evidence is weaker than what we already have.

    A caller whose freezer just died wants off the phone, and a line that cuts
    during "how did I do" turns a resolved call into an unresolved one.
    """
    from src import agents

    for instruction in (agents.FRONT_INSTRUCTION, agents.DESK_INSTRUCTION):
        low = instruction.lower()
        for phrase in ("rate this call", "how did i do", "satisfaction",
                       "out of five", "survey"):
            assert phrase not in low, f"the desk asks for feedback on the call: {phrase}"


# opt-out and replies


def test_somebody_who_opted_out_is_never_followed_up(dbfile):
    """They said stop. That was about being contacted, not about one record."""
    from src import db, followup

    who = _contact_with_phone(db)
    with db.txn() as c:
        c.execute(
            """INSERT INTO outreach_consent (account_id,granted,revoked_on)
               VALUES (?,0,?)
               ON CONFLICT(account_id) DO UPDATE SET granted=0, revoked_on=?""",
            (who["account_id"], datetime.now().date().isoformat(),
             datetime.now().date().isoformat()))

    out = followup.record_call_status("CA-OPTOUT", "no-answer", who["e164"])
    assert out["missed"] is True, "the call itself should still be recorded"
    assert out.get("followup") is None, "we messaged somebody who opted out"


def test_a_reply_is_tied_back_to_what_we_asked(dbfile):
    """Otherwise the after-visit question is rhetorical: somebody answers
    "yes all good" and it is read as a fresh conversation."""
    from src import db, followup

    who = _contact_with_phone(db)
    with db.txn() as c:
        c.execute(
            """INSERT INTO followups (id,dealer_id,kind,phone,context,
                                      due_after,status,sent_at,created_at)
               VALUES ('FU-1','D-REF','after_visit',?,'x',?,'sent',?,?)""",
            (who["e164"], datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds")))

    assert followup.record_reply(who["e164"], "yes all good thanks")["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT status, reply FROM followups WHERE id='FU-1'").fetchone()
    assert row["status"] == "answered"
    assert "all good" in row["reply"]


def test_messages_are_assembled_not_written_by_a_model(dbfile):
    """Same rule as the technician briefing. Nothing goes out unattended to a
    customer containing a sentence nobody chose."""
    import inspect

    from src import followup

    src = inspect.getsource(followup.render)
    for banned in ("generate_content", "llm", "Agent", "genai"):
        assert banned not in src, f"render reaches for a model: {banned}"
