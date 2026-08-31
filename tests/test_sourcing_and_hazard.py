"""Asking suppliers rather than guessing, and reading complaints for danger.

TWO THINGS THAT WERE DECIDED BY CONSTANTS

    supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()
    LEAD_DAYS = {"part": 3, "specialised": 15, "machine": 21}

Sourcing picked whichever supplier came first in the table and read the date
out of a dict. Four suppliers were on file, three had phone numbers, and none
was ever asked anything. On a live call a customer was told "about 21 days" by
a lookup, attributed to a company nobody had contacted.

And complaints were read one at a time, by a person, or not at all. Nobody
ever asked whether the same model was drawing the same frightening report
from several different customers, which is the only question a dealer can
answer that no manufacturer and no regulator can.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def books(dbfile):
    """Suppliers, and a catalogue each, so asking them is a real question.

    The fixture database holds no suppliers at all, which is itself the point:
    sourcing fell back to `SELECT id FROM suppliers LIMIT 1` precisely because
    nobody had ever given a supplier anything to say.
    """
    from scripts.seed_supplier_books import load

    from src import db

    with db.txn() as c:
        for sid, name in (("SUP-1", "Midway Parts Co"),
                          ("SUP-2", "Encompass Supply"),
                          ("SUP-3", "Great River Refrigeration Supply")):
            c.execute("INSERT OR REPLACE INTO suppliers (id,name,dealer_id) "
                      "VALUES (?,?,'D-REF')", (sid, name))
    return load()


# The fixture carries these, and no condenser fan.
PART = "Evaporator fan motor"


@pytest.fixture
def a_model_many_people_own(dbfile):
    """One model, on four different customers' sites.

    Built rather than looked for. The pattern detector needs separate
    HOUSEHOLDS by design -- one person reporting a burning smell is an
    incident -- so a fixture with a single account cannot exercise it, and
    the tests below were silently skipping instead of running.
    """
    from src import db

    made = []
    with db.txn() as c:
        for i in range(4):
            acct, site = f"A-HAZ{i}", f"S-HAZ{i}"
            c.execute("INSERT OR REPLACE INTO accounts (id,kind,name,dealer_id) "
                      "VALUES (?,'business',?, 'D-REF')",
                      (acct, f"Hazard Test Cafe {i}"))
            c.execute("INSERT OR REPLACE INTO sites (id,account_id,label) "
                      "VALUES (?,?,?)", (site, acct, "Kitchen"))
            c.execute(
                """INSERT OR REPLACE INTO assets
                   (id,site_id,manufacturer,model_number,family)
                   VALUES (?,?,'Beverage-Air','HR1HC***G********',
                           'display cooler')""",
                (f"AST-HAZ{i}", site))
            made.append(acct)
    return made



# What a supplier is asked, and what is done with the answers.


def test_every_supplier_who_carries_it_is_asked(books):
    from src import sourcing

    out = sourcing.ask_suppliers(PART)
    assert out["ok"] is True
    assert out["asked"] >= 2, "only one supplier was consulted"
    assert all(r["unit_price"] for r in out["replies"])


def test_the_date_comes_from_a_supplier_not_a_constant(books):
    """LEAD_DAYS said 3 for any part and 21 for any machine, whoever it was."""
    from src import backorder, sourcing

    out = sourcing.ask_suppliers(PART)
    quoted = {r["lead_time_days"] for r in out["replies"]}
    assert len(quoted) > 1, "every supplier quoted the same, so it is a constant"
    assert quoted != set(backorder.LEAD_DAYS.values())


def test_when_they_are_down_it_takes_the_soonest(books):
    """A restaurant with a dead walk-in loses stock every day it waits.
    Saving twenty dollars and waiting six more days is not thrift."""
    from src import sourcing

    out = sourcing.ask_suppliers(PART, urgent=True)
    soonest = min(out["replies"], key=lambda r: r["lead_time_days"])
    assert out["chosen"]["supplier_id"] == soonest["supplier_id"]


def test_it_says_which_trade_off_it_made(books):
    from src import sourcing

    out = sourcing.ask_suppliers(PART)
    assert out["because"], "a choice with no stated reason is a guess"
    assert any(w in out["because"] for w in ("cheapest", "soonest", "same"))


def test_the_supplier_is_never_named_to_the_caller(books):
    """Which of ours fills the order is our arrangement, not theirs."""
    from src import sourcing

    out = sourcing.ask_suppliers(PART)
    assert "Do NOT name the supplier" in out["say"]
    for r in out["replies"]:
        assert r["supplier"] not in out["say"]


def test_it_refuses_to_invent_a_date_for_something_nobody_carries(books):
    from src import sourcing

    out = sourcing.ask_suppliers("a flux capacitor")
    assert out["ok"] is False
    assert "Do not invent a lead time" in out["say"]


def test_who_said_what_is_kept(books):
    """A promise another company made, that our customer is invoiced
    against, has to be checkable later."""
    from src import sourcing

    sourcing.ask_suppliers(PART)
    log = sourcing.what_we_asked()
    assert log["asked"] >= 1
    assert log["requests"][0]["replies"], "nobody's answer was kept"
    assert log["requests"][0]["chosen"]


def test_a_capacitor_is_not_a_compressor(books):
    """The trap backorder.py already documents, which the first version of
    the supplier seed fell into: matching the word "compressor" put a 42 day
    OEM lead time on a fifty dollar shelf part."""
    from scripts.seed_supplier_books import _tier

    assert _tier("Compressor start capacitor") == "part"
    assert _tier("Compressor overload relay") == "part"
    assert _tier("Electronic control board") == "specialised"


def test_a_supplier_quotes_only_its_own_vendors_parts(books, dbfile):
    """Refrigeration suppliers were quoting on laptop batteries."""
    from src import db

    with db.connect() as c:
        wrong = c.execute(
            """SELECT COUNT(*) n FROM supplier_catalogue sc
               JOIN suppliers s ON s.id = sc.supplier_id
               JOIN parts p ON p.sku = sc.sku
               WHERE p.dealer_id IS NOT NULL AND s.dealer_id IS NOT NULL
                 AND p.dealer_id <> s.dealer_id""").fetchone()["n"]
    assert wrong == 0


# Reading complaints for danger.


@pytest.mark.parametrize("said,expected", [
    ("the door seal is a bit loose", "nuisance"),
    ("it is not holding temperature and icing up again", "degraded"),
    ("it keeps tripping the breaker", "unsafe"),
    ("there is a burning smell and the back panel is hot to touch", "dangerous"),
])
def test_a_complaint_is_read_for_danger(dbfile, said, expected):
    from src.hazard import classify

    assert classify(said)["level"] == expected


def test_the_same_words_read_worse_on_a_flammable_machine(dbfile):
    """The desk already refuses to send a technician without the
    certification to open a flammable circuit. It has never used the same
    fact to protect the person standing next to it.

    "I can smell gas" is unsafe on most equipment. On a machine holding
    propane it is not.
    """
    from src.hazard import DANGEROUS, classify

    said = "I can smell gas near it"
    plain = classify(said, "Traulsen", "G12010")
    propane = classify(said, "Beverage-Air", "HR1HC***G********")

    if propane["flammable_charge"]:
        assert propane["level"] == DANGEROUS
        assert propane["raised_for_refrigerant"] is True
        assert plain["level"] != DANGEROUS


def test_one_report_is_an_incident_and_two_are_a_pattern(dbfile):
    """One person saying their freezer smells hot is an incident. Two, on the
    same model, from different customers, is the thing a regulator would want
    to have been told about."""
    from src.hazard import PATTERN_AT

    assert PATTERN_AT >= 2


def test_a_pattern_fires_only_across_separate_customers(a_model_many_people_own):
    from scripts.seed_hazard_reports import load

    from src.hazard import sweep_hazards

    out = load()
    assert out.get("ok"), out.get("why")

    swept = sweep_hazards("D-REF")
    assert swept["patterns"], "three dangerous reports raised no pattern"
    p = swept["patterns"][0]
    assert p["households"] >= 2
    assert p["dangerous_reports"] >= 2


def test_the_safety_instruction_changes_for_a_flammable_charge(a_model_many_people_own):
    """On a propane machine the answer is NOT "unplug it": a spark is the
    thing to avoid."""
    from scripts.seed_hazard_reports import load

    from src.hazard import stop_using_it, sweep_hazards

    out = load()
    assert out.get("ok"), out.get("why")

    swept = sweep_hazards("D-REF")
    p = swept["patterns"][0]
    say = stop_using_it(p)["say"]

    assert "not a sales call" in say
    if p["flammable_charge"]:
        assert "do not unplug it" in say
        assert "flammable refrigerant" in say


def test_it_does_not_ask_them_to_investigate_it_themselves(a_model_many_people_own):
    from scripts.seed_hazard_reports import load

    from src.hazard import stop_using_it, sweep_hazards

    out = load()
    assert out.get("ok"), out.get("why")

    say = stop_using_it(sweep_hazards("D-REF")["patterns"][0])["say"]
    assert "Do NOT diagnose it for them" in say
    assert "instruction comes before the conversation" in say


def test_the_customer_never_hears_what_a_supplier_charges_us(dbfile):
    """FOUND ON A LIVE WHATSAPP CONVERSATION.

    Two columns, similarly named, meaning opposite things:

        parts.unit_cost                92.00   what we CHARGE
        supplier_catalogue.unit_price  84.64   what we PAY Midway

    The sentence handed to the desk was built from the supplier's price, so a
    real customer was quoted a door gasket at $84.64 and then offered $84.64
    again as "a lower-cost option". That sells the part below our own price
    before any labour, and it shows a stranger what our suppliers charge us.

    The module already refused to name the supplier and refused to shorten the
    delivery date. It gave away the number those protections exist for.
    """
    from src import db, sourcing

    with db.connect() as c:
        part = c.execute(
            "SELECT sku, unit_cost FROM parts WHERE dealer_id='D-REF' "
            "AND unit_cost IS NOT NULL LIMIT 1").fetchone()

    assert part is not None, "the fixture book must hold a priced part"

    # Build the supplier rather than skipping without one. A test that
    # quietly skips is a test that protects nothing, and this one guards a
    # margin bug that reached a real customer.
    with db.txn() as c:
        c.execute("""INSERT INTO suppliers (id,name)
                     VALUES ('SUP-T','Test Supply Co')
                     ON CONFLICT(id) DO NOTHING""")
    supplier = {"id": "SUP-T"}

    ours = float(part["unit_cost"])
    theirs = round(ours * 0.6, 2)          # a wholesale price, clearly lower

    with db.txn() as c:
        c.execute(
            """INSERT INTO supplier_catalogue
                 (supplier_id,sku,their_ref,unit_price,lead_time_days,
                  on_hand,min_order_qty,updated_at)
               VALUES (?,?,?,?,?,?,?,'2026-08-30')
               ON CONFLICT(supplier_id,sku) DO UPDATE SET
                 unit_price=excluded.unit_price""",
            (supplier["id"], part["sku"], "REF", theirs, 4, 3, 1))

    out = sourcing.ask_suppliers(part["sku"])
    assert out.get("ok"), f"sourcing declined: {out.get('why')}"

    said = out["say"]
    assert f"{ours:,.2f}" in said, "our own price must be the one quoted"
    assert f"{theirs:,.2f}" not in said, (
        "the supplier's price reached the sentence the desk reads aloud")
    assert "NEVER quote what a supplier charges us" in said
