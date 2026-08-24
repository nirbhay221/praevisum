"""What can physically go on a machine.

Two separate bad-data incidents produced this file. Semantic recall crossed
manufacturers and carried part numbers with it, so a Traulsen freezer was
offered Whirlpool parts. Then a seed script wrote one fitment row per part per
asset, so an uninterruptible power supply was offered an LCD panel, a laptop
battery and a keyboard.

Both are the same class of mistake and both are invisible from the outside:
the agent sounds completely confident either way. The only thing that catches
them is an assertion about what fits what.
"""

from __future__ import annotations

from conftest import IT, REF


class Ctx:
    def __init__(self, dealer):
        self.state = {"dealer_id": dealer, "caller": {}}


def fitting_skus(dealer, asset_id):
    """The parts the loader would consider for one machine."""
    from src import db

    with db.connect() as c:
        a = c.execute("SELECT manufacturer, model_number, family FROM assets "
                      "WHERE id=?", (asset_id,)).fetchone()
        return {r["sku"] for r in c.execute(
            """SELECT DISTINCT p.sku FROM parts p
               JOIN fitments f ON f.sku=p.sku
               WHERE p.dealer_id=? AND f.manufacturer=? AND ? LIKE f.model_pattern
                 AND (p.families IS NULL
                      OR (',' || p.families || ',') LIKE ('%,' || ? || ',%'))""",
            (dealer, a["manufacturer"], a["model_number"], a["family"]))}


def test_a_ups_is_not_offered_laptop_parts(dbfile):
    """The fixture deliberately contains the bad fitment rows.

    The family filter is what has to reject them. If this test fails the seed
    data is being trusted, which is how the bad rows shipped the first time.
    """
    assert fitting_skus(IT, "AS-UPS") == set()


def test_a_laptop_is_offered_laptop_parts(dbfile):
    """The filter must not simply reject everything."""
    assert fitting_skus(IT, "AS-LAPTOP") == {"IT-LCDPANEL", "IT-BATTERY"}


def test_a_freezer_is_offered_freezer_parts(dbfile):
    assert fitting_skus(REF, "AS-FREEZER") == {
        "P-DEFROSTTHE", "P-EVAPFAN", "P-CONTROLBOA"}


def test_migration_leaves_no_crossing_fitments(dbfile):
    """After migrating, no part fits a family it does not belong on.

    The fixture ships the bad rows deliberately. Running the migration must
    remove them, and running it a second time must find nothing left to do.
    """
    import sys

    sys.path.insert(0, str(__import__("conftest").ROOT / "scripts"))
    import migrate

    migrate.main()

    from src import db

    with db.txn() as c:
        assert c.execute(migrate.PRUNE).rowcount == 0, \
            "fitments still cross equipment families after migrating"


def test_every_part_is_mapped_to_a_family(dbfile):
    """An unmapped part fits anything, which is how the bug survived once.

    The first prune left three parts out of the map. Because the prune skips
    parts with no families set, those three kept their nonsense rows and a UPS
    battery cartridge went on being offered for laptops. Silence is the danger
    here, so the gap gets asserted rather than warned about.
    """
    import sys

    sys.path.insert(0, str(__import__("conftest").ROOT / "scripts"))
    import migrate

    from src import db

    with db.connect() as c:
        skus = {r["sku"] for r in c.execute("SELECT sku FROM parts")}

    assert not (skus - set(migrate.FAMILIES)), \
        "parts with no family mapping fit every machine in the catalogue"


def test_loader_never_crosses_manufacturers(corpus):
    """Recall may cross brands. Parts may not.

    A fault seen on another make is a real hint and worth carrying across.
    A part number from another make is a technician holding something that
    does not fit, which is worse than carrying nothing because it was believed.
    """
    from src.reason import what_to_load

    r = what_to_load(REF, "AS-FREEZER", "not holding temp overnight")
    assert r["ok"]

    from src import db
    with db.connect() as c:
        allowed = {row["sku"] for row in c.execute(
            """SELECT p.sku FROM parts p JOIN fitments f ON f.sku=p.sku
               WHERE f.manufacturer='Traulsen'""")}

    for part in r["load"] + r["left_behind"]:
        assert part["sku"] in allowed, \
            f"{part['sku']} does not fit a Traulsen"
