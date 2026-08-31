"""Our records cover them. Their word opens a claim.

THE HOLE THIS CLOSES

`register_asset` takes the install date from whatever the caller says on the
phone, and `covers()` treated that date as though we had written it down when
we sold the machine. So anybody could ring, say it went in last year, and be
quoted zero. Nothing anywhere could tell a record from a claim.

That is not a warranty. It is an honour system with a database attached.

CHARGE, THEN CREDIT. NOT THE OTHER WAY ROUND

Quoting zero on paperwork nobody has read, and then invoicing when it does not
turn up, is precisely how a customer stops believing everything else we told
them. Quoting the real number and taking it off when they produce the invoice
costs them nothing and surprises nobody.

And a claim is settled by a person: the technician who saw the certificate on
the doorstep, or somebody reading the photograph they sent. Never by the desk,
and never on the call.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def terms(dbfile):
    from scripts.load_warranties import load
    return load()


def _machine(source, terms_on_account="net30", asset="AS-FREEZER"):
    """Put the freezer at a known age with a known provenance."""
    from datetime import date, timedelta

    from src import db

    when = (date.today() - timedelta(days=730)).isoformat()   # 2 years old
    with db.txn() as c:
        c.execute("UPDATE assets SET installed_on=?, installed_source=? WHERE id=?",
                  (when, source, asset))
        c.execute("UPDATE accounts SET trade_terms=? WHERE id='A-1'",
                  (terms_on_account,))


# A record is not a claim.


def test_a_machine_we_sold_is_simply_covered(terms, corpus):
    from src import pricing

    _machine("sold_by_us")
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-27T10:00:00")

    assert q["total"] == 0.0
    assert q["covered_by_warranty"] > 0
    assert "would_credit" not in q


def test_their_word_is_charged_and_credited_not_waived(terms, corpus):
    """The bug this whole module exists for. The same machine, the same age,
    the same fault: the only difference is who the date came from."""
    from src import pricing

    _machine("customer_stated")
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-27T10:00:00")

    assert q["total"] > 0, "not waived on a date nobody has verified"
    assert q["covered_by_warranty"] == 0
    assert q["would_credit"] == q["total"]
    assert "Do NOT tell them it is covered" in q["say"]


def test_the_desk_is_given_real_channels_to_send_proof_to(terms, corpus):
    """A channel we do not answer is worse than none: they send it there and
    wait."""
    from src import db, pricing

    with db.txn() as c:
        c.execute("""UPDATE dealers SET proof_email='service@example.test',
                     proof_whatsapp='+18573617165' WHERE id='D-REF'""")

    _machine("customer_stated")
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-27T10:00:00")

    sent = q["send_proof_to"]
    assert sent["whatsapp"] == "+18573617165"
    assert sent["email"] == "service@example.test"
    assert "technician" in sent["on_site"]


def test_covers_reports_the_claim_separately_from_the_cover(terms, corpus):
    """Both facts matter and they are different facts."""
    from src import cover

    _machine("customer_stated")
    out = cover.covers("AS-FREEZER", "Electronic control board")

    assert out["parts"] is False, "we cannot grant it"
    assert out["claimed_parts"] is True, "but on their date it would stand"
    assert out["needs_proof"] is True
    assert out["date_from"] == "customer_stated"


def test_an_unknown_provenance_is_not_treated_as_ours(terms, corpus):
    """Every asset that existed before this column has a null source. Null
    must not mean trusted."""
    from src import cover, db

    _machine("customer_stated")
    with db.txn() as c:
        c.execute("UPDATE assets SET installed_source=NULL WHERE id='AS-FREEZER'")

    assert cover.covers("AS-FREEZER", "Electronic control board")["parts"] is False


def test_registering_a_machine_on_a_call_records_whose_date_it_is(dbfile):
    """The desk writes down what the caller says. It must not launder it into
    a record on the way in."""
    from src import caller, db, trace

    who = caller.resolve("+13095557777")
    with db.txn() as c:
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-R','+13095557777',?,'2026-08-26T13:00:00')",
                  (who["contact_id"],))
    trace.call_context("CALL-R")
    caller.confirm_details(name="Dana Whitfield", account_name="Riverside Taphouse",
                           site_label="Davenport")
    out = caller.register_asset(manufacturer="Traulsen", model_number="G12010",
                                installed_on="2024-03-01")
    trace.call_context("")

    assert out["installed_source"] == "customer_stated"
    with db.connect() as c:
        row = c.execute("SELECT installed_source FROM assets WHERE id=?",
                        (out["asset_id"],)).fetchone()
    assert row["installed_source"] == "customer_stated"


# What the customer is to us.


def test_a_stranger_pays_more_than_somebody_on_account(terms, corpus):
    """No credit terms, no service agreement, nothing known about the site,
    and it is settled on the day."""
    from src import db, standing

    with db.txn() as c:
        c.execute("UPDATE accounts SET trade_terms='net30' WHERE id='A-1'")

    on_account = standing.standing("A-1")
    assert on_account["tier"] == "on_account"
    assert on_account["multiplier"] == 1.0

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,kind,name) "
                  "VALUES ('A-STRANGER','business','Nobody We Know')")

    stranger = standing.standing("A-STRANGER")
    assert stranger["tier"] == "new"
    assert stranger["multiplier"] > 1.0
    assert "no account" in stranger["say"]


def test_a_customer_we_have_worked_for_is_not_a_stranger(terms, corpus):
    """History counts even without trade terms."""
    from src import db, standing

    with db.txn() as c:
        c.execute("UPDATE accounts SET trade_terms=NULL WHERE id='A-1'")

    out = standing.standing("A-1")   # the corpus gave them four closed repairs
    assert out["tier"] == "known"
    assert out["multiplier"] == 1.0


def test_the_rate_says_why_it_is_higher(terms, corpus):
    """A number somebody can argue with is the only kind worth quoting."""
    from src import db, pricing

    # A genuinely new customer, rather than an existing one with their history
    # deleted: the point is somebody we have never touched.
    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,kind,name) "
                  "VALUES ('A-NEW','business','Riverside Taphouse')")
        c.execute("INSERT INTO sites (id,account_id,label,lat,lon) "
                  "VALUES ('S-NEW','A-NEW','Davenport',41.52,-90.57)")
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,
                                         family,installed_on,installed_source)
                     VALUES ('AST-NEW','S-NEW','Traulsen','G12010',
                             'reach-in freezer','2024-03-01','customer_stated')""")

    q = pricing.quote_visit("AST-NEW", when="2026-08-27T10:00:00")
    assert "first visit with no account" in q["rate_from"]


