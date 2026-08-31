"""Ringing people who did not ring us, which is where this could do harm.

Every other part of this system responds to somebody who chose to make contact.
This part reaches into a customer's day uninvited, so the tests here are
weighted towards refusing rather than acting.

The rule the file exists to pin: **absence of a consent record is not consent.**
That is the default that outbound systems get wrong, and getting it wrong means
ringing people who never agreed.

The one deliberate exception is a federal safety recall, and it is an exception
in code you can point at rather than an accident of how the checks happen to be
ordered. Somebody who opted out of offers has not opted out of being told their
oven can electrocute them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from conftest import REF


def _consent(db, account_id, granted=1, revoked=None, before=540, after=1020,
             gap=30, form="written"):
    """Grant consent. Written by default, because that is the standard an AI
    voice has to meet for a marketing call, and tests about frequency or quiet
    hours should not silently trip the consent-form gate instead."""
    with db.txn() as c:
        c.execute(
            """INSERT OR REPLACE INTO outreach_consent
               (account_id,granted,granted_on,granted_via,revoked_on,
                quiet_before,quiet_after,max_per_days,consent_form)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (account_id, granted, "2026-01-01", "test", revoked,
             before, after, gap, form))


def _recall(db, brands, title, hazard="Electrocution"):
    with db.txn() as c:
        c.execute(
            """INSERT INTO recalls
               (recall_number,recall_date,title,hazard,remedy,brands,url)
               VALUES (?,?,?,?,?,?,?)""",
            (f"R-{brands[:6]}", "2026-07-30", title, hazard, "stop using it",
             brands, "https://cpsc.gov/x"))


def _complaint(db, account, asset, what, category="reliability", days_ago=10):
    when = (datetime.now() - timedelta(days=days_ago)).isoformat()
    with db.txn() as c:
        c.execute(
            """INSERT INTO complaints
               (id,dealer_id,account_id,asset_id,manufacturer,model_number,
                family,what,category,severity,raised_at,status)
               VALUES (?,?,?,?,?,?,?,?,?,'major',?,'open')""",
            (f"CM-{abs(hash(what)) % 99999}", REF, account, asset, "Traulsen",
             "G12010", "reach-in freezer", what, category, when))


# --------------------------------------------------------------------------
# consent
# --------------------------------------------------------------------------

def test_no_consent_record_means_no_marketing_call(corpus):
    """The default that outbound systems get wrong."""
    from src import outreach

    got = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "they might like one",
        "evidence": "-"}], REF)

    assert got["queued"] == []
    assert got["blocked"][0]["blocked_because"] == "no consent on record"


def test_revoked_consent_blocks(corpus):
    from src import db, outreach

    _consent(db, "A-1", granted=1, revoked="2026-06-01")
    got = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))

    assert got["queued"] == []
    assert "revoked" in got["blocked"][0]["blocked_because"]


def test_granted_consent_allows_an_offer(corpus):
    from src import db, outreach

    _consent(db, "A-1")
    got = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))

    assert len(got["queued"]) == 1


def test_a_safety_recall_ignores_marketing_consent(corpus):
    """Opting out of offers is not opting out of a hazard notice."""
    from src import outreach

    got = outreach.queue_outreach([{
        "kind": "recall", "account_id": "A-1", "account_name": "x",
        "asset_id": "AS-FREEZER",
        "reason": "under federal recall", "evidence": "electrocution"}], REF)

    assert len(got["queued"]) == 1
    assert got["queued"][0]["kind"] == "recall"


def test_a_recall_still_cannot_be_raised_twice(corpus):
    """Telling somebody twice about one hazard is how a warning gets ignored."""
    from src import outreach

    cand = [{"kind": "recall", "account_id": "A-1", "account_name": "x",
             "asset_id": "AS-FREEZER", "reason": "r", "evidence": "e"}]
    outreach.queue_outreach(cand, REF)
    again = outreach.queue_outreach(cand, REF)

    assert again["queued"] == []
    assert again["blocked"][0]["blocked_because"] == "already raised"


