"""Acting on a hazard, which was the half that did not exist.

WHAT WAS BUILT AND WHAT WAS NOT

hazard.py could read every complaint this dealer's customers made, group them
per model, weigh them for danger against what the machine actually is, and
list every other owner of that model. All of that worked and was tested.

Nothing called it. `sweep_hazards` was reachable from one seed script and from
no part of the running system. On the live book that meant a Beverage-Air
HR1HC with three dangerous reports across three sites had twenty-six other
owners, and no code anywhere was going to tell any of them.

And `stop_using_it` already said "We are sending an engineer to take it out
and put a replacement in", in every case, including the ones where nothing had
been booked and nobody had been asked.

WHAT THESE PIN

  the sweep reaches every owner, not only the ones who complained
  a hazard call is queued whether or not marketing consent exists
  it is NOT queued to somebody who revoked consent for a different reason
  certification decides who goes before distance does
  the promise made to the customer matches what was actually arranged
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_dangerous_model(dbfile):
    """One model, dangerous complaints from two different accounts, and a
    third owner who has not rung."""
    from src import db

    with db.txn() as c:
        for n, name in ((1, "Complained Cafe"), (2, "Also Complained Ltd"),
                        (3, "Silent Owner Inc")):
            c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                      "VALUES (?,?,'business',?,'2020-01-01')",
                      (f"A-H{n}", "D-REF", name))
            c.execute("INSERT INTO sites (id,account_id,label,lat,lon) "
                      "VALUES (?,?,'kitchen',41.51,-90.41)",
                      (f"S-H{n}", f"A-H{n}"))
            c.execute(
                "INSERT INTO assets (id,site_id,manufacturer,model_number,"
                "family,installed_on) VALUES (?,?,'Testco','TX-9','display "
                "cooler','2024-01-01')", (f"AS-H{n}", f"S-H{n}"))

        for n in (1, 2):
            c.execute(
                "INSERT INTO complaints (id,dealer_id,account_id,asset_id,"
                "manufacturer,model_number,family,what,raised_at,status) "
                "VALUES (?,?,?,?,'Testco','TX-9','display cooler',?,?, 'open')",
                (f"CP-H{n}", "D-REF", f"A-H{n}", f"AS-H{n}",
                 "there was smoke coming out of the back and it smells burning",
                 "2026-08-01"))
    return "TX-9"


def test_the_sweep_finds_the_owners_who_never_complained(a_dangerous_model):
    """The people who have not rung yet are the entire reason to make the
    call. Two complained; three own one."""
    from src import hazard

    pats = hazard.sweep_hazards("D-REF")["patterns"]
    assert len(pats) == 1
    assert pats[0]["households"] == 2
    assert len(pats[0]["owners"]) == 3

    silent = [o for o in pats[0]["owners"] if o["account"] == "Silent Owner Inc"]
    assert silent, "the owner who has not rung was not found"


def test_every_owner_is_queued_for_a_call(a_dangerous_model):
    """The gap this closes. Detection existed, and nothing acted on it."""
    from src import db, hazard

    out = hazard.act_on_hazards("D-REF", send=False)
    assert out["patterns"] == 1
    assert out["owners_found"] == 3
    assert out["queued"] == 3

    with db.connect() as c:
        rows = c.execute(
            "SELECT account_id, kind, reason FROM outreach_queue "
            "WHERE kind = 'hazard'").fetchall()
    assert len(rows) == 3
    assert all("dangerous reports" in r["reason"] for r in rows)


def test_a_hazard_call_does_not_need_marketing_consent(a_dangerous_model):
    """Somebody who opted out of offers has not opted out of being told their
    cooler can catch fire. None of these accounts has a consent row at all,
    which is refusal for anything else."""
    from src import db, hazard

    with db.connect() as c:
        assert not c.execute("SELECT 1 FROM outreach_consent").fetchone(), (
            "the fixture accidentally granted consent, so this proves nothing")

    assert hazard.act_on_hazards("D-REF", send=False)["queued"] == 3


def test_it_is_still_queued_after_consent_is_revoked(a_dangerous_model):
    """Revoking consent stops marketing. It does not stop a safety call, and
    the two must not be conflated in either direction."""
    from src import db, hazard

    with db.txn() as c:
        c.execute("INSERT INTO outreach_consent (account_id,granted,revoked_on) "
                  "VALUES ('A-H3',0,'2026-01-01')")

    out = hazard.act_on_hazards("D-REF", send=False)
    with db.connect() as c:
        got = c.execute("SELECT 1 FROM outreach_queue WHERE kind='hazard' "
                        "AND account_id='A-H3'").fetchone()
    assert got, "a revoked account was not warned about a dangerous machine"
    assert out["queued"] == 3


def test_running_twice_does_not_warn_anybody_twice(a_dangerous_model):
    """Telling somebody twice about the same hazard on the same machine is
    noise, and noise is how a real warning gets tuned out."""
    from src import db, hazard

    hazard.act_on_hazards("D-REF", send=False)
    second = hazard.act_on_hazards("D-REF", send=False)

    assert second["queued"] == 0
    assert second["already_raised"] == 3

    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM outreach_queue "
                         "WHERE kind='hazard'").fetchone()["n"] == 3


def test_certification_decides_who_goes_before_distance_does(a_dangerous_model):
    """A propane charge is exactly the case where the nearest available person
    is the wrong answer if they cannot open that circuit."""
    from src import db, hazard

    with db.txn() as c:
        # Right next door, and not certified.
        c.execute("INSERT INTO technicians (id,dealer_id,name,phone,lat,lon,"
                  "active) VALUES ('T-NEAR','D-REF','Near Nocert','+15550001',"
                  "41.511,-90.411,1)")
        # Further away, and certified.
        c.execute("INSERT INTO technicians (id,dealer_id,name,phone,email,lat,"
                  "lon,active) VALUES ('T-FAR','D-REF','Far Certified',"
                  "'+15550002','far@example.com',41.60,-90.50,1)")
        c.execute("INSERT INTO technician_certs (technician_id,cert,expires_on)"
                  " VALUES ('T-FAR','EPA608-I','2099-01-01')")

    with db.connect() as c:
        site = dict(c.execute("SELECT id,lat,lon FROM sites WHERE id='S-H1'"
                              ).fetchone())
        picked = hazard._nearest_engineer(c, "D-REF", site, "display cooler",
                                          "R-290")

    assert picked, "nobody was picked at all"
    assert picked["name"] == "Far Certified", (
        "the nearer uncertified engineer was sent to a flammable circuit")


def test_nobody_to_send_is_reported_rather_than_dropped(a_dangerous_model):
    """An owner nobody can be sent to still has to be rung and told to switch
    it off, and somebody has to know a van was never arranged."""
    from src import hazard

    out = hazard.act_on_hazards("D-REF", send=False)
    assert out["assigned"] == 0
    assert len(out["nobody_to_send"]) == 3
    assert out["queued"] == 3, (
        "the warning was dropped because no engineer was available")


def test_the_promise_matches_what_was_arranged(a_dangerous_model):
    """It said "We are sending an engineer" in every case, including when
    nothing had been booked. Somebody who has just switched their only freezer
    off is relying on that sentence."""
    from src import hazard

    pat = hazard.sweep_hazards("D-REF")["patterns"][0]

    booked = hazard.stop_using_it(pat, engineer="Marisol Vance")["say"]
    assert "Marisol Vance is coming out" in booked

    unbooked = hazard.stop_using_it(pat)["say"]
    assert "coming out" not in unbooked
    assert "do not have an engineer's name yet" in unbooked
    assert "ring you back today" in unbooked


def test_the_instruction_comes_before_the_conversation(a_dangerous_model):
    """The CPSC remedy shape. Do not diagnose it at them first."""
    from src import hazard

    pat = hazard.sweep_hazards("D-REF")["patterns"][0]
    say = hazard.stop_using_it(pat)["say"]

    assert "not a sales call" in say
    assert "Do NOT diagnose" in say
    assert say.index("Switch it off") < say.index("Do NOT diagnose")


def test_the_nightly_sweep_actually_runs_it(a_dangerous_model, monkeypatch):
    """The whole bug: it was reachable from a seed script and from nothing
    that runs."""
    from src import outreach

    monkeypatch.setattr(outreach, "sweep_recalls", lambda d="D-REF": [])
    monkeypatch.setattr(outreach, "sweep_predictions", lambda d="D-REF": [])
    monkeypatch.setattr(outreach, "sweep_offers", lambda d="D-REF": [])

    out = outreach.run_sweep("D-REF")
    assert out["hazards"]["owners_found"] == 3
    assert out["hazards"]["queued"] == 3


def test_a_hazard_outranks_a_federal_recall(dbfile):
    """Both are safety. Ours is derived from our own customers' words and is
    the earlier of the two by definition."""
    from src.outreach import PRIORITY

    assert PRIORITY["hazard"] < PRIORITY["recall"] < PRIORITY["prediction"]


def test_one_message_per_engineer_not_one_per_machine(dbfile, monkeypatch):
    """Somebody with six of these on their round gets one list. Six separate
    messages and the sixth is the one they stop reading."""
    from src import hazard

    sent = []
    monkeypatch.setattr("src.email_out.send",
                        lambda to, subj, body, **kw: sent.append((to, body))
                        or {"ok": True, "why": "sent"})

    eng = {"id": "T-1", "name": "One Person", "email": "e@example.com"}
    jobs = [{"account": f"Cust {n}", "site": "kitchen", "machine": "Testco TX-9",
             "flammable": True, "engineer": eng} for n in range(6)]

    out = hazard._brief_the_engineers(jobs, "D-REF")
    assert len(sent) == 1, f"{len(sent)} messages went out for one engineer"
    assert out[0]["machines"] == 6
    assert sent[0][1].count("Testco TX-9") == 6
    assert "FLAMMABLE" in sent[0][1]
