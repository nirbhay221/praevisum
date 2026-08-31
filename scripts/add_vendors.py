"""Two more businesses behind the same phone number.

WHY THIS IS A SCRIPT AND NOT A REWRITE

Because the single-front design was built so that adding a vendor is data, not
code. A caller rings one number, says what they want, and route_to_vendor
reads `dealers.families` to decide whose stock, technicians, rates, warranty
terms and repair history apply. Nothing in the desk names a trade.

So a new business needs five real things and no new logic:

    1. a dealers row, with the families it carries
    2. a trade_rates row, from BLS, for what that trade actually pays
    3. published warranty terms for the makes it sells
    4. the wear items that trade argues about
    5. stock, which scripts/stock_from_market.py already builds from families

THE WAGES ARE REAL AND THE TWO ARE NOT THE SAME KIND OF NUMBER

Both were read from the BLS public API, and the difference matters enough to
record on the row:

    49-9071  General Maintenance and Repair Workers
             Davenport-Moline-Rock Island metro, median $24.61/hr
             A LOCAL figure. The metro series exists for this occupation.

    49-2097  Electronic Home Entertainment Equipment Installers and Repairers
             Iowa, median $30.96/hr
             No metro series is published, so this is state-wide, exactly as
             it is for IT support. Quoting a state median as though it were
             local is the same quiet dishonesty as inventing one.

THE WARRANTY SPLIT THIS BRINGS IN

Every trade on this desk turns out to have the same argument in it, under
different words, and it is always the argument that loses a customer's trust:

    refrigeration   a domestic freezer in a commercial kitchen has no NSF
                    rating and a warranty void on business use
    IT              ThinkPad three years, IdeaPad one
    AV              a consumer television's warranty EXCLUDES commercial,
                    business and public display use, so the restaurant that
                    mounts one in the dining room has no cover at all
    furniture       Eurotech's lifetime term is SINGLE SHIFT. Herman Miller,
                    Steelcase and Humanscale cover 24/7. A chair in a
                    24-hour operation is a different warranty question

And every trade has its consumable that customers assume is covered and never
is: the door gasket, the laptop battery, the printer drum, and now the
projector lamp and the chair's gas cylinder and fabric.

Run: python -m scripts.add_vendors
"""

from __future__ import annotations

from src import db

# --- 1. The businesses -------------------------------------------------------

VENDORS = [
    ("D-FURN", "Prairie Contract Furnishings", "furniture", "+18573617167",
     "office chair,desk,conference table,filing cabinet,shelving unit",
     "America/Chicago"),
    ("D-AV", "Rock Island Display Systems", "av", "+18573617168",
     "television,commercial display,projector,sound system,digital signage",
     "America/Chicago"),
]

# The IT vendor sells accessories too, which is most of what a shop like that
# actually moves: cheap, fast, and reordered constantly.
IT_EXTRA = "monitor,docking station,headset"

# --- 2. What the work is worth, from BLS ------------------------------------

RATES = [
    # A metro figure: the series exists for this occupation here.
    ("furniture", "49-9071", "General Maintenance and Repair Workers",
     24.61, "OEUM001934000000049907108", "Davenport-Moline-Rock Island metro",
     2025, 2.1, 55.0),

    # State-wide, because BLS publishes no metro series for it, same as IT.
    ("av", "49-2097",
     "Electronic Home Entertainment Equipment Installers and Repairers",
     30.96, "OEUS190000000000049209708", "Iowa (no metro series is published)",
     2025, 2.3, 75.0),
]

# --- 3. Published warranty terms --------------------------------------------
#
# (manufacturer, series, parts_years, labour_years, condition_note, source)
#
# `series` uses SQL LIKE, so '%' means the whole range and a prefix narrows it
# to one line, which is how the ThinkPad and IdeaPad split is already stored.

BTOD = "https://www.btod.com/blog/best-office-chair-warranties/"
HM = "https://www.hermanmiller.com/customer-service/warranty-and-service/"
SAMSUNG = ("https://insights.samsung.com/2024/10/21/"
           "why-you-shouldnt-use-consumer-tvs-for-commercial-digital-signage/")
TELEMETRY = ("https://www.telemetrytv.com/posts/"
             "commercial-digital-signage-displays/")
EPSON = "https://epson.com/warranty-extended-service-plans"

