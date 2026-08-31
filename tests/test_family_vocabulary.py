"""The catalogue and the trade do not call things the same name.

    catalogue: "Vertical Solid Door Refrigerator"
    trade:     "reach-in cooler"

Technician skills, NEEDS_CERT and the repair corpus are all keyed on the trade
word. `_family_for` was written to stop a NULL family reaching the scheduler,
and it asked the certification catalogue first, so it wrote the catalogue's
word onto a real customer's machine in the middle of a call. The scheduler
then answered:

    'nobody is qualified on Vertical Solid Door Refrigerator'

and escalated to the branch manager a job that eight certified technicians
could have taken that afternoon.

A wrong family is worse than the null it replaced. A null is visibly missing.
This looked like an answer.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("catalogue,trade", [
    ("Vertical Solid Door Refrigerator", "reach-in cooler"),
    ("Vertical Solid Door Freezer", "reach-in freezer"),
    ("Vertical Transparent Door Refrigerator", "display cooler"),
    ("Vertical Transparent Door Freezer", "display cooler"),
    ("Horizontal Solid Door Freezer", "reach-in freezer"),
    ("Chef Base Refrigerator", "reach-in cooler"),
    ("Ice Making Head", "ice machine"),
    ("Self Contained Unit", "ice machine"),
    ("Service Over Counter", "display cooler"),
])
def test_the_catalogue_word_becomes_the_trade_word(dbfile, catalogue, trade):
    from src import caller

    assert caller._trade_word(catalogue) == trade


def test_an_unmapped_type_records_nothing_rather_than_guessing(dbfile):
    """A family nobody is skilled on reads to a customer as "nobody here can
    fix your freezer"."""
    from src import caller

    assert caller._trade_word("Something Nobody Listed") == ""


def test_a_family_it_produces_is_one_somebody_is_certified_for(dbfile):
    """The whole point. Every trade word it can return must be a family the
    certification table actually knows about, or the scheduler refuses."""
    from src import caller, cover

    known = set(cover.NEEDS_CERT)
    for trade in set(caller.CATALOGUE_TO_TRADE.values()):
        assert trade in known, (
            f"{trade!r} is not in NEEDS_CERT, so nobody will ever be "
            "qualified for it")


def test_our_own_machines_are_asked_before_the_catalogue(dbfile):
    """They already speak the right language. Asking the catalogue first is
    what put the wrong word on a live customer's machine."""
    from src import caller, db

    with db.txn() as c:
        c.execute("""INSERT INTO equipment
                     (source,dataset,category,brand,model_number,product_type,
                      site_visit,model_norm)
                     VALUES ('energystar','t','refrigeration','Traulsen',
                             'RHT126WUT-FHS','Vertical Solid Door Refrigerator',
                             1,'RHT126WUTFHS')""")
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AST-KNOWN','S-1','Traulsen','RHT126WUT-FHS',
                             'reach-in freezer')""")

    assert caller._family_for("Traulsen", "RHT126WUT-FHS") == "reach-in freezer"


def test_the_catalogue_is_still_used_when_we_have_nothing(dbfile):
    from src import caller, db

    with db.txn() as c:
        c.execute("""INSERT INTO equipment
                     (source,dataset,category,brand,model_number,product_type,
                      site_visit,model_norm)
                     VALUES ('energystar','t','refrigeration','Hoshizaki',
                             'KM-901','Ice Making Head',1,'KM901')""")

    assert caller._family_for("Hoshizaki", "KM-901") == "ice machine"


def test_a_registered_machine_can_actually_be_serviced(dbfile):
    """End to end, which is the failure as the customer experienced it: the
    machine was registered and then nobody was qualified to touch it."""
    from scripts.seed_certs import load

    from src import caller, cover, db, trace

    load()
    with db.txn() as c:
        # The catalogue knows this machine by its certification name, which is
        # exactly the case that broke: the trade word has to come out.
        c.execute("""INSERT INTO equipment
                     (source,dataset,category,brand,model_number,product_type,
                      site_visit,model_norm)
                     VALUES ('energystar','t','refrigeration','Traulsen',
                             'RHT126WUT-FHS','Vertical Solid Door Refrigerator',
                             1,'RHT126WUTFHS')""")

    who = caller.resolve("+13095557777", "D-REF")
    with db.txn() as c:
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-F','+13095557777',?,'2026-08-27T16:16:00')",
                  (who["contact_id"],))
    trace.call_context("CALL-F")
    caller.confirm_details(name="Arjun Raman", site_label="Kitchen",
                           address="12 Adams St")
    out = caller.register_asset(manufacturer="Traulsen",
                                model_number="RHT126WUT-FHS")
    trace.call_context("")

    served = cover.can_we_serve(out["asset_id"])
    assert served["ok"] is True, served.get("why")
    assert served["qualified"] > 0
