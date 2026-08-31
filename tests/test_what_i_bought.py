"""Answering "what did I buy today", which nothing could.

WHAT HAPPENED ON A LIVE CALL

The caller bought four things in twenty minutes: an HP laptop, an Atosa
cooler, a CyberPowerPC desktop and a chair. Then asked what they had bought.

The desk called `load_memory` six times, which searches the repair corpus, and
answered "I see a history of service calls, but no purchase orders for
equipment or parts."

`what_we_sold_them` existed, was on the agent, and would not have helped: it
reads ASSETS, and a machine only becomes an asset when it is delivered. So the
entire ordered-but-not-arrived stage was invisible to the one person who most
wants to see it.

AND THE ORDER DID NOT KNOW WHO SOLD IT

`purchase_orders` had no dealer_id. The company was inferred from the account,
and an account belongs to whichever business the caller first rang. So the HP
laptop, bought from the IT company, was filed as a refrigeration sale because
the conversation started there.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def a_customer_who_bought(dbfile):
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-B','D-REF','business','Bought Things',"
                  "'2024-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-B','A-B','kitchen')")
        # placed and on the way
        c.execute("INSERT INTO purchase_orders (id,account_id,site_id,status,"
                  "placed_at,subtotal,dealer_id) VALUES "
                  "('PO-1','A-B','S-B','confirmed','2026-08-31',379.0,'D-IT')")
        c.execute("INSERT INTO purchase_lines (po_id,line_no,description,qty,"
                  "unit_price) VALUES ('PO-1',1,'HP 15.6 laptop',1,379.0)")
        # still a draft, not an order
        c.execute("INSERT INTO purchase_orders (id,account_id,site_id,status,"
                  "placed_at,subtotal,dealer_id) VALUES "
                  "('PO-2','A-B','S-B','draft','2026-08-31',149.99,'D-REF')")
        c.execute("INSERT INTO purchase_lines (po_id,line_no,description,qty,"
                  "unit_price) VALUES ('PO-2',1,'Realspace chair',1,149.99)")
        # already delivered, so it is a machine now
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on,installed_source) VALUES "
                  "('AS-B','S-B','Traulsen','G12010','reach-in freezer',"
                  "'2025-01-01','sold_by_us')")
    return "A-B"


def test_it_can_finally_answer_what_they_bought(a_customer_who_bought):
    """The question that got six wrong answers."""
    from src.ownership import what_we_sold_them

    out = what_we_sold_them(a_customer_who_bought)
    assert out["ordered_not_delivered"] == 1
    assert out["on_order"][0]["id"] == "PO-1"
    assert "HP 15.6 laptop" in out["on_order"][0]["items"]


def test_a_draft_is_not_called_an_order(a_customer_who_bought):
    """A draft is a conversation, not a purchase. Reading it back as placed is
    how somebody believes they bought something they did not."""
    from src.ownership import what_we_sold_them

    out = what_we_sold_them(a_customer_who_bought)
    assert [o["id"] for o in out["drafts"]] == ["PO-2"]
    assert "PO-2" not in [o["id"] for o in out["on_order"]]
    assert "is not an order until they say yes" in out["say"]


def test_delivered_machines_still_come_back(a_customer_who_bought):
    """Adding the order stage must not lose the stage that already worked."""
    from src.ownership import what_we_sold_them

    out = what_we_sold_them(a_customer_who_bought)
    assert out["count"] == 1
    assert out["machines"][0]["model_number"] == "G12010"


def test_a_delivered_order_stops_being_on_the_way(a_customer_who_bought):
    from src import db
    from src.ownership import what_we_sold_them

    with db.txn() as c:
        c.execute("INSERT INTO deliveries (id,po_id,carrier,delivered_on,"
                  "notified_at) VALUES ('DEL-1','PO-1','UPS','2026-08-31',"
                  "'2026-08-31T10:00:00')")

    assert what_we_sold_them(a_customer_who_bought)["ordered_not_delivered"] == 0


def test_the_order_records_the_company_that_sold_it(dbfile):
    """Not the account's company. An account belongs to whichever business the
    caller first rang, so a laptop from the IT company was being filed as a
    refrigeration sale."""
    from src import db
    from src.buying import create_purchase_order
    from src.tenancy import routed_to

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-X','D-REF','business','Rang Fridge','2024-01-01')")

    routed_to("D-IT")
    try:
        create_purchase_order("A-X", ["HP laptop"])
    finally:
        routed_to("")

    with db.connect() as c:
        row = c.execute("SELECT dealer_id FROM purchase_orders "
                        "WHERE account_id = 'A-X'").fetchone()
    assert row["dealer_id"] == "D-IT", (
        "the sale was filed against the wrong business")


def test_the_agent_is_told_which_tool_answers_it(dbfile):
    """It reached for load_memory six times. An instruction that does not name
    the right tool for a question the caller actually asks is the failure this
    whole file is about."""
    from src import agents

    class Ctx:
        def __init__(self):
            self.state = {"dealer_id": "D-REF"}

    text = agents.front_agent.instruction(Ctx())
    assert "what_we_sold_them" in text
    assert "load_memory" in text
    assert "What did I buy today" in text


# --------------------------------------------------------------------------
# and what the line hears, which was guessing
# --------------------------------------------------------------------------

def test_the_transcriber_is_told_what_language_to_expect():
    """OBSERVED: one English speaker was transcribed as English, then
    Portuguese, then Hindi, then German, inside ninety seconds. Once it
    guessed Portuguese the desk called set_language and switched, which is the
    feature working correctly on a false premise.

    Stating the language is not the same as only speaking one: set_language
    still switches what the desk SAYS when a caller genuinely asks."""
    from src.telephony.twilio_bridge import _run_config

    heard = _run_config().input_audio_transcription
    assert heard.language_codes == ["en-US"]
    assert heard.language_auto is None, "detection is back on"


def test_a_dealer_can_say_their_callers_speak_two_languages(monkeypatch):
    """Inferred from an accent is wrong. Stated by the business is right."""
    monkeypatch.setenv("PRAEVISUM_CALLER_LANGUAGES", "en-US,es-US")
    from src.telephony.twilio_bridge import _spoken_here

    assert _spoken_here() == ["en-US", "es-US"]


def test_the_carrier_names_are_biased_for():
    """A caller said UPS and was quoted USPS Priority Mail. Said it again and
    it came back UBC. On a desk that ships things these are not rare words,
    and the cost is a wrong carrier on a real order."""
    from src.telephony.twilio_bridge import _run_config

    phrases = _run_config().input_audio_transcription.adaptation_phrases
    for word in ("UPS", "FedEx", "2nd Day Air", "EPA 608", "walk-in cooler"):
        assert word in phrases


def test_the_desk_still_transcribes_its_own_speech():
    """Both sides are recorded: the work order needs it and so does any later
    argument about what was said."""
    from src.telephony.twilio_bridge import _run_config

    assert _run_config().output_audio_transcription is not None
