"""File every product under the vendor that actually carries it.

WHAT WAS WRONG, AND HOW A LIVE CALL FOUND IT

`seed_product_stock.load()` takes ONE dealer_id and stamps it on every row it
writes. It defaults to D-REF. So the entire catalogue, IT products included,
was filed under the refrigeration vendor:

    D-REF  laptop  ASUS    A7406CMA       $1188
    D-REF  laptop  Lenovo  LOQ 15IPH11E   $1188

Which did not matter while one desk answered as one vendor, because nothing
ever switched. The moment route_to_vendor started moving the desk to D-IT for
a laptop, every stock query after the switch looked in D-IT and found an empty
shelf. On the call that exposed it the desk said, in order:

    "I'm not finding any ASUS laptops in our system at the moment."
    "I don't see any Dell laptops in our system either."
    "I don't see any Lenovo IdeaPad laptops in our system."

Four ASUS laptops were sitting in the database the whole time. The routing was
right and the filing was wrong, and the routing is what made it visible.

The inconsistency is worse than the absence: `supply` found the Lenovo LOQ on
the same call, because it does not scope by vendor, while `lookup_product`
does. So the desk contradicted itself twice in one conversation about whether
a machine existed.

AND THE FAMILIES WERE WRONG TOO

`ups` held two ASUS laptops and a desktop. `printer` held an AORUS AE6H and an
Acer CP314-2H, neither of which is a printer. The family is what routes the
call, prices the labour and picks who may be sent, so a laptop filed as a UPS
is not a cosmetic error.

WHAT THIS DOES

Reads dealers.families, which is the only statement anywhere of who carries
what, and files each product under the vendor whose list contains its family.
Nothing is invented and nothing is deleted: only dealer_id moves.

    python -m scripts.refile_stock          show what would move
    python -m scripts.refile_stock --write  move it
"""

from __future__ import annotations

import sys

from src import db


def _families_by_dealer() -> dict[str, str]:
    """family -> dealer_id, from the only place that records it."""
    out: dict[str, str] = {}
    with db.connect() as c:
        rows = c.execute("SELECT id, families FROM dealers "
                         "WHERE families IS NOT NULL").fetchall()
    for r in rows:
        for fam in (r["families"] or "").split(","):
            fam = fam.strip().lower()
            if fam:
                out[fam] = r["id"]
    return out


def plan() -> dict:
    owner = _families_by_dealer()
    if not owner:
        return {"ok": False, "why": "no dealer declares any families"}

    moves, orphans = [], []
    with db.connect() as c:
        rows = c.execute(
            "SELECT rowid, dealer_id, manufacturer, model_number, family "
            "FROM product_stock").fetchall()

    for r in rows:
        fam = (r["family"] or "").strip().lower()
        belongs = owner.get(fam)
        if belongs is None:
            # Nobody claims that family. Left alone deliberately: guessing an
            # owner is how the laptops ended up on the refrigeration book.
            orphans.append({"model": r["model_number"], "family": r["family"]})
            continue
        if belongs != r["dealer_id"]:
            moves.append({"rowid": r["rowid"], "from": r["dealer_id"],
                          "to": belongs, "family": r["family"],
                          "model": f"{r['manufacturer']} {r['model_number']}"})

    return {"ok": True, "moves": moves, "orphans": orphans, "total": len(rows)}


def write() -> dict:
    out = plan()
    if not out.get("ok"):
        return out
    with db.txn() as c:
        for m in out["moves"]:
            c.execute("UPDATE product_stock SET dealer_id=? WHERE rowid=?",
                      (m["to"], m["rowid"]))
    return out


if __name__ == "__main__":
    doing = "--write" in sys.argv
    out = write() if doing else plan()

    if not out.get("ok"):
        print(out.get("why"))
        raise SystemExit(1)

    print(f"{out['total']} products on the book")
    by_move: dict[str, int] = {}
    for m in out["moves"]:
        by_move[f"{m['from']} -> {m['to']}"] = by_move.get(
            f"{m['from']} -> {m['to']}", 0) + 1
    for k, v in sorted(by_move.items()):
        print(f"  {v:>3}  {k}")
    for m in out["moves"][:12]:
        print(f"       {m['family']:<16} {m['model']}")
    if len(out["moves"]) > 12:
        print(f"       ... and {len(out['moves']) - 12} more")

    if out["orphans"]:
        print(f"\n{len(out['orphans'])} products in a family no dealer claims:")
        for o in out["orphans"][:10]:
            print(f"  {o['family']!r:<20} {o['model']}")
        print("  left where they are: guessing an owner is the original bug")

    print("\nwritten" if doing else "\nnothing written, pass --write")
