"""Booking a carrier, which was the missing half of the shipping leg.

WHAT WAS THERE AND WHAT WAS NOT

`carrier_delivered` handles a carrier reporting that a parcel landed. That is
the END of shipping. The beginning did not exist: the `shipments` table has
columns for carrier, service level, tracking, ship date and cost, and no code
anywhere had ever written a row into it.

So an order could be placed and confirmed, and then nothing happened to it
until somebody told a carrier out of band, in a way the system never saw.

THE ONE WORTH READING

`test_it_does_not_invent_a_tracking_number`. The tempting version books the
collection and fills the tracking field immediately, because an empty column
looks unfinished. A tracking number the customer cannot type into the
carrier's site is worse than a blank one: they will try it, it will fail, and
they will believe the parcel is lost.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_confirmed_order(dbfile):
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-S','D-REF','business','Brady Bakery','2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label,address) VALUES "
                  "('S-S','A-S','shop','412 Brady St, Davenport IA')")
        c.execute("INSERT INTO contacts (id,account_id,name,role,channel_pref) "
                  "VALUES ('C-S','A-S','Ada Brady','owner','sms')")
        c.execute("INSERT INTO purchase_orders (id,account_id,site_id,"
                  "contact_id,status,placed_at) VALUES "
                  "('PO-S','A-S','S-S','C-S','confirmed','2026-08-28')")
        c.execute("INSERT INTO purchase_lines (po_id,line_no,description,qty,"
                  "unit_price) VALUES ('PO-S',1,'Display cooler',1,3299.0)")
    return "PO-S"


def test_booking_writes_the_shipment_nothing_ever_wrote(a_confirmed_order):
    """The table existed with 0 rows and no writer."""
    from src import db, shipping

    out = shipping.book_collection("D-REF", a_confirmed_order, "UPS",
                                   "two_day", send=False)
    assert out["ok"] is True

    with db.connect() as c:
        row = c.execute("SELECT carrier, service_level, eta_date, tracking "
                        "FROM shipments WHERE po_id = ?",
                        (a_confirmed_order,)).fetchone()
    assert row["carrier"] == "UPS"
    assert row["service_level"] == "2nd Day Air"
    assert row["eta_date"]


def test_it_does_not_invent_a_tracking_number(a_confirmed_order):
    """A number the customer cannot type into ups.com is worse than a blank
    field, because they will try it and think the parcel is lost."""
    from src import db, shipping

    out = shipping.book_collection("D-REF", a_confirmed_order, send=False)
    assert out["tracking"] == ""

    with db.connect() as c:
        assert c.execute("SELECT tracking FROM shipments WHERE po_id = ?",
                         (a_confirmed_order,)).fetchone()["tracking"] is None

    assert shipping.in_transit("D-REF")["without_tracking"] == 1


def test_tracking_is_attached_when_the_carrier_returns_it(a_confirmed_order):
    from src import shipping

    shipping.book_collection("D-REF", a_confirmed_order, send=False)
    got = shipping.note_tracking("D-REF", a_confirmed_order, "1Z999AA101")
    assert got["ok"] is True
    assert shipping.in_transit("D-REF")["without_tracking"] == 0


def test_a_draft_order_is_never_shipped(dbfile):
    """Shipping a draft is how somebody receives a machine they were still
    deciding about."""
    from src import db, shipping

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-DR','D-REF','business','Undecided','2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label,address) VALUES "
                  "('S-DR','A-DR','shop','1 Main St')")
        c.execute("INSERT INTO purchase_orders (id,account_id,site_id,status,"
                  "placed_at) VALUES ('PO-DR','A-DR','S-DR','draft','2026-08-28')")

    out = shipping.book_collection("D-REF", "PO-DR", send=False)
    assert out["ok"] is False
    assert "draft" in out["why"]

    with db.connect() as c:
        assert not c.execute("SELECT 1 FROM shipments").fetchone()


def test_it_will_not_book_the_same_order_twice(a_confirmed_order):
    """Two collections is two vans and one parcel."""
    from src import db, shipping

    shipping.book_collection("D-REF", a_confirmed_order, send=False)
    again = shipping.book_collection("D-REF", a_confirmed_order, send=False)

    assert again["ok"] is False
    assert "already booked" in again["why"]
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM shipments").fetchone()["n"] == 1


def test_no_address_means_nobody_can_be_asked_to_collect(dbfile):
    from src import shipping, db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-NA','D-REF','business','No Address','2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-NA','A-NA','shop')")
        c.execute("INSERT INTO purchase_orders (id,account_id,site_id,status,"
                  "placed_at) VALUES ('PO-NA','A-NA','S-NA','confirmed','2026-08-28')")

    out = shipping.book_collection("D-REF", "PO-NA", send=False)
    assert out["ok"] is False
    assert "address" in out["why"]


def test_the_shipment_survives_a_failed_email(a_confirmed_order, monkeypatch):
    """Recorded first, mailed second. A mail failure must leave something
    somebody can chase, not lose the fact that we tried."""
    from src import db, shipping

    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("src.email_out.send", boom)
    monkeypatch.setenv("SHIPPING_DEPOT_EMAIL", "depot@example.com")

    out = shipping.book_collection("D-REF", a_confirmed_order, send=True)
    assert out["ok"] is True
    assert out["collection_request"]["sent"] is False

    with db.connect() as c:
        assert c.execute("SELECT 1 FROM shipments WHERE po_id = ?",
                         (a_confirmed_order,)).fetchone()


def test_the_collection_request_says_what_and_where(a_confirmed_order,
                                                    monkeypatch):
    """A depot cannot collect from an address the mail does not carry."""
    from src import shipping

    sent = {}
    monkeypatch.setenv("SHIPPING_DEPOT_EMAIL", "depot@example.com")
    monkeypatch.setattr("src.email_out.send",
                        lambda to, subj, body, **k: sent.update(
                            to=to, subject=subj, body=body) or {"ok": True})

    shipping.book_collection("D-REF", a_confirmed_order, "UPS", "overnight")

    assert sent["to"] == "depot@example.com"
    assert "Brady Bakery" in sent["body"]
    assert "412 Brady St" in sent["body"]
    assert "Display cooler" in sent["body"]
    assert "Next Day Air" in sent["body"]


def test_delivery_takes_it_out_of_transit(a_confirmed_order, monkeypatch):
    """The two halves meet: we booked it, the carrier reported it."""
    from src import shipping
    from src.delivery import carrier_delivered

    shipping.book_collection("D-REF", a_confirmed_order, send=False)
    assert len(shipping.in_transit("D-REF")["shipments"]) == 1

    carrier_delivered(a_confirmed_order, carrier="UPS")
    assert len(shipping.in_transit("D-REF")["shipments"]) == 0


def test_only_the_console_can_book_a_collection(dbfile):
    """The same rule price and stock follow. A caller must not be able to
    talk the desk into shipping something."""
    from src import agents
    from src.console_agent import console_agent

    console = [getattr(t, "__name__", "") for t in console_agent.tools]
    assert "ship_it" in console

    for name in ("front_agent", "desk_agent", "supply_agent", "advice_agent"):
        tools = [getattr(t, "__name__", "")
                 for t in getattr(agents, name).tools]
        assert "ship_it" not in tools
        assert "note_tracking" not in tools
