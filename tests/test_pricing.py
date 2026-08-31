"""What the visit costs, and which lines the warranty pays for.

The money side of this system was entirely absent. Grep found zero references
to a labour rate, a call-out charge or an out-of-hours premium anywhere in it.
A visit recorded `labor_hours` after the fact and nothing priced them, so the
first question anybody asks on a service call, what will this cost me, met the
rule that there are no prices beyond what a tool returned and became "I will
have to confirm and follow up". Every time.

These tests are written against the manufacturers' REAL published terms,
loaded by scripts/load_warranties.py from the warranty statements themselves,
because the three things that make this hard are all things the real terms say
and a covered/not-covered flag cannot:

  wear items are excluded from every one of them,
  compressor cover outlasts parts and labour cover, and
  Traulsen ship the compressor and bill the owner for fitting it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest


@pytest.fixture
def terms(dbfile):
    """The real published warranty terms, in the test database."""
    from scripts.load_warranties import load
    return load()


def _age(asset_id, years):
    """Put a machine at a chosen age, so a term can be tested from both sides.

    Recorded as one we sold. These tests are about the warranty arithmetic,
    and whether the install date is ours or the customer's is a separate
    question with its own file: see tests/test_standing.py. A date the
    customer gave us is a claim rather than cover, and mixing the two here
    would test both badly.
    """
    from src import db

    when = (date.today() - timedelta(days=int(years * 365.25))).isoformat()
    with db.txn() as c:
        c.execute("UPDATE assets SET installed_on=?, installed_source='sold_by_us' "
                  "WHERE id=?", (when, asset_id))


# What the real terms say.


def test_the_terms_are_loaded_from_published_statements(terms):
    """Not invented. Each row carries the URL it came from and the day it was
    read, so a number a customer disputes can be checked rather than
    defended."""
    from src import db

    with db.connect() as c:
        rows = c.execute(
            "SELECT * FROM warranty_terms WHERE source_url IS NULL").fetchall()
    assert rows == [], "every term must carry its source"
    assert terms["terms"] >= 10


def test_a_series_beats_the_brand_default(terms):
    """Beverage-Air's CF and CT lines carry one year where everything else
    carries three, and Avantco runs one, two and three across three groups of
    prefixes. A brand-level answer gets those customers wrong."""
    from src import cover

    assert cover.published_terms("Beverage-Air", "HR1HC")["parts_years"] == 3
    assert cover.published_terms("Beverage-Air", "CF1HC")["parts_years"] == 1


def test_the_distributor_prefix_does_not_hide_the_series(terms):
    """Our Avantco model numbers carry the catalogue item number in front of
    the manufacturer's own: 178Z1RGHC is item 178 and model Z1RGHC. Without
    stripping it every Z-series machine falls to the one year default and gets
    told its three year cover has run out."""
    from src import cover

    assert cover.published_terms("Avantco Refrigeration", "178Z1RGHC")["parts_years"] == 3


# Coverage is per line, not per machine.


def test_a_covered_machine_is_never_quoted_a_part_price(terms, corpus):
    from src import pricing

    _age("AS-FREEZER", 2)     # well inside Traulsen's six year term
    # A weekday morning on purpose. Left to default this would be priced for
    # whenever the suite happens to run, and an evening run adds the out of
    # hours premium, which is charged even on a covered machine.
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-26T10:00:00")

    board = [x for x in q["lines"] if "control board" in x["what"].lower()][0]
    assert board["charged"] is False
    assert q["total"] == 0.0
    assert q["covered_by_warranty"] > 0


def test_a_wear_item_is_chargeable_on_a_fully_covered_machine(terms, corpus):
    """The one that would have been got wrong. No warranty in this trade
    covers gaskets, bulbs or shelf pins, and the door gasket is one of the
    commonest calls we take."""
    from src import db, pricing

    with db.txn() as c:
        c.execute("""INSERT INTO parts (sku,dealer_id,name,unit_cost,families)
                     VALUES ('P-DOORGASKET','D-REF','Door gasket',92.0,
                             'reach-in freezer')""")

    _age("AS-FREEZER", 2)
    q = pricing.quote_visit("AS-FREEZER", ["P-DOORGASKET"])

    gasket = [x for x in q["lines"] if "gasket" in x["what"].lower()][0]
    assert gasket["charged"] is True
    assert "wear item" in gasket["why"]
    assert q["total"] > 0


def test_the_compressor_outlives_the_parts_term(terms, corpus):
    """A six and a half year old Traulsen has a covered compressor and nothing
    else covered. One boolean on the asset cannot say that."""
    from src import cover

    _age("AS-FREEZER", 6.5)

    assert cover.covers("AS-FREEZER", "Compressor overload relay")["parts"] is True
    assert cover.covers("AS-FREEZER", "Electronic control board")["parts"] is False


def test_the_compressor_is_covered_and_the_labour_to_fit_it_is_not(terms, corpus):
    """Traulsen's own statement: the owner pays installation, recharging and
    repair costs on a warranty compressor. That sentence is worth several
    hundred dollars on a quote."""
    from src import pricing

    _age("AS-FREEZER", 2)
    from src import db
    with db.txn() as c:
        c.execute("""INSERT INTO parts (sku,dealer_id,name,unit_cost,families)
                     VALUES ('P-COMPRESSOR','D-REF','Compressor overload relay',
                             54.75,'reach-in freezer')""")

    q = pricing.quote_visit("AS-FREEZER", ["P-COMPRESSOR"],
                            when="2026-08-26T10:00:00")
    part = [x for x in q["lines"] if "compressor" in x["what"].lower()][0]
    labour = [x for x in q["lines"] if x["what"].startswith("Labour")][0]

    assert part["charged"] is False, "the compressor is covered"
    assert labour["charged"] is True, "and fitting it is not"
    assert q["total"] > 0


def test_not_knowing_the_make_never_becomes_out_of_warranty(terms, corpus):
    """We hold no published terms for Dell laptops. Telling somebody their
    machine is out of warranty on the strength of our own ignorance is a claim
    we cannot support and the one they are most likely to be able to check."""
    from src import cover

    out = cover.covers("AS-LAPTOP", "LCD display assembly")
    assert out["known"] is False
    assert "Do NOT say it is out of warranty" in out["say"]


# Where the numbers come from.


def test_the_labour_rate_carries_its_real_source(dbfile):
    """A rate somebody can check is a rate somebody can argue with, which is
    the only kind worth quoting."""
    from src import pricing

    out = pricing.labour_rate()
    assert out["rate"] == round(pricing.BLS_HOURLY_WAGE * pricing.SHOP_MULTIPLIER, 2)
    assert "49-9021" in out["source"]
    assert pricing.BLS_SERIES in out["source"]


def test_a_dealer_rate_beats_the_federal_figure(dbfile):
    """The BLS number is a defensible starting point, not a rule."""
    from src import db, pricing

    with db.txn() as c:
        c.execute("UPDATE dealers SET labour_rate=145.0 WHERE id='D-REF'")

    assert pricing.labour_rate()["rate"] == 145.0


def test_the_hours_come_from_jobs_we_actually_did(dbfile, corpus):
    """Not a guess. The same evidence the van loading uses, asked a different
    way."""
    from src import pricing

    out = pricing.hours_for("reach-in freezer")
    assert out["jobs"] >= pricing.ENOUGH_JOBS
    assert str(out["jobs"]) in out["basis"]


def test_too_few_jobs_says_so_rather_than_inventing_a_number(dbfile):
    """One anecdote divided by itself is not an estimate."""
    from src import pricing

    out = pricing.hours_for("blast chiller")
    assert out["hours"] == pricing.ASSUMED_HOURS
    assert "not done enough of these" in out["basis"]


def test_the_quote_reports_a_range_not_a_single_number(terms, corpus):
    """A job that has run from one to four hours before will not take exactly
    1.9 this time, and quoting one number as though it were firm is how a
    quote becomes an argument."""
    from src import pricing

    _age("AS-FREEZER", 9)      # out of warranty, so the hours drive the total
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"])

    assert "range" in q
    low, high = q["range"]
    assert low <= q["total"] <= high


# Out of hours.


def test_the_premium_is_charged_even_on_a_covered_machine(terms, corpus):
    """Manufacturer labour cover is straight time. The overtime is the
    owner's, and the line says so rather than burying it."""
    from src import pricing

    _age("AS-FREEZER", 2)
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"],
                            when="2026-08-26T19:30:00")

    premium = [x for x in q["lines"] if "Out of hours" in x["what"]][0]
    assert premium["charged"] is True
    assert q["total"] == premium["amount"]
    assert "straight time" in premium["why"]


