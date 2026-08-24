"""Two businesses share one phone service and must share nothing else.

The leak that produced these tests: a refrigeration caller describing a warm
walk-in was told about Dell LCD panels at 0.449 confidence, because the
retrieval index was a single global object. The repair corpus is the most
valuable thing a dealer owns. Handing a slice of it to another company is the
failure that ends the product, so it gets its own file.
"""

from __future__ import annotations

from conftest import IT, REF


def test_corpus_does_not_cross_dealers(corpus):
    """The same sentence must reach a different world for each dealer."""
    from src.memory import index_for

    ref = index_for(REF).search("screen has gone black", limit=5)
    it = index_for(IT).search("screen has gone black", limit=5)

    assert all(h.repair.manufacturer == "Traulsen" for h in ref), \
        "refrigeration dealer reached IT repairs"
    assert any(h.repair.manufacturer == "Dell" for h in it), \
        "IT dealer could not reach its own repairs"

    ref_ids = {h.repair.id for h in ref}
    it_ids = {h.repair.id for h in it}
    assert not (ref_ids & it_ids)


def test_every_index_holds_only_its_own_repairs(corpus):
    """Not just the top hits. Nothing of the other dealer's is in there."""
    from src import db
    from src.memory import index_for

    with db.connect() as c:
        owner = {r["id"]: r["dealer_id"]
                 for r in c.execute("SELECT id, dealer_id FROM repairs")}

    for dealer in (REF, IT):
        idx = index_for(dealer)
        # search with a term common to both corpora and take everything
        for hit in idx.search("it", limit=1000):
            assert owner[hit.repair.id] == dealer, \
                f"{dealer} index holds repair owned by {owner[hit.repair.id]}"


def test_parts_lookup_is_scoped(dbfile):
    """A refrigeration dealer cannot see, price or sell an IT part."""
    from src import tools

    class Ctx:
        def __init__(self, d):
            self.state = {"dealer_id": d}

    ref = tools.lookup_product("battery", Ctx(REF))
    it = tools.lookup_product("battery", Ctx(IT))

    def skus(r):
        return {m.get("sku") for m in (r.get("parts") or [])}

    assert "IT-BATTERY" not in skus(ref)
    assert "IT-BATTERY" in skus(it)


def test_technicians_are_scoped(dbfile):
    """A dispatcher must never be offered another company's technician."""
    from src import db

    with db.connect() as c:
        for dealer, expected in [(REF, {"T-1"}), (IT, {"T-2"})]:
            got = {r["id"] for r in c.execute(
                "SELECT id FROM technicians WHERE dealer_id=?", (dealer,))}
            assert got == expected