def test_the_frequency_cap_is_respected(corpus):
    """Consent is not permission to ring somebody every week."""
    from src import db, outreach

    _consent(db, "A-1", gap=30)
    first = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r1", "evidence": "-"}], REF)

    with db.txn() as c:
        c.execute("UPDATE outreach_queue SET status='called', called_at=? WHERE id=?",
                  (datetime.now().isoformat(), first["queued"][0]["outreach_id"]))

    second = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r2", "evidence": "-"}], REF)

    assert second["queued"] == []
    assert "within 30 days" in second["blocked"][0]["blocked_because"]


# --------------------------------------------------------------------------
# what gets found
# --------------------------------------------------------------------------

def test_a_recalled_machine_reaches_its_owner(corpus):
    from src import db, outreach

    _recall(db, "Traulsen Reach-In Freezers",
            "Traulsen Recalls Reach-In Freezers")
    found = outreach.sweep_recalls(REF)

    assert any(f["asset_id"] == "AS-FREEZER" for f in found)
    hit = next(f for f in found if f["asset_id"] == "AS-FREEZER")
    assert "Electrocution" in hit["evidence"]
    assert "Do not sell them anything" in hit["say"]


def test_an_accessory_recall_does_not_trigger_a_call(corpus):
    """Ringing to say a laptop is dangerous over a recalled power bank."""
    from src import db, outreach

    _recall(db, "Dell Laptop Battery Packs",
            "Dell Recalls Laptop Battery Packs", hazard="fire")
    found = outreach.sweep_recalls("D-IT")

    assert all("battery" not in (f["evidence"] or "").lower() for f in found)


def test_a_price_complaint_never_predicts_a_failure(corpus):
    """The bug this caught before it shipped.

    "Quoted nearly four hundred for a control board, that is absurd" matched
    control board failures at 0.68 because it contains the words. Acting on it
    means ringing a customer to warn their machine is failing because they
    grumbled about an invoice.
    """
    from src import db, outreach

    _complaint(db, "A-1", "AS-FREEZER",
               "quoted nearly four hundred for a control board, that is absurd",
               category="parts_cost")
    found = outreach.sweep_predictions(REF)

    assert all("absurd" not in f["evidence"] for f in found)


def test_a_real_symptom_does_predict(corpus):
    from src import db, outreach

    _complaint(db, "A-1", "AS-FREEZER",
               "there is frost building up at the back we keep chipping off",
               category="reliability")
    found = outreach.sweep_predictions(REF)

    assert found, "a genuine early symptom produced no warning"
    assert found[0]["confidence"] >= outreach.PREDICTION_FLOOR
    assert "Never state their machine is failing" in found[0]["say"]


def test_an_old_complaint_is_not_worth_a_call(corpus):
    """Beyond the window the customer thinks we are inventing problems."""
    from src import db, outreach

    _complaint(db, "A-1", "AS-FREEZER",
               "frost building up at the back", days_ago=400)
    assert outreach.sweep_predictions(REF) == []


def test_a_machine_with_an_open_job_is_not_warned_about(corpus):
    """They already told us. Warning them again teaches them to ignore us."""
    from src import db, outreach, tools

    class Ctx:
        def __init__(self):
            self.state = {"dealer_id": REF, "caller": {}}

    _complaint(db, "A-1", "AS-FREEZER",
               "there is frost building up at the back we keep chipping off")
    assert outreach.sweep_predictions(REF)

    tools.open_work_order("AS-FREEZER", "frost on the coil", Ctx())
    assert outreach.sweep_predictions(REF) == []


def test_an_offer_is_never_for_something_they_already_own(corpus):
    """A second identical freezer is a catalogue read aloud, not advice."""
    from src import db, outreach

    with db.connect() as c:
        owned = {(r["account_id"], r["family"]) for r in
                 c.execute("SELECT account_id, family FROM account_families")}

    for o in outreach.sweep_offers(REF, min_support=1):
        assert (o["account_id"], o["suggest_family"]) not in owned


# --------------------------------------------------------------------------
# ordering and timing
# --------------------------------------------------------------------------

