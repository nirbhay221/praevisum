"""A buying call must not walk into the service machinery.

WHAT HAPPENED, 26 AUGUST

Somebody rang to BUY a reach-in freezer. The desk classified it correctly:
set_intent({'intent': 'product'}). Then it took them all the way through the
breakdown flow anyway.

  register_asset      put the machine they were BUYING on their account as
                      one they already own
  scheduling          sent the DELIVERY to the engineer diary, four times
  can_we_serve        found nobody certified to service a machine that does
                      not exist yet
  raise_it            escalated that to the branch manager

Every step followed sensibly from the one before. The intent was right and
nothing enforced it: GATED covered four tools out of forty.

AND NOBODY IS EVER ASKED FOR AN ID

The same call asked a restaurant owner for an Asset ID, then an Account ID,
then a Work Order ID. They do not have them. They are ours, they are in the
call row, and asking somebody three minutes into a conversation tells them we
have lost track of it.
"""

from __future__ import annotations

import pytest


class _Tool:
    def __init__(self, name):
        self.__name__ = name


class _Ctx:
    def __init__(self, intent="service"):
        self.state = {"intent": intent, "language": "", "dealer_id": "D-REF"}


@pytest.fixture
def on_a_product_call(dbfile):
    from src import db, trace

    with db.txn() as c:
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-P','+13095550101','CT-1','2026-08-26T17:23:00')")
    trace.call_context("CALL-P")
    yield
    trace.call_context("")


# The tangle.


@pytest.mark.parametrize("tool", [
    "scheduling", "register_asset", "can_we_serve", "quote_visit",
    "should_send_someone", "raise_it", "warranty_status",
])
def test_a_buying_call_cannot_reach_the_service_machinery(on_a_product_call, tool):
    from src import guards

    out = guards.guard_tool(_Tool(tool), {}, _Ctx("product"))
    assert out is not None and out["blocked"] is True


@pytest.mark.parametrize("tool", [
    "scheduling", "register_asset", "can_we_serve", "quote_visit",
    "should_send_someone", "raise_it", "warranty_status",
])
def test_a_breakdown_call_still_reaches_all_of_it(on_a_product_call, tool):
    """The gate must not be so tight that the thing this product is for stops
    working."""
    from src import guards

    assert guards.guard_tool(_Tool(tool), {}, _Ctx("service")) is None


def test_a_breakdown_call_cannot_quote_machine_stock(on_a_product_call):
    """The other direction. Selling a freezer to somebody whose freezer just
    died is a different conversation and they have not asked for it."""
    from src import guards

    out = guards.guard_tool(_Tool("product_availability"), {}, _Ctx("service"))
    assert out["blocked"] is True

    assert guards.guard_tool(_Tool("product_availability"), {},
                             _Ctx("product")) is None


def test_an_unclassified_call_is_still_stopped_first(on_a_product_call):
    from src import guards

    out = guards.guard_tool(_Tool("scheduling"), {}, _Ctx(""))
    assert out["blocked"] is True
    assert "has not been classified" in out["why"]


# Nobody is asked for an id.


def test_a_missing_account_id_is_filled_in_from_the_call(on_a_product_call):
    """It used to ask the customer. They do not have one."""
    from src import guards

    args = {"account_id": ""}
    guards.guard_tool(_Tool("note_wishlist"), args, _Ctx("product"))
    assert args["account_id"] == "A-1"


def test_a_missing_site_id_is_filled_in_too(on_a_product_call):
    from src import guards

    args = {"site_id": ""}
    guards.guard_tool(_Tool("counter_slots"), args, _Ctx("product"))
    assert args["site_id"] == "S-1"


def test_the_work_order_and_machine_come_from_this_call(dbfile):
    """Not from whatever the model last read in a memory result."""
    from src import db, guards, trace

    with db.txn() as c:
        c.execute("INSERT INTO calls (id,from_e164,contact_id,started_at) "
                  "VALUES ('CALL-W','+13095550101','CT-1','2026-08-26T17:00:00')")
        c.execute("""INSERT INTO work_orders
                     (id,dealer_id,account_id,site_id,asset_id,contact_id,
                      reported_symptom,status,opened_at,opened_from_call)
                     VALUES ('WO-LIVE','D-REF','A-1','S-1','AS-FREEZER','CT-1',
                             'warm','open','2026-08-26T17:01:00','CALL-W')""")
    trace.call_context("CALL-W")

    args = {"work_order_id": "", "asset_id": ""}
    guards.guard_tool(_Tool("build_briefing"), args, _Ctx("service"))
    trace.call_context("")

    assert args["work_order_id"] == "WO-LIVE"
    assert args["asset_id"] == "AS-FREEZER"


def test_an_id_the_model_supplied_is_not_quietly_replaced(on_a_product_call):
    """Overwriting it would hide the case where the model reached for another
    customer's machine, which is what the ownership guard is for."""
    from src import db, guards, trace

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,kind,name) "
                  "VALUES ('A-OTHER','business','Rockvale')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-OTHER','A-OTHER','Rockvale')")
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AST-THEIRS','S-OTHER','True','TUC-27F','reach-in freezer')""")

    out = guards.guard_tool(_Tool("should_send_someone"),
                            {"asset_id": "AST-THEIRS"}, _Ctx("service"))
    assert out["blocked"] is True
    assert "another customer's account" in out["why"]


def test_filling_in_never_blocks_a_call_when_it_fails(on_a_product_call, monkeypatch):
    from src import db, guards

    def boom(*a, **k):
        raise RuntimeError("database gone")

    monkeypatch.setattr(db, "connect", boom)
    # must not raise
    guards.guard_tool(_Tool("note_wishlist"), {"account_id": ""}, _Ctx("product"))
