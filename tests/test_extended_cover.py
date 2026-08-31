"""Extended cover somebody bought, as against cover we only quoted.

THE GAP

`warranty_options` is a live tool. It reads the manufacturer's published term,
prices a few more years, and says it out loud on a sales call. Nothing recorded
the answer: no table, no column, no function for the customer saying yes.

So the desk could sell extended cover and, eighteen months later, compute
coverage from the manufacturer term alone and tell the same customer they were
out of warranty. `assets.warranty_until` looked like the place for it and was
NULL on all 428 rows.

THE TWO WORTH READING

`test_it_extends_rather_than_restarts`: two years on top of a six year term
must end at eight years from installation. Restarting the clock sells somebody
two years of cover they already had.

`test_the_quote_says_who_actually_owes_it`: the labour line read "covered by
Avantco under the 3 year parts and labour term" whenever labour came back
covered, including when the manufacturer term had expired and the customer was
covered because they paid US. Somebody reading that files a claim against a
manufacturer who owes nothing, it is refused, and the customer is chased for a
bill they bought their way out of.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def an_expired_machine(dbfile):
    """A machine whose manufacturer term has run out. Three year term,
    installed six years ago."""
    from datetime import date

    from src import db

    installed = f"{date.today().year - 6}-09-10"
    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-W','D-REF','business','Warranty Cafe','2018-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label,address,lat,lon) "
                  "VALUES ('S-W','A-W','kitchen','1 Main St',41.5,-90.5)")
        c.execute("INSERT INTO warranty_terms (manufacturer,series,parts_years,"
                  "labour_years,compressor_years,compressor_labour_covered,"
                  "source_url,read_on) VALUES ('Testco','%',3.0,3.0,5.0,1,"
                  "'https://example.com/terms','2026-01-01')")
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on,installed_source) VALUES "
                  "('AS-W','S-W','Testco','TX-1','reach-in cooler',?, "
                  "'sold_by_us')", (installed,))
    return "AS-W"


def test_before_the_sale_they_are_out_of_warranty(an_expired_machine):
    from src import cover

    out = cover.covers(an_expired_machine)
    assert out["parts"] is False
    assert out["labour"] is False
    assert "run out" in out["why"]


def test_selling_cover_is_recorded_and_honoured(an_expired_machine):
    """The whole gap: quoting worked, saying yes went nowhere."""
    from src import cover, extended

    sold = extended.sell_cover(an_expired_machine, 5, price=1250.0,
                               covers_labour=True, sold_by="Sam said yes")
    assert sold["ok"] is True

    out = cover.covers(an_expired_machine)
    assert out["parts"] is True
    assert out["labour"] is True
    assert out["extended_to"] == sold["covered_until"]


def test_it_extends_rather_than_restarts(an_expired_machine):
    """Five years on top of a three year term ends at eight years from
    INSTALLATION. Restarting the clock sells somebody cover they already had,
    and quietly moves the end date five years too far out."""
    from src import db, extended

    sold = extended.sell_cover(an_expired_machine, 5)

    with db.connect() as c:
        installed = c.execute("SELECT installed_on FROM assets WHERE id = ?",
                              (an_expired_machine,)).fetchone()["installed_on"]

    assert sold["manufacturer_term_years"] == 3.0
    assert sold["covered_until"][:4] == str(int(installed[:4]) + 8)
    assert sold["covered_until"] > installed


def test_parts_only_is_the_default_and_is_said(an_expired_machine):
    """Selling "five years cover" that turns out to exclude labour is how a
    warranty becomes an argument on a kitchen floor."""
    from src import cover, extended

    sold = extended.sell_cover(an_expired_machine, 5)
    assert sold["labour"] is False
    assert "Parts only" in sold["say"]

    out = cover.covers(an_expired_machine)
    assert out["parts"] is True
    assert out["labour"] is False
    assert "labour is chargeable" in out["say"]


def test_expired_extended_cover_does_not_cover(dbfile):
    """An extension that has itself run out must not read as live."""
    from src import cover, db, extended

    with db.txn() as c:
        c.execute("INSERT INTO accounts (id,dealer_id,kind,name,opened_on) "
                  "VALUES ('A-X','D-REF','business','Old','2010-01-01')")
        c.execute("INSERT INTO sites (id,account_id,label) "
                  "VALUES ('S-X','A-X','shop')")
        c.execute("INSERT INTO warranty_terms (manufacturer,series,parts_years,"
                  "labour_years,compressor_years,compressor_labour_covered) "
                  "VALUES ('Oldco','%',2.0,2.0,3.0,0)")
        c.execute("INSERT INTO assets (id,site_id,manufacturer,model_number,"
                  "family,installed_on,installed_source) VALUES "
                  "('AS-X','S-X','Oldco','OX-1','reach-in cooler',"
                  "'2012-01-01','sold_by_us')")

    extended.sell_cover("AS-X", 2)
    assert extended.cover_on("AS-X")["live"] is False
    assert cover.covers("AS-X")["parts"] is False


def test_it_will_not_sell_cover_twice(an_expired_machine):
    """Either a mistake or a customer being charged twice."""
    from src import db, extended

    extended.sell_cover(an_expired_machine, 3)
    again = extended.sell_cover(an_expired_machine, 2)

    assert again["ok"] is False
    assert "already carries" in again["why"]
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM cover_sold").fetchone()["n"] == 1


def test_cover_needs_a_machine_and_an_install_date(dbfile):
    from src import extended

    assert extended.sell_cover("AS-NOPE", 2)["ok"] is False
    assert extended.sell_cover("AS-NOPE", 0)["ok"] is False


def test_the_quote_says_who_actually_owes_it(an_expired_machine):
    """It said "covered by Testco under the 3 year term" whenever labour came
    back covered, including when Testco owed nothing. Somebody reading that
    files a claim that gets refused."""
    from src import extended, pricing

    extended.sell_cover(an_expired_machine, 5, covers_labour=True)
    q = pricing.quote_visit(an_expired_machine, "compressor not running")

    labour = [l for l in q["lines"] if l["what"].startswith("Labour")][0]
    assert labour["charged"] is False
    assert "bought from us" in labour["why"]
    assert "not the manufacturer" in labour["why"]
    assert "Testco" not in labour["why"]


def test_the_desk_can_actually_record_the_sale(dbfile):
    """Structural. A pricing tool with no way to say yes is a quote nobody can
    accept."""
    from src import agents

    for a in (agents.front_agent, agents.desk_agent):
        names = [getattr(t, "__name__", "") for t in a.tools]
        assert "warranty_options" in names, "it could always quote"
        assert "sell_extended_cover" in names, "and now it can sell"