def test_a_recall_outranks_a_sales_call(corpus):
    """Absolute, not a weighting."""
    from src import db, outreach

    _consent(db, "A-1")
    outreach.queue_outreach([
        {"kind": "offer", "account_id": "A-1", "account_name": "x",
         "asset_id": None, "reason": "buy this", "evidence": "-"},
        {"kind": "recall", "account_id": "A-1", "account_name": "x",
         "asset_id": "AS-FREEZER", "reason": "hazard", "evidence": "-"},
    ], REF, at=datetime(2026, 8, 31, 10, 0))

    ready = outreach.due_now(REF, at=datetime(2026, 8, 31, 11, 0))["ready"]
    assert ready[0]["kind"] == "recall"


def test_nobody_is_rung_outside_their_quiet_hours(corpus):
    from src import db, outreach

    _consent(db, "A-1", before=540, after=1020)
    outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))

    # THE NIGHT AFTER, not two in the morning on the day it was queued.
    #
    # queue_outreach stamps due_after with the real clock, and due_now only
    # considers items whose due_after has passed. Asking about 02:00 TODAY
    # therefore excluded the item entirely whenever the suite ran after 2am,
    # which is almost always: the queue came back empty and the test read
    # that as "nobody was held", which is not what it is checking.
    #
    # The claim is about the hour of day, so any 02:00 will do.
    night = outreach.due_now(REF, at=datetime(2026, 9, 1, 2, 0))
    assert night["ready"] == []
    assert night["held_for_quiet_hours"]

    day = outreach.due_now(REF, at=datetime(2026, 9, 1, 11, 0))
    assert day["ready"]


def test_the_sweep_is_safe_to_run_twice(corpus):
    """Scheduled jobs fire late, twice, or not at all.

    Idempotence is enforced at the queue rather than by remembering when it
    last ran, so a scheduler misfiring cannot produce a wrong outcome.
    """
    from src import db, outreach

    _consent(db, "A-1")
    _recall(db, "Traulsen Reach-In Freezers", "Traulsen Recalls Freezers")

    first = outreach.run_sweep(REF)
    second = outreach.run_sweep(REF)

    assert first["queued"], "the first sweep queued nothing to compare"
    assert second["queued"] == [], "running twice queued the same calls again"

    with db.connect() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM outreach_queue WHERE kind='recall'").fetchone()[0]
    assert n == len([q for q in first["queued"] if q["kind"] == "recall"])


# --------------------------------------------------------------------------
# the consent standard an AI voice actually has to meet
# --------------------------------------------------------------------------

def test_oral_consent_is_not_enough_for_a_marketing_call(corpus):
    """The FCC's 2024 ruling makes an AI voice an artificial voice.

    Marketing with one needs prior express WRITTEN consent, at $500 to $1,500
    a violation. "They said yes on a service call" is oral, and the first
    version of this treated any consent row as sufficient.
    """
    from src import db, outreach

    _consent(db, "A-1", form="oral")

    got = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))

    assert got["queued"] == []
    assert "not enough for a marketing call" in got["blocked"][0]["blocked_because"]


def test_written_consent_allows_a_marketing_call(corpus):
    from src import db, outreach

    _consent(db, "A-1", form="written")

    got = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))
    assert len(got["queued"]) == 1


def test_a_safety_recall_does_not_need_written_consent(corpus):
    """A hazard notice is not marketing, so the marketing standard is not it."""
    from src import db, outreach

    _consent(db, "A-1", form="oral")

    got = outreach.queue_outreach([{
        "kind": "recall", "account_id": "A-1", "account_name": "x",
        "asset_id": "AS-FREEZER", "reason": "hazard", "evidence": "-"}], REF)
    assert len(got["queued"]) == 1


def test_a_predicted_failure_is_treated_as_marketing(corpus):
    """It is a sales opportunity wearing a warning, and it is gated as one."""
    from src import db, outreach

    _consent(db, "A-1", form="oral")

    got = outreach.queue_outreach([{
        "kind": "prediction", "account_id": "A-1", "account_name": "x",
        "asset_id": "AS-FREEZER", "reason": "r", "evidence": "-"}], REF)
    assert got["queued"] == []


# --------------------------------------------------------------------------
# placing the calls, which nothing did
# --------------------------------------------------------------------------

