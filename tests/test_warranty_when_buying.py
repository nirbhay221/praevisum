"""What to say about warranty to somebody who is BUYING, not repairing.

HEARD ON A LIVE SALES CALL

A caller was choosing a display cooler and asked "does it come with a
warranty?". The desk answered:

    "We can't see the manufacturer's warranty terms from here; do you happen
    to have the paperwork?"

They were buying it new, from us, that minute. There is no paperwork for them
to have, and asking reads as though we do not know what we sell.

WHY IT HAPPENED

"Ask whether they have the paperwork" is the CORRECT line in a different
situation: a machine they already own, that we did not sell them, where the
install date and the cover are genuinely theirs to prove. cover.py says it for
exactly that reason and should keep saying it.

`warranty_options` is the buying-time tool. Its own docstring says "call it
when somebody is about to buy something, BEFORE they commit", and it had
borrowed the repair-time wording. The two situations look similar and are
opposite: in one the customer holds the evidence, in the other we do.
"""

from __future__ import annotations


def test_a_buyer_is_never_asked_for_paperwork(dbfile):
    """The bug. They are buying it new from us."""
    from src.aftercare import warranty_options

    out = warranty_options("Nobody We Know", "XYZ-1", 1127.0)
    said = out["say"].lower()

    assert "do you have the paperwork" not in said
    assert "whether they have the paperwork" not in said
    # The old wording said "do NOT ask them for paperwork" out loud. The
    # instruction is gone because the answer no longer has a hole in it to
    # fill: there is a plan to offer. What must stay true is that nothing
    # here ever sends a buyer looking for documents they cannot have.
    assert "paperwork" not in said


def test_it_says_whose_gap_it_is(dbfile):
    """We hold no terms for that make, and that is ours to own out loud."""
    from src.aftercare import warranty_options

    out = warranty_options("Nobody We Know", "XYZ-1", 1127.0)
    assert "we do not hold" in out["say"].lower()


def test_it_still_refuses_to_sell_EXTRA_YEARS_on_an_unknown_term(dbfile):
    """The half that was always right, and is not what changed.

    Selling somebody three more years on top of a term nobody can state is
    selling an unknown quantity, and that is still refused. What changed is
    that refusing an EXTENSION is no longer the same as having nothing to
    offer: a plan of our own starts on delivery and states its own cover, so
    it does not depend on knowing what the maker gives.

    The line this guards is the one between those two things. An answer here
    must never carry an extension.
    """
    from src.aftercare import warranty_options

    out = warranty_options("Nobody We Know", "XYZ-1", 1127.0)

    assert "extra_years" not in out, "that would be extending an unknown term"
    assert "covered_until_years" not in out
    assert out.get("standard_years") is None


def test_our_own_cover_is_never_passed_off_as_the_makers(dbfile):
    """The way this feature could do real harm.

    Selling our plan is honest. Letting a customer believe Serta underwrites
    it is not, because it decides who they ring when the chair breaks and
    what they are entitled to. The offer has to name whose cover it is.
    """
    from src.aftercare import warranty_options

    out = warranty_options("Nobody We Know", "XYZ-1", 1127.0)
    ours = out.get("our_own_cover") or {}
    assert ours.get("ours") is True
    assert out.get("standard_terms_on_file") is False
    assert "our cover, not the maker" in out["say"].lower()


def test_every_plan_offered_says_what_it_does_not_cover(dbfile):
    """A price with no exclusions attached is not an offer anybody can accept,
    and the exclusions are the part that gets left off when a sale is going
    well."""
    from src.aftercare import warranty_options

    out = warranty_options("Nobody We Know", "XYZ-1", 1127.0)
    plans = (out.get("our_own_cover") or {}).get("plans") or []
    assert plans, "we hold no terms, so our own cover is the whole answer"
    for plan in plans:
        assert plan["covers"].strip()
        assert plan["excludes"].strip()


def test_it_will_not_sell_a_plan_worth_more_than_the_advice_allows(dbfile):
    """The decline threshold does not stop applying because the plan has our
    name on it. Above a fifth of the purchase price the published advice is
    to decline, and we give that advice against our own product."""
    from src.aftercare import NOT_WORTH_IT_ABOVE
    from src.our_cover import plans_for

    out = plans_for(399.99, "office chair")
    for plan in out["plans"]:
        assert plan["share_of_price"] <= NOT_WORTH_IT_ABOVE
    assert out["priced_out"], "the dearest tier on a chair is over the line"


def test_a_known_make_still_quotes_the_real_term(dbfile):
    """Fixing the empty case must not break the normal one."""
    from src import db
    from src.aftercare import warranty_options

    with db.txn() as c:
        c.execute("INSERT INTO warranty_terms (manufacturer,series,"
                  "parts_years,labour_years,compressor_years,"
                  "compressor_labour_covered,source_url,read_on) VALUES "
                  "('Knownco','%',5.0,5.0,7.0,1,'https://example.com/w',"
                  "'2026-01-01')")

    out = warranty_options("Knownco", "K-1", 2000.0)
    assert out["standard_years"] == 5.0
    assert out["source"] == "https://example.com/w"


def test_the_repair_side_still_asks_for_paperwork(dbfile):
    """The line is correct where it belongs: a machine they already own, that
    we did not sell them, where the evidence is genuinely theirs."""
    from src import cover, db

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-P','D-REF','business','Theirs','2020-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-P','A-P','kitchen')")
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on,installed_source) VALUES "
                  "('AS-P','S-P','Unheardof','U-1','reach-in cooler',"
                  "'2024-01-01','customer_stated')")

    out = cover.covers("AS-P")
    assert out["known"] is False
    assert "paperwork" in out["say"].lower()
