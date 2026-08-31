"""What happens after somebody says yes, which was nothing.

FOUR LOOPS, ALL OF THEM LEFT OPEN

An order could be placed, confirmed, sourced and promised, and then the system
stopped caring. Everything downstream of the sale was missing, and the gaps
were not cosmetic:

  THE SALE DID NOT RECORD THE SALE. confirm_purchase_order updated a status
  and ordered stock, and never wrote that the customer owned anything. So
  standing.py's whole distinction between cover that is OURS to grant and
  cover that is a CLAIM they must prove had no source of truth. Nothing ever
  wrote sold_by_us, because the one moment that could threw the fact away.
  Every customer who bought from us was treated, on their next call, exactly
  like somebody who walked in with a competitor's machine.

  NOBODY WAS EVER ASKED IF THEY WANTED COVER. And more to the point nobody was
  ever told what the standard term already gave them, which is the part they
  are entitled to hear before they decide.

  NOTHING WAS EVER DELIVERED. There was no delivery event at all. Orders sat
  at "confirmed" forever, and cover was dated from the PROMISE, so a carrier
  running two days late silently cost the customer two days of warranty, in
  our favour.

  THE FOLLOW-UP JUDGED NOBODY. followup.queue_after_visit already asked the
  right question, and the answer was never attached to whoever did the work.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def an_order(dbfile):
    """A draft order for a machine and a part, on a real account."""
    from src import db

    with db.connect() as c:
        account = c.execute(
            "SELECT id FROM accounts WHERE id IN (SELECT account_id FROM sites) "
            "LIMIT 1").fetchone()["id"]

    with db.txn() as c:
        c.execute("""INSERT INTO purchase_orders
                     (id,account_id,status,subtotal,placed_at)
                     VALUES ('PO-AS',?,'draft',6688.0,'2026-08-27T09:00:00')""",
                  (account,))
        c.execute("""INSERT INTO purchase_lines
                     (po_id,line_no,description,qty,unit_price)
                     VALUES ('PO-AS',1,'Traulsen G12010 reach-in freezer',1,6599.0)""")
        c.execute("""INSERT INTO purchase_lines
                     (po_id,line_no,description,qty,unit_price)
                     VALUES ('PO-AS',2,'Door gasket',1,89.0)""")
    return "PO-AS"


# The sale records the sale.


def test_confirming_puts_it_on_their_account(an_order):
    from src import buying, db

    out = buying.confirm_purchase_order(an_order, agreed_by="Arjun Raman")
    assert out["cover_is_ours"] is True
    assert out["now_theirs"], "the sale did not record that they own it"

    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM assets "
                      "WHERE installed_source='sold_by_us'").fetchone()["n"]
    assert n >= 1


def test_the_cover_is_ours_not_a_claim(an_order):
    """The distinction standing.py is built on, which had no source of truth."""
    from src import buying, standing

    out = buying.confirm_purchase_order(an_order)
    asset = out["now_theirs"][0]["asset_id"]

    p = standing.date_provenance(asset)
    assert p["proven"] is True
    assert p["source"] == "sold_by_us"
    assert "we sold and installed this machine" in p["why"]


def test_a_gasket_is_not_a_machine(an_order):
    """Registering a part as a machine puts something on their asset register
    that cannot fail and cannot be serviced, and confuses every later call."""
    from src import buying

    out = buying.confirm_purchase_order(an_order)
    descriptions = [a["description"] for a in out["now_theirs"]]
    assert not any("gasket" in d.lower() for d in descriptions)
    assert any("Traulsen" in d for d in descriptions)


def test_cover_starts_on_delivery_not_on_the_phone_call(an_order):
    """Using the order date shortens their warranty by exactly the lead time,
    and does it in our favour, which nobody notices until a claim."""
    from datetime import date

    from src import buying

    out = buying.confirm_purchase_order(an_order)
    starts = out["now_theirs"][0]["cover_starts"]
    assert starts >= date.today().isoformat()


# Delivery closes it, and corrects the date.


def test_the_carrier_moves_the_warranty_to_the_truth(an_order):
    from src import buying, db, delivery

    buying.confirm_purchase_order(an_order)
    out = delivery.carrier_delivered(an_order, delivered_on="2026-09-25",
                                     carrier="UPS", carrier_ref="1Z999")

    assert out["ok"] is True
    assert out["cover_corrected"], "the promised date was left in place"

    with db.connect() as c:
        row = c.execute("SELECT installed_on FROM assets "
                        "WHERE installed_source='sold_by_us' "
                        "ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["installed_on"] == "2026-09-25"


def test_a_resent_webhook_does_not_deliver_it_twice(an_order):
    """Carriers resend. A second one must not produce a second call, or a
    second correction to the date."""
    from src import buying, db, delivery

    buying.confirm_purchase_order(an_order)
    delivery.carrier_delivered(an_order, delivered_on="2026-09-25")
    again = delivery.carrier_delivered(an_order, delivered_on="2026-09-25")

    assert again["already"] is True
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM deliveries WHERE po_id=?",
                      (an_order,)).fetchone()["n"]
    assert n == 1


def test_an_order_is_finished_when_they_say_so(an_order):
    from src import buying, db, delivery

    buying.confirm_purchase_order(an_order)
    delivery.carrier_delivered(an_order)
    out = delivery.close_order(an_order, confirmed_by="Arjun", condition="ok")

    assert out["closed"] is True
    with db.connect() as c:
        d = c.execute("SELECT checked_in_at, confirmed_by, condition "
                      "FROM deliveries WHERE po_id=?", (an_order,)).fetchone()
    assert d["checked_in_at"], "nobody recorded that they confirmed it"
    assert d["confirmed_by"] == "Arjun"
    assert d["condition"] == "ok"


def test_a_confirmed_order_stops_showing_as_outstanding(an_order):
    """The list somebody works through: delivered, and never heard about."""
    from src import buying, delivery

    buying.confirm_purchase_order(an_order)
    delivery.carrier_delivered(an_order)
    assert any(o["id"] == an_order for o in delivery.open_orders()["orders"])

    delivery.close_order(an_order, confirmed_by="Arjun")
    assert not any(o["id"] == an_order for o in delivery.open_orders()["orders"])


def test_damaged_does_not_close_and_does_not_argue(an_order):
    from src import buying, delivery

    buying.confirm_purchase_order(an_order)
    delivery.carrier_delivered(an_order)
    out = delivery.close_order(an_order, condition="damaged",
                               note="dented on the door")

    assert out["closed"] is False
    assert "do not argue about it on the phone" in out["say"]


def test_nothing_is_closed_that_the_carrier_never_reported(an_order):
    from src import buying, delivery

    buying.confirm_purchase_order(an_order)
    out = delivery.close_order(an_order)
    assert out["ok"] is False


# Cover offered honestly, including when the answer is no.


def test_it_refuses_to_sell_cover_on_a_twelve_year_chair(dbfile):
    """Herman Miller covers a chair for twelve years including labour. Selling
    three more is selling nothing, and saying so is worth more than the sale."""
    from scripts.add_vendors import load

    from src.aftercare import warranty_options

    load()
    out = warranty_options("Herman Miller", "Aeron", 1400.0, "office chair")
    assert out["recommend"] is False
    assert "NOT to buy it" in out["say"]


def test_it_refuses_above_the_published_threshold(dbfile):
    """The consumer advice is consistent: decline anything above twenty per
    cent of retail, because the premium outruns the expected repair."""
    from scripts.add_vendors import load

    from src.aftercare import warranty_options

    load()
    out = warranty_options("Samsung", "QN65 television", 900.0, "television")
    assert out["recommend"] is False
    assert out["share_of_price"] > 0.20


def test_it_offers_on_a_machine_a_kitchen_cannot_trade_without(dbfile):
    from scripts.load_warranties import load

    from src.aftercare import warranty_options

    load()
    out = warranty_options("Traulsen", "G12010", 6599.0, "reach-in freezer")
    assert out["recommend"] is True
    assert "18,000" in out["say"], "the honest reason is downtime, not repairs"


def test_it_will_not_extend_terms_it_cannot_read(dbfile):
    """It cannot quote the maker's term, so it must not add years to it.

    It offers a plan of our own instead, which starts on delivery and does
    not depend on knowing what the maker gives. The thing being guarded is
    that the answer is never an EXTENSION of an unknown quantity.
    """
    from src.aftercare import warranty_options

    out = warranty_options("Nobody", "X1", 500.0, "laptop")
    assert "extra_years" not in out
    assert out.get("standard_terms_on_file") is False
    assert (out.get("our_own_cover") or {}).get("ours") is True


# The work is judged, and a disagreement is handled.


@pytest.fixture
def a_finished_job(dbfile):
    """A job that was attended and closed, with a named technician on it.

    Built rather than looked for. Depending on the fixture happening to hold a
    completed visit meant every test below skipped silently, which is the same
    as not having written them.
    """
    from src import db

    with db.connect() as c:
        acct = c.execute("SELECT id FROM accounts LIMIT 1").fetchone()["id"]
        site = c.execute("SELECT id FROM sites WHERE account_id=? LIMIT 1",
                         (acct,)).fetchone()
        asset = c.execute("SELECT id FROM assets LIMIT 1").fetchone()
        techs = c.execute("SELECT id FROM technicians ORDER BY id").fetchall()

    if site is None or asset is None or len(techs) < 2:
        pytest.skip("the fixture needs a site, an asset and two technicians")

    with db.txn() as c:
        c.execute("""INSERT INTO work_orders
                     (id,account_id,site_id,asset_id,reported_symptom,status,
                      opened_at,dealer_id)
                     VALUES ('WO-AS',?,?,?,'not holding temperature','closed',
                             '2026-08-20T09:00:00','D-REF')""",
                  (acct, site["id"], asset["id"]))
        c.execute("""INSERT INTO visits
                     (id,work_order_id,seq,technician_id,completed_at,outcome)
                     VALUES ('V-AS','WO-AS',1,?,'2026-08-21T14:00:00','fixed')""",
                  (techs[0]["id"],))
    return "WO-AS"


def test_the_answer_is_attached_to_whoever_did_the_work(a_finished_job):
    from src import db, recovery

    out = recovery.record_workmanship(a_finished_job, still_working=True,
                                      on_time=True, customer_said="all good")
    assert out["ok"] is True
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM workmanship").fetchone()["n"] == 1


def test_a_fix_that_did_not_hold_is_not_left_to_wait_and_see(a_finished_job):
    from src import recovery

    out = recovery.record_workmanship(a_finished_job, still_working=False)
    assert "Do NOT ask them to wait and see" in out["say"]


@pytest.mark.parametrize("said,kind,severity", [
    ("the freezer is still warm and we lost all our stock", "outcome", "severe"),
    ("it is still not working, same problem", "outcome", "normal"),
    ("he turned up two hours late", "process", "normal"),
])
def test_an_outcome_failure_is_not_a_process_failure(dbfile, said, kind, severity):
    """The published work is unambiguous that these need different responses.
    Treating "the technician was late" and "my freezer is still warm and I have
    lost a service" the same insults one customer and overpays the other."""
    from src import recovery

    assert recovery._classify(said) == (kind, severity)
    assert (recovery.MAKE_GOOD[("outcome", "severe")]
            > recovery.MAKE_GOOD[("process", "normal")])


