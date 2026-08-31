"""Repair the manufacturer/model split on the shop floor.

WHAT IS WRONG

`product_stock` was filled from real Google Shopping listings, and the brand
was taken as the first word of the title. That works for "Dell Latitude 5450"
and fails for everything else:

    Stainless    | Steel Dishwasher Hood
    General      | Foodservice Deep Fryer
    15.5         | ft Single Glass Door Merchandiser
    Office       | Star Executive Chair

105 of 923 rows, 11%, have a manufacturer that is not one. It is invisible in
the database and glaring the moment a person reads the shop floor on a screen.

HOW THE REAL BRANDS ARE IDENTIFIED

Not from a list somebody typed. A brand that appears across MANY DIFFERENT
listings is a brand; a word that appears once at the front of one title is
part of a description. So the evidence is the data's own repetition:

    Dell 33, Samsung 29, LG 21, Rockville 17, Lenovo 16, ViewSonic 15

Anything below the threshold, or that is obviously not a name (starts with a
digit, or is a common descriptive word), is folded back into the model so the
row reads as one product title instead of a fictional company.

WHAT IT DOES NOT DO

It does not invent a manufacturer. A listing whose brand cannot be
established keeps an empty manufacturer and the full title as its model, which
is honest and reads correctly. Guessing "Stainless" is a company is exactly
the failure being repaired.

    python -m scripts.clean_product_names          # report only
    python -m scripts.clean_product_names --write  # apply
"""

from __future__ import annotations

import sys

from src import db

# A word at the front of a title that is describing the thing rather than
# naming who made it. Counted from the data: every one of these appears as a
# "manufacturer" on rows whose model makes plain it is part of the title.
NOT_A_BRAND = {
    "stainless", "commercial", "professional", "general", "heavy", "double",
    "single", "new", "office", "portable", "modern", "large", "small",
    "indoor", "outdoor", "digital", "electric", "manual", "premium",
    "adjustable", "ergonomic", "executive", "under", "counter", "full",
    "compact", "deluxe", "standard", "industrial commercial", "wood",
    "metal", "glass", "black", "white", "silver", "used", "refurbished",
}

def _looks_like_a_name(word: str) -> bool:
    w = (word or "").strip()
    if len(w) < 2:
        return False
    if w[0].isdigit():
        return False
    if w.lower() in NOT_A_BRAND:
        return False
    return True


def real_brands() -> set[str]:
    """Brands the catalogue itself proves are brands.

    THE COUNT IS A TIEBREAKER, NOT THE TEST, and getting that round the wrong
    way discarded a third of the floor.

    Requiring two or more listings first flagged 296 rows of 923 for repair.
    Most were fine: a genuine manufacturer that happens to sell one model here
    appears exactly once, and there is nothing wrong with that. What actually
    marks a bad row is the WORD -- a digit at the front, or a description like
    "Stainless" or "Commercial" -- and _looks_like_a_name already decides that
    without counting anything.

    Adding the count back as an OR then made it worse in the other direction:
    "Stainless" fronts many listings, so recurrence rescued exactly the words
    the repair exists to remove. Frequency cannot be evidence of brandhood
    when the descriptive words are the frequent ones.

    So there is no count. A word is a brand if it reads as a name and is not
    a description, and how often it appears says nothing either way.
    """
    with db.connect() as c:
        rows = c.execute(
            """SELECT DISTINCT manufacturer FROM product_stock
               WHERE manufacturer IS NOT NULL""").fetchall()

    return {r["manufacturer"] for r in rows
            if _looks_like_a_name(r["manufacturer"])}


def load(write: bool = False) -> dict:
    brands = real_brands()

    with db.connect() as c:
        rows = c.execute(
            """SELECT rowid, manufacturer, model_number
               FROM product_stock""").fetchall()

    fixed, kept = [], 0
    for r in rows:
        make = (r["manufacturer"] or "").strip()
        model = (r["model_number"] or "").strip()

        if make in brands:
            kept += 1
            continue

        # Not a brand. Fold it back into the title so the row reads as one
        # product rather than as a company that does not exist.
        whole = f"{make} {model}".strip()
        fixed.append((r["rowid"], "", whole))

    if write and fixed:
        with db.txn() as c:
            for rowid, make, model in fixed:
                # Empty string, not NULL: the column is NOT NULL, and a blank
                # is the honest value here. There is no manufacturer to state.
                c.execute(
                    "UPDATE product_stock SET manufacturer=?, model_number=? "
                    "WHERE rowid=?", (make, model[:200], rowid))

    return {"brands_kept": len(brands), "rows_ok": kept,
            "rows_repaired": len(fixed), "written": bool(write and fixed),
            "examples": [(m, mo[:52]) for _, m, mo in fixed[:6]]}


if __name__ == "__main__":
    out = load("--write" in sys.argv)
    print(f"  {out['brands_kept']} names kept as brands")
    print(f"  {out['rows_ok']} rows already correct")
    print(f"  {out['rows_repaired']} rows had a manufacturer that was not one")
    for make, model in out["examples"]:
        print(f"      -> manufacturer blank, model: {model}")
    if not out["written"]:
        print("  nothing written. Re-run with --write to apply.")