TERMS = [
    # Furniture. The condition that matters is shifts, not years.
    ("Herman Miller", "%", 12.0, 12.0,
     "Twelve years including parts and labour, rated for 24/7 use, weight "
     "limit 300 to 350 lb depending on model. Cover requires the "
     "manufacturing label and purchase from an authorised dealer.", HM),
    ("Steelcase", "%", 12.0, 12.0,
     "Lifetime on the seating itself, twelve years on components, five to "
     "twelve on fabrics. Rated 24/7, 300 to 400 lb.", BTOD),
    ("Humanscale", "%", 15.0, 15.0,
     "Fifteen years on seating and components including casters, cylinders "
     "and mechanisms. Fabric, cushions and arm pads are five years and are "
     "rated SINGLE SHIFT only.", BTOD),
    ("Sitmatic", "%", 10.0, 10.0,
     "Lifetime on seating, ten years on components and fabrics, 24/7, and "
     "no weight limit at all.", BTOD),
    ("Eurotech", "%", 5.0, 5.0,
     "Lifetime on the seating sounds longer than it is: the term is SINGLE "
     "SHIFT. In a 24-hour operation it does not apply. Fabric is five years.",
     BTOD),

    # AV. The split here is the sharpest of any trade on this desk.
    ("Samsung", "commercial%", 3.0, 3.0,
     "Three years on commercial display and signage lines, which is the "
     "line intended for business use.", TELEMETRY),
    ("Samsung", "%", 1.0, 1.0,
     "One year, and the consumer television warranty EXCLUDES commercial, "
     "business and public display use. Mounted in a dining room or a shop "
     "floor it is void, so a consumer set is not the cheaper option it "
     "looks like. Say this before they buy, not after it fails.", SAMSUNG),
    ("LG", "commercial%", 3.0, 3.0,
     "Three years on the commercial display range.", TELEMETRY),
    ("LG", "%", 1.0, 1.0,
     "One year, and void on commercial or public display use, same as every "
     "consumer television line.", SAMSUNG),
    ("Sony", "%", 1.0, 1.0,
     "One year, and the consumer term excludes business use.", SAMSUNG),
    ("Epson", "%", 2.0, 2.0,
     "Two years on commercial projectors, three on some models. The LAMP is "
     "not covered for the same term as the projector: it is a consumable "
     "with its own much shorter term.", EPSON),
    ("BenQ", "%", 3.0, 3.0,
     "Three years on business projector lines, lamp excluded.",
     "https://www.benq.com/en-us/business/resource/trends/"
     "what-is-the-best-projector-warranty-available-in-2023-.html"),
]

# --- 4. What each trade argues about -----------------------------------------

WEAR = [
    ("lamp", "a projector lamp is a consumable with a rated life in hours. "
             "It is never covered for the projector's term and replacing one "
             "is a scheduled cost, not a fault", EPSON),
    ("bulb", "the same item under the other word customers use for it", EPSON),
    ("gas cylinder", "the pneumatic cylinder in a chair is a wear part on "
                     "most terms and the commonest thing to fail", BTOD),
    ("caster", "chair casters wear against the floor and are consumable, "
               "though Humanscale is a notable exception and covers them",
     BTOD),
    ("fabric", "upholstery carries a SHORTER term than the frame on every "
               "brand here, five to twelve years against twelve or lifetime, "
               "and is often single shift where the frame is 24/7", BTOD),
    ("arm pad", "a wear surface, five years where the chair is twelve",
     BTOD),
]


def load() -> dict:
    db.init()

    with db.txn() as c:
        for v in VENDORS:
            c.execute(
                """INSERT INTO dealers (id,name,trade,phone_e164,families,timezone)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, trade=excluded.trade,
                     phone_e164=excluded.phone_e164,
                     families=excluded.families, timezone=excluded.timezone""",
                v)

        # Widen the IT vendor rather than replacing what it already carries.
        row = c.execute("SELECT families FROM dealers WHERE id='D-IT'").fetchone()
        if row:
            have = [f.strip() for f in (row["families"] or "").split(",") if f.strip()]
            for extra in IT_EXTRA.split(","):
                if extra.strip() and extra.strip() not in have:
                    have.append(extra.strip())
            c.execute("UPDATE dealers SET families=? WHERE id='D-IT'",
                      (",".join(have),))

        for r in RATES:
            c.execute(
                """INSERT OR REPLACE INTO trade_rates
                   (trade,occupation,occupation_name,hourly_wage,series_id,
                    geography,year,multiplier,call_out)
                   VALUES (?,?,?,?,?,?,?,?,?)""", r)

        for make, series, parts, labour, note, url in TERMS:
            c.execute(
                """INSERT OR REPLACE INTO warranty_terms
                   (manufacturer,series,parts_years,labour_years,
                    compressor_years,compressor_labour_covered,
                    condition_note,source_url,read_on)
                   VALUES (?,?,?,?,NULL,0,?,?,date('now'))""",
                (make, series, parts, labour, note, url))

        for pattern, why, url in WEAR:
            c.execute(
                "INSERT OR REPLACE INTO wear_items (pattern,why,source_url) "
                "VALUES (?,?,?)", (pattern, why, url))

    with db.connect() as c:
        dealers = c.execute("SELECT id,name,trade,families FROM dealers "
                            "ORDER BY id").fetchall()
        rates = c.execute("SELECT COUNT(*) n FROM trade_rates").fetchone()["n"]
        terms = c.execute("SELECT COUNT(*) n FROM warranty_terms").fetchone()["n"]
        wear = c.execute("SELECT COUNT(*) n FROM wear_items").fetchone()["n"]

    return {"dealers": [dict(d) for d in dealers], "rates": rates,
            "terms": terms, "wear": wear}


if __name__ == "__main__":
    out = load()
    print(f"\n{len(out['dealers'])} businesses behind one number:")
    for d in out["dealers"]:
        print(f"  {d['id']:<8} {d['name'][:34]:<34} {d['trade']}")
        print(f"           {d['families']}")
    print(f"\n{out['rates']} trade rates, {out['terms']} warranty terms, "
          f"{out['wear']} wear items")
