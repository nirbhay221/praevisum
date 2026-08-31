"""Put a picture against the machines on the shop floor.

WHY THERE WERE NONE

`market.py` fetches real Google Shopping listings and kept the price, the
title and the seller, and dropped the `imageUrl` that came with every one of
them. So the console could list 923 machines for sale and show a picture of
none of them. That capture is fixed, and from now on an image arrives with
every price refresh.

This is for the ones already on the shelf, which will otherwise stay blank
until their price happens to be refreshed.

IT COSTS MONEY, SO IT IS CAPPED AND IT ASKS

One paid search per product. 923 machines is 923 searches, which is not a
thing to run by accident, so this does nothing without --write and takes
--limit, and it goes after the machines somebody would actually SEE first:
most stock on hand, dearest first, which is what the console shows at the top.

WHAT IT WILL NOT DO

It will not attach a picture it is not confident in. A shopping search for a
model number returns near matches, and the wrong photograph against a $47,000
walk-in is worse than a blank space: somebody orders from the picture. The
result has to name the manufacturer or the model in its title before the image
is kept.

    python -m scripts.fetch_product_images --limit 20         # dry run
    python -m scripts.fetch_product_images --limit 20 --write
"""

from __future__ import annotations

import sys

from src import db
from src.market import _flat
from src.reviews import _fetch_shopping


def _theirs(title: str, manufacturer: str, model: str) -> bool:
    """Whether a listing is plausibly the machine we asked about.

    Cheap and strict. Near matches are the failure that matters: a picture of
    a different fridge against a real price is how somebody orders the wrong
    machine from a screen.
    """
    flat = _flat(title or "")
    if model and len(model) >= 4 and _flat(model)[:12] in flat:
        return True
    if manufacturer and len(manufacturer) >= 4:
        return _flat(manufacturer) in flat
    return False


def load(limit: int = 20, write: bool = False) -> dict:
    with db.connect() as c:
        rows = c.execute(
            """SELECT rowid, dealer_id, manufacturer, model_number, family,
                      on_hand, list_price
               FROM product_stock
               WHERE image_url IS NULL OR image_url = ''
               ORDER BY on_hand DESC, list_price DESC
               LIMIT ?""", (limit,)).fetchall()

    found, missed = [], []
    for r in rows:
        make = (r["manufacturer"] or "").strip()
        model = (r["model_number"] or "").strip()
        query = f"{make} {model}".strip() or r["family"]

        raw = _fetch_shopping(query) if write else None
        if raw is None:
            missed.append({"model": model[:44],
                           "why": "not searched" if not write else "no answer"})
            continue

        url = ""
        for item in (raw.get("shopping") or []):
            if not item.get("imageUrl"):
                continue
            if not _theirs(item.get("title", ""), make, model):
                continue
            url = item["imageUrl"]
            break

        if not url:
            missed.append({"model": model[:44],
                           "why": "nothing matched confidently"})
            continue

        with db.txn() as c:
            c.execute("UPDATE product_stock SET image_url=? WHERE rowid=?",
                      (url[:600], r["rowid"]))
        found.append({"model": model[:44], "url": url[:60]})

    with db.connect() as c:
        have = c.execute(
            "SELECT COUNT(*) n FROM product_stock "
            "WHERE image_url IS NOT NULL AND image_url != ''").fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM product_stock").fetchone()["n"]

    return {"looked_at": len(rows), "found": found, "missed": missed,
            "with_images": have, "total": total, "written": write}


if __name__ == "__main__":
    limit = 20
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    out = load(limit, "--write" in sys.argv)

    if not out["written"]:
        print(f"  would search {out['looked_at']} products, one paid search "
              "each. Nothing fetched.")
        print("  re-run with --write to actually do it.")
    else:
        for f in out["found"]:
            print(f"  got   {f['model']}")
        for m in out["missed"][:5]:
            print(f"  none  {m['model']}  ({m['why']})")
    print(f"  {out['with_images']} of {out['total']} products have a picture")