def test_a_weekday_morning_carries_no_premium(terms, corpus):
    from src import pricing

    _age("AS-FREEZER", 9)
    q = pricing.quote_visit("AS-FREEZER", when="2026-08-26T10:00:00")

    assert q["after_hours"] is False
    assert not [x for x in q["lines"] if "Out of hours" in x["what"]]


# Keeping it.


def test_the_quote_is_written_down_line_by_line(terms, corpus):
    """A quote given on a call is the thing most likely to be argued about
    later, and the only way the review pass can compare what was quoted
    against what the visit actually billed."""
    from src import db, pricing

    _age("AS-FREEZER", 9)
    q = pricing.quote_visit("AS-FREEZER", ["P-CONTROLBOA"])

    with db.connect() as c:
        row = c.execute("SELECT * FROM quotes WHERE id=?", (q["quote_id"],)).fetchone()
        lines = c.execute("SELECT * FROM quote_lines WHERE quote_id=?",
                          (q["quote_id"],)).fetchall()

    assert row is not None
    assert row["total"] == q["total"]
    assert "49-9021" in row["rate_source"], "the rate must stay checkable"
    assert len(lines) == len(q["lines"])


def test_an_unknown_machine_is_refused_rather_than_priced(dbfile):
    from src import pricing

    assert pricing.quote_visit("AS-NOTHING")["ok"] is False


# What the desk is told.


def test_the_desk_is_told_to_price_from_the_tool_only(dbfile):
    from src import agents

    rules = " ".join(agents.DESK_RULES.split())
    assert "Call quote_visit for ANY question about what a visit will cost" in rules
    assert "never guess an hourly rate" in rules
    assert "Coverage is PER LINE, not per machine" in rules


def test_the_tool_is_actually_on_the_desk(dbfile):
    from src import agents

    names = {getattr(t, "__name__", "") for t in agents.front_agent.tools}
    assert "quote_visit" in names
    assert "quote_visit" in {getattr(t, "__name__", "")
                             for t in agents.desk_agent.tools}
