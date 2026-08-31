"""Load published manufacturer warranty terms. Real, sourced, dated.

WHY THIS IS A LOADER AND NOT A DICT IN THE CODE

These are published facts. They have a URL and a day they were read, they
differ per brand and per series, and they change: Traulsen's six year term
applies to units invoiced from January 2023 and not to the ones before it.
A row that carries its source is a number a customer can dispute and we can
check. A constant buried in a function is a number we can only defend.

WHAT THE REAL TERMS TURNED OUT TO SAY

Three things, none of which a covered/not-covered flag on the asset can
express, and all three change what a customer is charged:

  Wear items are excluded from every one of them. Gaskets, light bulbs and
  shelf pins are chargeable on a machine that is otherwise fully covered, and
  the door gasket is one of the commonest calls we take.

  Compressor cover outlasts parts and labour cover nearly everywhere, so a
  six and a half year old Traulsen has a covered compressor and nothing else.

  Traulsen ships a replacement compressor and bills the owner for fitting it.
  The compressor is free and the four hours are not.

Run: python -m scripts.load_warranties
"""

from __future__ import annotations

from datetime import date

from src import db

READ_ON = "2026-08-25"

GUIDE = "https://www.webstaurantstore.com/guide/932/refrigeration-warranties-explained.html"
TRAULSEN = "https://www.traulsen.com/traulsen-introduces-new-6-year-parts-and-labor-7-year-compressor-warranty"
BEVAIR = "https://beverage-air.com/content/warranty/Beverage-Air-Warranty-Statement.pdf"

# IT. Published terms for business and consumer lines, which differ by up to
# two years WITHIN the same brand: a ThinkPad carries three years where an
# IdeaPad carries one, and a Latitude carries three where an Inspiron carries
# one. That is the same shape as Beverage-Air's CF and CT lines and it is
# handled the same way, by series pattern.
LAPTOPS = "https://www.surebright.com/blog/which-laptop-brand-has-the-longest-warranty-a-coverage-comparison-across-apple-dell-hp-lenovo-asus-razer"

# manufacturer, series pattern, parts, labour, compressor, compressor labour
# covered, condition note, source
TERMS = [
    # Traulsen. Six and seven, and the compressor term is explicitly the part
    # alone: "all installation, recharging, and repair costs shall be the
    # responsibility of the Owner". That sentence is worth several hundred
    # dollars on a quote and it is the reason the column exists.
    ("Traulsen", "%", 6, 6, 7, 0,
     "Six year term applies to units invoiced from 1 January 2023. Older units "
     "carry the previous term, so check the unit's own paperwork before "
     "relying on this.", TRAULSEN),

    ("Continental", "%", 6, 6, 7, 1, None, GUIDE),

    # True. The guide reads seven across the board. True's own announcements
    # have carried a three year parts and labour term with a five year
    # compressor extension, so the term has moved over the years and a 2017
    # machine was not sold under today's terms. Recorded as published, with
    # the disagreement stated rather than silently resolved.
    ("True Refrigeration", "%", 7, 7, 7, 1,
     "Published terms for this brand have changed over the years and older "
     "units were sold under a shorter term. Treat this as indicative and ask "
     "for the unit's paperwork before promising cover.", GUIDE),

    # Beverage-Air. Three years on everything except the CF and CT lines,
    # which get one. The compressor runs four years past the parts term.
    ("Beverage-Air", "%", 3, 3, 7, 1,
     "Not effective unless registered on the Beverage-Air site within 10 days "
     "of installation. We cannot see whether that was done, so never quote "
     "zero on this brand without asking them to confirm registration.", BEVAIR),
    ("Beverage-Air", "CF%", 1, 1, 5, 1,
     "CF line carries the shorter one year term.", GUIDE),
    ("Beverage-Air", "CT%", 1, 1, 5, 1,
     "CT line carries the shorter one year term.", GUIDE),

    # Avantco, which splits three ways by model prefix. The five year
    # compressor term is the same across all of them.
    ("Avantco Refrigeration", "%", 1, 1, 5, 1,
     "Avantco's base term. The Z and stainless series carry longer parts and "
     "labour cover, matched by the rows below.", GUIDE),
    ("Avantco Refrigeration", "Z%", 3, 3, 5, 1, None, GUIDE),
    ("Avantco Refrigeration", "ZPT%", 3, 3, 5, 1, None, GUIDE),
    ("Avantco Refrigeration", "ZUC%", 3, 3, 5, 1, None, GUIDE),
    ("Avantco Refrigeration", "ZWT%", 3, 3, 5, 1, None, GUIDE),
    ("Avantco Refrigeration", "SS%", 2, 2, 5, 1, None, GUIDE),
    ("Avantco Refrigeration", "CPSS%", 2, 2, 5, 1, None, GUIDE),

    ("Delfield", "%", 3, 3, 7, 1, None, GUIDE),

    # Hoshizaki publishes a range rather than one term. The shortest is
    # recorded, because quoting the longest and being wrong sends somebody an
    # invoice they were told they would not get.
    ("Hoshizaki", "%", 1, 1, 2, 1,
     "This brand publishes 1 to 3 years parts and labour and 2 to 5 on the "
     "compressor depending on model. The shortest is recorded here on "
     "purpose: promising the longest and being wrong sends somebody a bill "
     "they were told would not come.", GUIDE),

    # ------------------------------------------------------------------
    # IT. The second business this service answers the phone for.
    #
    # Everything above is refrigeration, and for months that was the whole
    # table, so an IT customer asking about their warranty was told we hold
    # no terms for the make. There are 161 machines and 240 repairs on that
    # dealer's book.
    # ------------------------------------------------------------------

    # A whole computer has no separately-warranted "compressor", so the third
    # column carries the same term as the parts cover rather than a longer
    # one. Recording a made-up longer figure to fill the column would be the
    # invented-number problem again.
    ("Lenovo", "%", 1, 1, 1, 1,
     "Consumer lines such as IdeaPad carry one year. ThinkPad and other "
     "business lines carry three, matched by the rows below. Ask which line "
     "it is before quoting anything.", LAPTOPS),
    ("Lenovo", "THINKPAD%", 3, 3, 3, 1, None, LAPTOPS),
    ("Lenovo", "T4%", 3, 3, 3, 1, None, LAPTOPS),
    ("Lenovo", "X1%", 3, 3, 3, 1, None, LAPTOPS),

    ("DELL", "%", 1, 1, 1, 1,
     "Consumer lines such as Inspiron carry one year. Latitude and OptiPlex "
     "business machines commonly ship with three years of ProSupport, which "
     "covers the hardware as well as the support.", LAPTOPS),
    ("DELL", "LATITUDE%", 3, 3, 3, 1, None, LAPTOPS),
    ("DELL", "OPTIPLEX%", 3, 3, 3, 1, None, LAPTOPS),
    ("DELL", "PRECISION%", 3, 3, 3, 1, None, LAPTOPS),

    ("HP", "%", 1, 1, 1, 1,
     "Consumer lines carry one year. EliteBook and ProBook business machines "
     "carry longer, and the exact term depends on what was bought with it.",
     LAPTOPS),
    ("HP", "ELITEBOOK%", 3, 3, 3, 1, None, LAPTOPS),

    ("ASUS", "%", 1, 1, 1, 1,
     "One year on consumer lines. ExpertBook business machines carry longer.",
     LAPTOPS),
    ("ASUS", "EXPERTBOOK%", 3, 3, 3, 1, None, LAPTOPS),

    ("Acer", "%", 1, 1, 1, 1, None, LAPTOPS),
    ("MSI", "%", 1, 1, 1, 1, None, LAPTOPS),
    ("AORUS", "%", 1, 1, 1, 1, None, LAPTOPS),
]

