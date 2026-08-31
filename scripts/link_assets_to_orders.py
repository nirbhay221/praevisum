"""Record which order a machine came from, and tidy what that gap allowed.

WHY THE COLUMN DID NOT EXIST

`becomes_theirs` turns a delivered order into machines on the customer's
account, and nothing on the asset said which order it came from. That made two
things impossible:

  IDEMPOTENCE   there was no way to ask "have I already registered this
                order?", so a repeated delivery report -- a carrier retry, an
                operator clicking the console button after the webhook already
                fired -- minted a second identical machine. Observed: one
                customer with two ThinkPads and one laptop.

  TRACEABILITY  a warranty conversation two years later could see the machine
                and not the sale that put it there.

WHAT THIS ALSO FIXES

Assets registered before `_family_of` learned to read the catalogue were
written with no family at all, and family is what an engineer's qualification
is matched against. A machine with no family cannot be scheduled: not "nobody
is free" but "the question cannot be asked".

    python scripts/link_assets_to_orders.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402


def add_the_column() -> None:
    with db.connect() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(assets)")]
    if "from_order" in cols:
        print("  assets.from_order already exists")
        return
    with db.txn() as c:
        c.execute("ALTER TABLE assets ADD COLUMN from_order TEXT")
    print("  added assets.from_order")


def give_them_a_family() -> None:
    """Fill in the family on machines that were registered without one."""
    from src.ownership import _family_of

    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT a.id, a.manufacturer, a.model_number, ac.dealer_id,
                      (SELECT po.dealer_id FROM purchase_orders po
                        WHERE po.id = a.from_order) from_dealer
               FROM assets a
               JOIN sites s ON s.id = a.site_id
               JOIN accounts ac ON ac.id = s.account_id
               WHERE (a.family IS NULL OR TRIM(a.family) = '')
                 AND a.installed_source = 'sold_by_us'""")]

    fixed, stuck = 0, 0
    for r in rows:
        name = f"{r['manufacturer'] or ''} {r['model_number'] or ''}".strip()

        # THE ORDER'S COMPANY FIRST, THEN ANY OF THEM.
        #
        # An account belongs to whichever business the customer first rang,
        # and they can buy from any of the four. A bakery that rang the
        # refrigeration desk and later bought a laptop has that laptop on a
        # D-REF account, so looking it up on D-REF's own catalogue finds
        # nothing and the machine keeps its empty family forever.
        fam = _family_of(name, r["from_dealer"] or r["dealer_id"])
        if not fam:
            with db.connect() as c:
                everyone = [x[0] for x in c.execute("SELECT id FROM dealers")]
            for d in everyone:
                fam = _family_of(name, d)
                if fam:
                    break
        if not fam:
            stuck += 1
            continue
        with db.txn() as c:
            c.execute("UPDATE assets SET family=? WHERE id=?", (fam, r["id"]))
        print(f"    {r['id']}  {name[:34]:36} -> {fam}")
        fixed += 1

    print(f"  {fixed} given a family, {stuck} still without one")


def drop_the_twins() -> None:
    """Remove duplicate machines the missing idempotence guard created.

    Two assets are twins when they sit at the same site with the same make,
    model and install date. The OLDEST is kept, because anything already
    attached -- extended cover, a work order -- points at that one.
    """
    with db.connect() as c:
        groups = c.execute(
            """SELECT site_id, manufacturer, model_number, installed_on,
                      COUNT(*) n, MIN(rowid) keep
               FROM assets
               WHERE installed_source = 'sold_by_us'
               GROUP BY site_id, manufacturer, model_number, installed_on
               HAVING n > 1""").fetchall()

    removed = 0
    for g in groups:
        with db.connect() as c:
            extra = [r[0] for r in c.execute(
                """SELECT id FROM assets
                   WHERE site_id=? AND manufacturer=? AND model_number=?
                     AND installed_on=? AND rowid != ?""",
                (g["site_id"], g["manufacturer"], g["model_number"],
                 g["installed_on"], g["keep"]))]
        for aid in extra:
            with db.connect() as c:
                busy = c.execute(
                    """SELECT (SELECT COUNT(*) FROM work_orders WHERE asset_id=?)
                            + (SELECT COUNT(*) FROM cover_sold WHERE asset_id=?)""",
                    (aid, aid)).fetchone()[0]
            if busy:
                print(f"    keeping {aid}: something already points at it")
                continue
            with db.txn() as c:
                c.execute("DELETE FROM assets WHERE id=?", (aid,))
            print(f"    removed duplicate {aid} "
                  f"({g['manufacturer']} {g['model_number']})")
            removed += 1

    print(f"  {removed} duplicate machines removed")


if __name__ == "__main__":
    add_the_column()
    give_them_a_family()
    drop_the_twins()
