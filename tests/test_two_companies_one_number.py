"""Who this caller is, and which vendor that does NOT decide.

THE LEAK, PROVEN AGAINST THE LIVE DATABASE

`caller.resolve` asked one question: whose number is this. Back when the front
counter was split across two numbers, that meant an IT customer ringing the
refrigeration line was recognised, greeted by name, and had their PRINTERS
handed to a refrigeration desk:

    Sean Kirby, Redwood Engineering, a D-IT account
    resolve("+15635558234") on a D-REF call
      -> known: True, equipment: Avision printer, AORUS printer

Scoping the lookup by the dialled number fixed that and bought something
worse. There is one number now, so the vendor in front of the caller is
whichever one the desk happened to be holding, and a nine year customer whose
account sat with the other one came back `known: False` and was greeted as a
stranger on the only number we publish.

WHAT ACTUALLY DECIDES THE VENDOR

Not the account. What they ask for.

`phones.e164` is a PRIMARY KEY, so a number reaches exactly one contact and
one account, and that account is the caller's identity. Assets carry no
vendor at all: they hang off the site, and the family on each one is what
picks the vendor, in route_to_vendor, per machine, as late as possible.

So reading somebody's own equipment back to them cannot cross a tenancy
boundary. There is no boundary to cross until they ask for something, and by
then the desk has routed.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def two_customers(dbfile):
    """One number at the IT business, another at the refrigeration one."""
    from src import db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name) "
                  "VALUES ('A-ITX','D-IT','business','Redwood Engineering')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-ITX','A-ITX','Office')")
        c.execute("""INSERT INTO assets (id,site_id,manufacturer,model_number,family)
                     VALUES ('AST-PRN','S-ITX','Avision','AM240QDW','printer')""")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('CT-ITX','A-ITX','Sean Kirby','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label) "
                  "VALUES ('+15635558234','CT-ITX','mobile')")


def test_their_own_business_still_knows_them(two_customers):
    from src import caller

    who = caller.resolve("+15635558234", "D-IT")
    assert who["known"] is True
    assert who["contact_name"] == "Sean Kirby"
    assert [a["family"] for a in who["assets"]] == ["printer"]


def test_the_desk_knows_them_whichever_vendor_it_is_holding(two_customers):
    """They rang the only number we publish. Which ledger their account sits
    in is not a reason to greet a nine year customer as a stranger."""
    from src import caller

    who = caller.resolve("+15635558234", "D-REF")

    assert who["known"] is True
    assert who["contact_name"] == "Sean Kirby"
    assert [a["family"] for a in who["assets"]] == ["printer"]


def test_which_vendor_holds_the_account_is_recorded_not_spoken(two_customers):
    """Downstream may want a default before the caller has said what they
    want. It is not a filter, and the caller never hears it."""
    from src import caller

    who = caller.resolve("+15635558234", "D-REF")
    assert who["account_vendor"] == "D-IT"
    assert "say" not in who


def test_reading_their_own_equipment_back_cannot_cross_a_vendor(two_customers):
    """The original leak, now prevented by a different mechanism: the printer
    is theirs, and its FAMILY is what routes it, so naming it to them is
    correct no matter which vendor the desk was holding a moment ago."""
    from src import caller, desk

    class Ctx:
        state = {"dealer_id": "D-REF"}

    who = caller.resolve("+15635558234", "D-REF")
    family = who["assets"][0]["family"]

    desk.route_to_vendor(family, Ctx())
    assert Ctx.state["dealer_id"] == "D-IT"


def test_it_does_not_crash_trying_to_register_them_twice(two_customers):
    """phones.e164 is a PRIMARY KEY. An early version of this raised a UNIQUE
    constraint failure in the middle of a call."""
    from src import caller

    for _ in range(3):
        who = caller.resolve("+15635558234", "D-REF")
        assert who["registered"] is False


def test_a_genuine_stranger_is_still_registered(dbfile):
    from src import caller

    who = caller.resolve("+13095559999", "D-REF")
    assert who["known"] is False
    assert who["registered"] is True
    assert who["contact_id"]


def test_no_vendor_given_changes_nothing(two_customers):
    """Telegram has no dialled number. Since identity never depended on the
    vendor, there is nothing to fall back from."""
    from src import caller

    who = caller.resolve("+15635558234")
    assert who["known"] is True
    assert who["contact_name"] == "Sean Kirby"


def test_the_phone_line_passes_the_dealer_through(dbfile):
    import inspect

    from src.telephony import twilio_bridge

    src = inspect.getsource(twilio_bridge._handle_call)
    assert "resolve(caller, dealer_id)" in src


def test_the_message_desk_resolves_the_dealer_first(dbfile):
    """Resolving the caller depends on knowing which line they messaged, so
    the order had to be inverted."""
    import inspect

    from src import desk

    src = inspect.getsource(desk._context)
    assert "resolve(identity, dealer)" in src


# The model number nobody should have to read out.


def test_the_desk_is_told_not_to_ask_for_what_it_holds(dbfile):
    from src import agents

    class _Ctx:
        state = {"dealer_id": "D-REF"}

    r = agents.front_agent.instruction(_Ctx())
    assert "DO NOT ASK FOR A MODEL NUMBER YOU ALREADY HAVE" in r
    assert "own exactly one of that kind, that is the machine" in r
    assert "Ask for a model number only when you genuinely cannot tell" in r

    # The rule must not name one trade's equipment. It said "reach-in freezer"
    # and appeared verbatim on the IT desk, the same mistake as the greeting
    # and the "you know nothing about refrigeration" line before it. Asserted
    # on the IT desk, because the refrigeration desk legitimately lists
    # reach-in freezers among its own families.
    # The rule itself must stay trade-neutral. The desk now lists every
    # family it covers, refrigeration included, so the check is on the RULE
    # rather than on the whole instruction.
    rule = r[r.index("DO NOT ASK FOR A MODEL NUMBER"):][:900]
    assert "reach-in freezer" not in rule
    assert "laptop" not in rule


# The crash this caused on a live call.


def test_registering_a_caller_records_which_business_they_rang(dbfile):
    """It did not, so every caller this system ever auto-registered was
    dealer-less: 1 of 107 accounts on the live book, and it happened to be the
    one belonging to the person testing it."""
    from src import caller, db

    who = caller.resolve("+13095557001", "D-IT")

    with db.connect() as c:
        dealer = c.execute("SELECT dealer_id FROM accounts WHERE id=?",
                           (who["account_id"],)).fetchone()["dealer_id"]
    assert dealer == "D-IT"


def test_an_account_with_no_dealer_is_not_a_stranger(dbfile):
    """Rows written before that column was filled in. Treating them as
    nobody's would make every existing customer a stranger overnight.

    And it did worse than that: the scoped lookup missed them, fell through to
    registration, and died on `UNIQUE constraint failed: phones.e164` in the
    middle of a real call, before a word was spoken.
    """
    from src import caller, db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name) "
                  "VALUES ('A-OLD',NULL,'person','Legacy Caller')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('CT-OLD','A-OLD','Arjun Raman','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label) "
                  "VALUES ('+13095557002','CT-OLD','mobile')")

    who = caller.resolve("+13095557002", "D-REF")
    assert who["known"] is True
    assert who["contact_name"] == "Arjun Raman"


def test_a_legacy_account_is_stamped_so_it_only_happens_once(dbfile):
    from src import caller, db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name) "
                  "VALUES ('A-OLD2',NULL,'person','Legacy')")
        c.execute("INSERT INTO contacts (id,account_id,name,role) "
                  "VALUES ('CT-OLD2','A-OLD2','Someone','owner')")
        c.execute("INSERT INTO phones (e164,contact_id,label) "
                  "VALUES ('+13095557003','CT-OLD2','mobile')")

    caller.resolve("+13095557003", "D-REF")

    with db.connect() as c:
        assert c.execute("SELECT dealer_id FROM accounts WHERE id='A-OLD2'"
                         ).fetchone()["dealer_id"] == "D-REF"


def test_resolving_the_same_number_twice_never_raises(dbfile):
    """The live failure was an IntegrityError escaping into the websocket
    handler, which killed the call before the greeting."""
    from src import caller

    for dealer in ("D-REF", "D-REF", "D-IT", "D-REF"):
        who = caller.resolve("+13095557004", dealer)
        assert who.get("contact_id")
