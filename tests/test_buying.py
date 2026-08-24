"""Advising somebody who is about to spend thousands, and taking the order.

The bug that produced most of this file: `recommend_equipment` returned the
verdict "recommended" for a machine on the strength of ONE install that had not
broken yet. A sample of one wearing a confident sentence. Worse, the ranking
sorted by raw fault rate, so a model we knew nothing about outranked one we had
genuinely proven over forty units, because zero-over-one beats point-one-over-
forty.

Both halves are asserted here: that tiny samples cannot win, and that the desk
says so out loud instead of guessing.
"""

from __future__ import annotations

from conftest import REF


def _install(db, model, units, dealer=REF, family="reach-in freezer",
             mfr="Traulsen", site="S-1"):
    """Put N of a model into service, so a denominator exists."""
    with db.txn() as c:
        for i in range(units):
            c.execute(
                """INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                   VALUES (?,?,?,?,?)""",
                (f"AS-{model}-{i}", site, mfr, model, family))
        c.execute(
            """INSERT OR IGNORE INTO equipment
               (source,dataset,category,brand,model_number,product_type,daily_kwh)
               VALUES ('energystar','d','refrigeration',?,?,
                       'Vertical Solid Door Freezer', 2.0)""",
            (mfr, model))


def _complain(db, model, n, mfr="Traulsen", severity="minor", dealer=REF):
    from datetime import datetime

    with db.txn() as c:
        for i in range(n):
            c.execute(
                """INSERT INTO complaints
                   (id,dealer_id,manufacturer,model_number,what,category,
                    severity,raised_at,status)
                   VALUES (?,?,?,?,?,?,?,?,'open')""",
                (f"CMP-{model}-{i}", dealer, mfr, model,
                 "it is deafening", "noise", severity,
                 datetime.now().isoformat(timespec="seconds")))


def test_one_install_is_never_a_recommendation(dbfile):
    """The bug. One unit, no faults, must not come back as recommended."""
    from src import db, ops

    _install(db, "LONELY-1", 1)
    r = ops.recommend_equipment("reach-in freezer", limit=20)

    for c in r["candidates"]:
        if c["model"] == "LONELY-1":
            assert c["verdict"] == "too few to judge", c
            assert "too few" in c["our_experience"]
            break
    else:
        raise AssertionError("the model under test was not returned at all")


def test_a_proven_model_outranks_an_unknown_one(dbfile):
    """Zero-over-one must not beat a low rate over a real sample.

    This is the ordering half of the bug. Without smoothing, a machine with one
    clean install sorts above one with forty installs and two faults, so the
    desk recommends the thing it knows least about.
    """
    from src import db, ops

    _install(db, "PROVEN-40", 40)
    _install(db, "UNKNOWN-1", 1)

    with db.txn() as c:
        for i in range(2):
            c.execute(
                """INSERT INTO repairs
                   (id,dealer_id,asset_id,manufacturer,model_number,
                    found_cause,closed_on)
                   VALUES (?,?,?,?,?,?,?)""",
                (f"R-P{i}", REF, f"AS-PROVEN-40-{i}", "Traulsen", "PROVEN-40",
                 "door gasket perished", "2026-05-01"))

    r = ops.recommend_equipment("reach-in freezer", limit=20)
    order = [c["model"] for c in r["candidates"]]
    assert "PROVEN-40" in order and "UNKNOWN-1" in order
    assert order.index("PROVEN-40") < order.index("UNKNOWN-1"), \
        "the machine we know nothing about outranked the one we proved"


def test_a_clean_record_over_a_real_sample_is_recommended(dbfile):
    from src import db, ops

    _install(db, "GOOD-12", 12)
    r = ops.recommend_equipment("reach-in freezer", limit=20)
    got = next(c for c in r["candidates"] if c["model"] == "GOOD-12")
    assert got["verdict"] == "recommended"
    assert got["units_in_service"] == 12


def test_complaints_count_against_a_model(dbfile):
    """A machine nobody calls out for, that everybody grumbles about.

    Service calls only capture what breaks badly enough to send a van. If
    complaints were ignored, a model that is merely awful to own would look
    identical to one that is genuinely good.
    """
    from src import db, ops

    _install(db, "NOISY-10", 10)
    _complain(db, "NOISY-10", 6)

    r = ops.recommend_equipment("reach-in freezer", limit=20)
    got = next(c for c in r["candidates"] if c["model"] == "NOISY-10")
    assert got["complaints"] == 6
    assert got["verdict"] != "recommended", \
        "six complaints across ten machines still came back as recommended"