def test_a_call_can_actually_be_taken_off_the_queue(corpus):
    """The queue had no consumer. It was a very well-tested list."""
    from datetime import datetime

    from src import outreach

    outreach.queue_outreach([{
        "kind": "recall", "account_id": "A-1", "account_name": "x",
        "asset_id": "AS-FREEZER", "reason": "hazard", "evidence": "e"}], REF,
        at=datetime(2026, 8, 31, 10, 0))

    got = outreach.take_next(REF, at=datetime(2026, 8, 31, 11, 0))
    assert got["call"] is not None
    assert got["call"]["kind"] == "recall"
    assert "not a sales call" in got["call"]["opening"]
    assert "automated assistant" in got["call"]["disclosure"]


def test_the_same_call_cannot_be_taken_twice(corpus):
    """Claimed in one transaction, like a reserved part."""
    from datetime import datetime

    from src import outreach

    outreach.queue_outreach([{
        "kind": "recall", "account_id": "A-1", "account_name": "x",
        "asset_id": "AS-FREEZER", "reason": "hazard", "evidence": "e"}], REF,
        at=datetime(2026, 8, 31, 10, 0))

    at = datetime(2026, 8, 31, 11, 0)
    first = outreach.take_next(REF, at=at)
    second = outreach.take_next(REF, at=at)

    assert first["call"] is not None
    assert second["call"] is None


def test_opting_out_is_honoured_permanently(corpus):
    """A queue you cannot leave is harassment with a schedule."""
    from datetime import datetime

    from src import db, outreach

    _consent(db, "A-1", form="written")

    outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))
    taken = outreach.take_next(REF, at=datetime(2026, 8, 31, 11, 0))

    out = outreach.record_outcome(taken["call"]["outreach_id"], "opted_out")
    assert out["consent_revoked"]

    again = outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "different offer", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))
    assert again["queued"] == []


def test_a_wrong_number_also_stops_the_calls(corpus):
    from datetime import datetime

    from src import db, outreach

    _consent(db, "A-1", form="written")
    outreach.queue_outreach([{
        "kind": "offer", "account_id": "A-1", "account_name": "x",
        "asset_id": None, "reason": "r", "evidence": "-"}], REF,
        at=datetime(2026, 8, 31, 10, 0))
    taken = outreach.take_next(REF, at=datetime(2026, 8, 31, 11, 0))

    assert outreach.record_outcome(taken["call"]["outreach_id"],
                                   "wrong_number")["consent_revoked"]


def test_nothing_is_taken_outside_quiet_hours(corpus):
    from datetime import datetime

    from src import outreach

    outreach.queue_outreach([{
        "kind": "recall", "account_id": "A-1", "account_name": "x",
        "asset_id": "AS-FREEZER", "reason": "hazard", "evidence": "e"}], REF,
        at=datetime(2026, 8, 31, 10, 0))

    assert outreach.take_next(REF, at=datetime(2026, 8, 31, 3, 0))["call"] is None