# Excluded from every warranty in the set above. The door gasket is the one
# that matters: it is a common call, it is on the shelf, and it is chargeable
# on a machine that is otherwise entirely covered.
WEAR = [
    ("gasket", "consumable wear item, excluded from every published warranty "
               "in this trade", GUIDE),
    # IT wear items. A laptop battery is the door gasket of this trade: it is
    # the commonest thing to fail, everybody assumes it is covered, and it is
    # consistently excluded or covered for a shorter term as a consumable.
    ("battery", "a consumable. Batteries wear by design and are excluded from "
                "the standard term or covered for a shorter one on every "
                "major brand", LAPTOPS),
    ("keyboard", "wear item on most business warranties", LAPTOPS),
    ("toner", "a consumable, not a fault", LAPTOPS),
    ("drum", "a consumable on a printer, replaced on a schedule", LAPTOPS),
    ("seal", "the same item under the other name customers use for it", GUIDE),
    ("light bulb", "consumable, excluded", GUIDE),
    ("lamp", "consumable, excluded", GUIDE),
    ("bulb", "consumable, excluded", GUIDE),
    ("shelf pin", "ordinary wear item, excluded", GUIDE),
    ("filter", "a maintenance item, replaced on a schedule rather than because "
               "it failed", GUIDE),
]


def load() -> dict:
    db.init()
    with db.txn() as c:
        for row in TERMS:
            c.execute(
                """INSERT OR REPLACE INTO warranty_terms
                   (manufacturer, series, parts_years, labour_years,
                    compressor_years, compressor_labour_covered,
                    condition_note, source_url, read_on)
                   VALUES (?,?,?,?,?,?,?,?,?)""", (*row, READ_ON))
        for pattern, why, url in WEAR:
            c.execute(
                "INSERT OR REPLACE INTO wear_items (pattern, why, source_url) "
                "VALUES (?,?,?)", (pattern, why, url))

    with db.connect() as c:
        terms = c.execute("SELECT COUNT(*) n FROM warranty_terms").fetchone()["n"]
        wear = c.execute("SELECT COUNT(*) n FROM wear_items").fetchone()["n"]

        # How much of our own book these terms actually reach. A loader that
        # covers four brands out of forty is worth knowing about before it is
        # relied on in a quote.
        reach = c.execute(
            """SELECT COUNT(*) n FROM assets a
               WHERE EXISTS (SELECT 1 FROM warranty_terms w
                             WHERE w.manufacturer = a.manufacturer)""").fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM assets").fetchone()["n"]

    return {"terms": terms, "wear_items": wear,
            "assets_reached": reach, "assets_total": total}


if __name__ == "__main__":
    out = load()
    print(f"{out['terms']} published warranty terms, {out['wear_items']} wear items")
    print(f"they reach {out['assets_reached']} of {out['assets_total']} machines "
          f"on the book")