def test_an_unusable_severity_forces_avoid(dbfile):
    """One customer saying the machine is unusable outweighs a tidy average."""
    from src import db, ops

    _install(db, "BAD-10", 10)
    _complain(db, "BAD-10", 1, severity="unusable")

    r = ops.recommend_equipment("reach-in freezer", limit=20)
    got = next(c for c in r["candidates"] if c["model"] == "BAD-10")
    assert got["verdict"] == "avoid"


def test_what_we_know_about_sees_complaints_without_a_service_call(dbfile):
    """The other half of the same blindness.

    This used to return "we have never seen it" whenever there were no service
    calls, even with a stack of complaints on file. Reporting the absence of
    one signal as the absence of all of them.
    """
    from src import db, ops

    _install(db, "GRUMBLE-8", 8)
    _complain(db, "GRUMBLE-8", 4)

    r = ops.what_we_know_about("Traulsen", "GRUMBLE-8")
    assert r["known"] is True
    assert r["complaints"] == 4
    assert r["in_their_words"]


def test_a_complaint_is_recorded_in_the_customers_words(dbfile):
    from src import db, ops

    r = ops.register_complaint(
        "Traulsen", "the door seal perished inside a year",
        model_number="G12010", category="design", severity="major",
        dealer_id=REF)
    assert r["ok"]

    with db.connect() as c:
        row = c.execute("SELECT * FROM complaints WHERE id=?",
                        (r["complaint_id"],)).fetchone()
    assert row["what"] == "the door seal perished inside a year"
    assert row["severity"] == "major"


def test_a_complaint_needs_a_make_and_words(dbfile):
    from src import ops

    assert not ops.register_complaint("", "something")["ok"]
    assert not ops.register_complaint("Traulsen", "  ")["ok"]


def test_a_complaint_outlives_its_machine(dbfile):
    """Make and model are stored outright, not only through the asset.

    A customer complaining about a machine is quite likely to replace it. If
    the complaint hung off the asset row alone, the evidence would vanish at
    exactly the moment it was proven right.
    """
    from src import db, ops

    _install(db, "DOOMED-5", 5)
    ops.register_complaint("Traulsen", "never held temperature",
                           model_number="DOOMED-5", asset_id="AS-DOOMED-5-0",
                           severity="unusable", dealer_id=REF)

    with db.txn() as c:
        c.execute("UPDATE assets SET retired_on='2026-08-01' WHERE id=?",
                  ("AS-DOOMED-5-0",))

    still = ops.complaints_about("Traulsen", "DOOMED-5")
    assert still["complaints"] == 1


# --------------------------------------------------------------------------
# taking the order
# --------------------------------------------------------------------------

def test_an_order_starts_as_a_draft(dbfile):
    from src import db, ops

    po = ops.create_purchase_order("A-1", ["P-DEFROSTTHE"], "S-1")
    assert po["ok"]
    with db.connect() as c:
        status = c.execute("SELECT status FROM purchase_orders WHERE id=?",
                           (po["purchase_order"],)).fetchone()["status"]
    assert status != "placed"


def test_confirming_places_it(dbfile):
    """The step that did not exist. Every order ever taken sat as a draft.

    "confirmed" rather than "placed" because that is the vocabulary the schema
    already enforces. A first version of this wrote "placed" and the CHECK
    constraint rejected it, which is the constraint doing its job.
    """
    from src import db, ops

    po = ops.create_purchase_order("A-1", ["P-DEFROSTTHE"], "S-1")
    r = ops.confirm_purchase_order(po["purchase_order"], agreed_by="Maria")
    assert r["ok"] and r["status"] == "confirmed"
    assert r["lines"]

    with db.connect() as c:
        row = c.execute("SELECT status, confirmed_at FROM purchase_orders WHERE id=?",
                        (po["purchase_order"],)).fetchone()
    assert row["status"] == "confirmed"
    assert row["confirmed_at"]


def test_confirming_twice_does_not_duplicate(dbfile):
    """Saying yes twice on a phone call is ordinary and must be harmless."""
    from src import db, ops

    po = ops.create_purchase_order("A-1", ["P-DEFROSTTHE"], "S-1")
    ops.confirm_purchase_order(po["purchase_order"])
    again = ops.confirm_purchase_order(po["purchase_order"])

    assert again["ok"]
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
    assert n == 1


def test_confirming_an_unknown_order_is_refused(dbfile):
    from src import ops

    assert not ops.confirm_purchase_order("PO-NOPE")["ok"]


def test_supplier_quotes_are_read_back(dbfile):
    """Vendor offers were written down and never read by anything."""
    from src import db, ops, tools

    class Ctx:
        def __init__(self):
            self.state = {"dealer_id": REF, "caller": {}}

    tools.log_supplier_offer("Midway Parts", "Dana",
                             "P-DEFROSTTHE at 54 dollars, three days", Ctx())

    r = ops.supplier_options("P-DEFROSTTHE")
    assert r["quotes"], "a logged supplier offer was invisible"
    assert r["quotes"][0]["supplier"]
    assert "not shipped it" in r["say"]