def test_a_dispute_sends_somebody_else(a_finished_job):
    """Sending the same person back to a customer who has just complained
    about them is the one option guaranteed to make it worse."""
    from src import db, recovery

    with db.connect() as c:
        was = c.execute("SELECT technician_id FROM visits "
                        "WHERE work_order_id=? ORDER BY seq DESC LIMIT 1",
                        (a_finished_job,)).fetchone()["technician_id"]

    out = recovery.raise_dispute(a_finished_job, "it is still not working",
                                 "it was holding when I left")
    assert out["ok"] is True
    if out["reassigned_to"]:
        assert out["reassigned_to"]["technician_id"] != was


def test_it_does_not_argue_about_who_was_right(a_finished_job):
    from src import recovery

    out = recovery.raise_dispute(a_finished_job, "still broken",
                                 "it was working when I left")
    assert "there is nothing to win" in out["say"]
    assert "ask THEM what time suits" in out["say"]


def test_what_we_gave_them_is_written_down(a_finished_job):
    """A make-good nobody recorded is one the next person cannot see, and
    being offered the same apology twice is worse than not being offered one."""
    from src import recovery

    d = recovery.raise_dispute(a_finished_job, "still broken")["dispute"]
    out = recovery.settle_dispute(d, made_good="revisit free", value=195.0)
    assert out["ok"] is True
    assert "will not offer it again" in out["say"]
