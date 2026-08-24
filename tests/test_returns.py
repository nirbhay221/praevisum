"""Things coming back, which are two different facts wearing one word.

A PART coming back is an inventory event. Unopened, it belongs on the shelf,
and the reorder advice must know or it buys what is already in a box by the
door. A MACHINE coming back is evidence about that model, and stronger
evidence than a complaint: a complaint is annoyance, a return is somebody
deciding they would rather have nothing.

Counting them together would let a customer who miscounted an order make a
good machine look bad.
"""

from __future__ import annotations

from conftest import REF


def _free(db, sku="P-DEFROSTTHE"):
    with db.connect() as c:
        return c.execute(
            """SELECT COALESCE(SUM(s.free),0) FROM stock_available s
               JOIN stock_locations l ON l.id = s.location_id
               WHERE s.sku=? AND l.dealer_id=?""", (sku, REF)).fetchone()[0]


def test_an_unopened_part_goes_back_on_the_shelf(dbfile):
    from src import db, ops

    before = _free(db)
    r = ops.register_return("part", "ordered_wrong", sku="P-DEFROSTTHE",
                            account_id="A-1", qty=2, condition="unopened",
                            dealer_id=REF)
    assert r["ok"] and r["back_on_shelf"]
    assert _free(db) == before + 2


def test_a_used_part_does_not(dbfile):
    """Putting a fitted part back would have us promise something unfittable."""
    from src import db, ops

    before = _free(db)
    r = ops.register_return("part", "faulty", sku="P-DEFROSTTHE",
                            account_id="A-1", condition="used", dealer_id=REF)
    assert r["ok"] and not r["back_on_shelf"]
    assert _free(db) == before


def test_a_part_damaged_in_transit_does_not(dbfile):
    from src import db, ops

    before = _free(db)
    ops.register_return("part", "damaged_in_transit", sku="P-DEFROSTTHE",
                        account_id="A-1", condition="unopened", dealer_id=REF)
    assert _free(db) == before


def test_a_returned_part_reduces_what_we_reorder(dbfile):
    """The money this feature exists to stop wasting."""
    from src import db, ops

    from datetime import date, timedelta
    with db.txn() as c:
        for i in range(40):
            c.execute(
                """INSERT INTO repairs (id,dealer_id,asset_id,manufacturer,
                   model_number,found_cause,parts_consumed,closed_on)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (f"RR-{i}", REF, "AS-FREEZER", "Traulsen", "G12010",
                 "x", "P-DEFROSTTHE",
                 (date.today() - timedelta(days=3 * i + 1)).isoformat()))
        c.execute("UPDATE stock SET on_hand=2 WHERE sku='P-DEFROSTTHE'")

    before = next(x for x in ops.restock_advice(REF)["order"]
                  if x["sku"] == "P-DEFROSTTHE")["order_qty"]

    ops.register_return("part", "ordered_wrong", sku="P-DEFROSTTHE",
                        account_id="A-1", qty=5, condition="unopened",
                        dealer_id=REF)

    line = next((x for x in ops.restock_advice(REF)["order"]
                 if x["sku"] == "P-DEFROSTTHE"), None)
    after = line["order_qty"] if line else 0
    assert after < before, "reordered as if the returned stock did not exist"


def test_a_returned_machine_counts_against_the_model(dbfile):
    from src import db, ops

    with db.txn() as c:
        for i in range(8):
            c.execute(
                """INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                   VALUES (?,?,?,?,?)""",
                (f"AS-R{i}", "S-1", "Traulsen", "RETME", "reach-in freezer"))
        c.execute("""INSERT OR IGNORE INTO equipment
                     (source,dataset,category,brand,model_number,product_type,daily_kwh)
                     VALUES ('energystar','d','refrigeration','Traulsen','RETME',
                             'Vertical Solid Door Freezer', 2.0)""")

    for i in range(3):
        ops.register_return("machine", "faulty", said="never held temperature",
                            asset_id=f"AS-R{i}", account_id="A-1", dealer_id=REF)

    r = ops.recommend_equipment("reach-in freezer", limit=30)
    got = next(c for c in r["candidates"] if c["model"] == "RETME")
    assert got["returned"] == 3
    assert got["verdict"] == "avoid"


def test_a_customer_ordering_wrong_does_not_blame_the_machine(dbfile):
    """Someone miscounting must not make a good model look bad."""
    from src import db, ops

    with db.txn() as c:
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AS-OK','S-1','Traulsen','FINEME','reach-in freezer')""")

    ops.register_return("machine", "changed_mind", asset_id="AS-OK",
                        account_id="A-1", dealer_id=REF)

    r = ops.returns_about("Traulsen", "FINEME")
    assert r["returns"] == 1
    assert r["blamed_on_the_machine"] == 0
    assert "none of them blamed" in r["say"]


def test_returns_are_reported_with_the_denominator(dbfile):
    """Three out of forty is noise. Three out of four is a verdict."""
    from src import ops

    r = ops.returns_about("Traulsen", "G12010")
    assert "units_supplied" in r


def test_bad_input_is_refused(dbfile):
    from src import ops

    assert not ops.register_return("widget", "faulty", sku="X")["ok"]
    assert not ops.register_return("part", "because_i_said", sku="X")["ok"]
    assert not ops.register_return("part", "faulty")["ok"]
    assert not ops.register_return("machine", "faulty")["ok"]


def test_no_refund_is_ever_promised(dbfile):
    """The desk does not price a return down a phone line."""
    from src import ops

    r = ops.register_return("part", "faulty", sku="P-DEFROSTTHE",
                            account_id="A-1", dealer_id=REF)
    assert "Do not promise a refund amount" in r["told_caller"]


def test_the_phone_agent_can_take_a_return(dbfile):
    from src import agents

    names = {getattr(t, "__name__", getattr(t, "name", ""))
             for t in agents.front_agent.tools}
    assert "register_return" in names