def test_no_quote_falls_back_to_the_catalogue(dbfile):
    from src import ops

    r = ops.supplier_options("P-EVAPFAN")
    assert r["quotes"] == []
    assert "catalogue lead time" in r["say"]


# --------------------------------------------------------------------------
# federal safety recalls, which we already held and never read
# --------------------------------------------------------------------------

def _recalls(db, rows):
    with db.txn() as c:
        for i, (brands, title, hazard) in enumerate(rows):
            c.execute(
                """INSERT INTO recalls
                   (recall_number,recall_date,title,hazard,brands,url)
                   VALUES (?,?,?,?,?,?)""",
                (f"R-{i}", "2026-07-30", title, hazard, brands,
                 "https://cpsc.gov/x"))


def test_a_recalled_machine_is_never_recommended(dbfile):
    """The defect this closes.

    324 federal recalls were loaded from day one and only the service side ever
    read them, so the buying side could recommend a machine the government had
    recalled. A clean fault record on a recalled machine is not reassurance, it
    only means the hazard has not reached our customers yet.
    """
    from src import db, ops

    _install(db, "ZAPPY-9", 9, mfr="Galanz")
    _recalls(db, [("Galanz Retro Refrigerators",
                   "Galanz Americas Recalls Retro Refrigerators",
                   "internal electrical components can overheat")])

    r = ops.recommend_equipment("reach-in freezer", limit=20)
    got = next(c for c in r["candidates"] if c["model"] == "ZAPPY-9")
    assert got["recalled"] is True
    assert got["verdict"] == "recalled, do not recommend"
    assert "RECALL" in got["our_experience"]


def test_recalled_machines_sort_last(dbfile):
    """Returned so the agent can warn, never at the head of a suggestion list."""
    from src import db, ops

    _install(db, "FINE-10", 10)
    _install(db, "ZAPPY-9", 9, mfr="Galanz")
    _recalls(db, [("Galanz Retro Refrigerators", "Galanz Recalls Refrigerators",
                   "overheat")])

    r = ops.recommend_equipment("reach-in freezer", limit=20)
    models = [c["model"] for c in r["candidates"]]
    assert models.index("FINE-10") < models.index("ZAPPY-9")


def test_a_brand_name_inside_another_word_is_not_a_recall(dbfile):
    """BUNN matched "Woven Bunny Baskets" on a substring.

    A false recall warning tells a customer not to buy something that is
    perfectly fine, which is the same class of error as inventing a fault.
    """
    from src import db, ops

    _install(db, "COFFEE-8", 8, mfr="BUNN", family="ice machine")
    _recalls(db, [("H for Happy Woven Bunny Baskets",
                   "Bed Bath and Beyond Recalls Woven Bunny Baskets",
                   "choking hazard")])

    r = ops.recommend_equipment("ice machine", limit=20)
    got = next((c for c in r["candidates"] if c["model"] == "COFFEE-8"), None)
    if got:
        assert got["recalled"] is False, "matched a brand inside another word"


def test_an_accessory_recall_does_not_condemn_the_machine(dbfile):
    """A recalled power bank is not a recalled laptop.

    Matching on brand alone flagged Dell over battery modules and Lenovo over
    power banks. Both titles contain the word "laptop". Neither is a reason to
    steer somebody away from the machine.
    """
    from src import db, ops

    _install(db, "THINK-10", 10, mfr="Lenovo", family="laptop")
    _recalls(db, [("Lenovo USB-C Laptop Power Banks",
                   "Lenovo Recalls USB-C Laptop Power Banks",
                   "fire hazard")])

    r = ops.recommend_equipment("laptop", limit=20)
    got = next((c for c in r["candidates"] if c["model"] == "THINK-10"), None)
    if got:
        assert got["recalled"] is False
        assert got["verdict"] != "recalled, do not recommend"


def test_a_recall_of_a_different_product_is_ignored(dbfile):
    """Right company, wrong machine. Their toaster is not our freezer."""
    from src import db, ops

    _install(db, "COLD-8", 8, mfr="Panasonic")
    _recalls(db, [("Panasonic Electric Toaster Ovens",
                   "Panasonic Recalls Electric Toaster Ovens",
                   "shock hazard")])

    r = ops.recommend_equipment("reach-in freezer", limit=20)
    got = next((c for c in r["candidates"] if c["model"] == "COLD-8"), None)
    if got:
        assert got["recalled"] is False