# Settling a claim.


def test_a_photo_is_recorded_but_never_approved_by_the_desk(terms, corpus):
    """A photograph is a photograph. Somebody still has to read it."""
    from src import pricing, standing

    _machine("customer_stated")
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-27T10:00:00")
    claim_id = q["claim"]["claim_id"]

    out = standing.record_proof(claim_id, "whatsapp", "MM-9931")
    assert out["state"] == "evidence_received"
    assert "Do NOT say it is approved" in out["say"]


def test_a_claim_cannot_be_settled_by_nobody(terms, corpus):
    from src import standing

    _machine("customer_stated")
    from src import pricing
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-27T10:00:00")

    assert standing.settle_claim(q["claim"]["claim_id"], True, by="")["ok"] is False


def test_an_accepted_claim_makes_the_date_ours_from_then_on(terms, corpus):
    """The next call about this machine must not start from nothing."""
    from src import cover, pricing, standing

    _machine("customer_stated")
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-27T10:00:00")
    claim_id = q["claim"]["claim_id"]

    standing.record_proof(claim_id, "on_site", "shown to Ray Delgado")
    out = standing.settle_claim(claim_id, True, by="Ray Delgado",
                                note="invoice dated 2024-03-04")

    assert out["credit"] == q["would_credit"]
    assert cover.covers("AS-FREEZER", "Electronic control board")["parts"] is True

    again = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                                when="2026-08-27T10:00:00")
    assert again["total"] == 0.0


def test_a_rejected_claim_leaves_the_job_chargeable(terms, corpus):
    from src import cover, pricing, standing

    _machine("customer_stated")
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-27T10:00:00")
    standing.settle_claim(q["claim"]["claim_id"], False, by="Ray Delgado",
                          note="certificate is for a different serial")

    assert cover.covers("AS-FREEZER", "Electronic control board")["parts"] is False


def test_open_claims_are_visible_so_nothing_rots(terms, corpus):
    from src import pricing, standing

    _machine("customer_stated")
    pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                        when="2026-08-27T10:00:00")

    waiting = standing.open_claims()
    assert len(waiting) == 1
    assert waiting[0]["state"] == "awaiting_proof"
    assert waiting[0]["expires_on"]


# What the desk is told.


def test_the_desk_is_told_the_rule(dbfile):
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    assert "OUR RECORDS COVER THEM. THEIR WORD OPENS A CLAIM." in rules
    assert "Never quote zero on paperwork nobody has read" in rules
    assert "Do NOT say it is approved" in rules


def test_the_claim_tools_are_on_the_desk(dbfile):
    from src import agents

    names = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "where_to_send_proof" in names
    assert "record_proof" in names