def test_quiet_hours_are_the_customers_clock_not_the_servers(dbfile,
                                                             monkeypatch):
    """THE BUG THIS PINS, found on the live VM.

    Quiet hours are minutes past midnight in the CUSTOMER's local time, and
    due_now compared them against datetime.now(). On a laptop in Central time
    that is right by coincidence. Production runs Etc/UTC:

        VM clock   03:27 UTC
        Chicago    22:27

    The damaging direction is the afternoon. At 22:00 UTC it is 17:00 in
    Chicago, an ordinary time to ring a restaurant, and the comparison refused
    it for being past a 20:00 cutoff. The bug silently blocked the whole US
    afternoon and only looked correct in the evening it was tested in.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src import outreach

    class FrozenUTC(datetime):
        @classmethod
        def now(cls, tz=None):
            utc = datetime(2026, 8, 30, 22, 0, tzinfo=ZoneInfo("UTC"))
            return utc.astimezone(tz) if tz else utc.replace(tzinfo=None)

    monkeypatch.setattr(outreach, "datetime", FrozenUTC)

    local = outreach._local_now("D-REF")
    assert local.hour == 17, (
        f"22:00 UTC is 17:00 in Chicago, got {local.hour}:00. Quiet hours "
        "are being measured against the server clock again.")


def test_a_bad_timezone_does_not_stop_the_queue(dbfile):
    """A dealer row with a nonsense timezone must not take the queue down."""
    from src import db, outreach

    with db.txn() as c:
        c.execute("UPDATE dealers SET timezone='Not/AZone' WHERE id='D-REF'")

    assert outreach._local_now("D-REF") is not None


def test_revoking_consent_stops_a_call_already_in_the_queue(dbfile):
    """FOUND BY THE OWNER, and worth more than any test I wrote.

    Consent was checked when the sweep put an item INTO the queue and never
    again. So withdrawing it stopped future items being queued and did nothing
    to the one already sitting there: revoke at 09:00, and the call queued
    yesterday still went out at 11:00.

    That is the failure that matters legally. Withdrawal has to take effect on
    the next CALL, not on the next sweep, which is the entire point of being
    able to withdraw it.
    """
    from datetime import date, datetime

    from src import db, outreach

    with db.connect() as c:
        acct = c.execute(
            "SELECT id FROM accounts WHERE dealer_id='D-REF' LIMIT 1").fetchone()["id"]

    with db.txn() as c:
        c.execute(
            """INSERT INTO outreach_consent
                 (account_id,granted,granted_on,granted_via,quiet_before,
                  quiet_after,max_per_days,consent_form)
               VALUES (?,1,'2026-01-01','test',540,1200,3,'written')
               ON CONFLICT(account_id) DO UPDATE SET
                 granted=1, revoked_on=NULL, consent_form='written'""",
            (acct,))
        c.execute(
            """INSERT INTO outreach_queue
                 (id,dealer_id,account_id,kind,reason,evidence,priority,
                  status,due_after)
               VALUES ('OUT-TEST1',?,?,'offer','a thing','because',5,
                       'queued','2020-01-01')""",
            ("D-REF", acct))

    at = datetime(2026, 8, 30, 11, 0)
    ready = outreach.due_now("D-REF", at=at)["ready"]
    assert any(x["outreach_id"] == "OUT-TEST1" for x in ready), (
        "the fixture is wrong: it should be dialable before revocation")

    with db.txn() as c:
        c.execute("UPDATE outreach_consent SET revoked_on=? WHERE account_id=?",
                  (date.today().isoformat(), acct))

    out = outreach.due_now("D-REF", at=at)
    assert not any(x["outreach_id"] == "OUT-TEST1" for x in out["ready"]), (
        "consent was revoked and the queued call is still dialable")
    assert any(x["outreach_id"] == "OUT-TEST1"
               for x in out["dropped_since_queued"]), (
        "it must be reported as dropped, not silently vanish")


def test_a_safety_recall_still_goes_out_after_consent_is_revoked(dbfile):
    """A hazard notice is not marketing and consent was never what permitted
    it. Withdrawing marketing consent must not stop somebody being told their
    freezer is under a federal recall."""
    from datetime import date, datetime

    from src import db, outreach

    with db.connect() as c:
        acct = c.execute(
            "SELECT id FROM accounts WHERE dealer_id='D-REF' LIMIT 1").fetchone()["id"]

    with db.txn() as c:
        c.execute(
            """INSERT INTO outreach_consent
                 (account_id,granted,granted_on,granted_via,quiet_before,
                  quiet_after,consent_form,revoked_on)
               VALUES (?,1,'2026-01-01','test',540,1200,'written',?)
               ON CONFLICT(account_id) DO UPDATE SET revoked_on=excluded.revoked_on""",
            (acct, date.today().isoformat()))
        c.execute(
            """INSERT INTO outreach_queue
                 (id,dealer_id,account_id,kind,reason,evidence,priority,
                  status,due_after)
               VALUES ('OUT-TEST2',?,?,'recall','recalled','CPSC',1,
                       'queued','2020-01-01')""",
            ("D-REF", acct))

    ready = outreach.due_now("D-REF", at=datetime(2026, 8, 30, 11, 0))["ready"]
    assert any(x["outreach_id"] == "OUT-TEST2" for x in ready)
