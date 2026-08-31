"""Parts for the two vendors that could sell but never repair.

THE HOLE

Counted across the four businesses on this desk:

    D-REF    parts=10   technicians=8   stock=212
    D-IT     parts=10   technicians=5   stock=161
    D-FURN   parts=0    technicians=3   stock=278
    D-AV     parts=0    technicians=3   stock=272

Furniture and AV could sell a chair or a display and send somebody to look at
one, and had nothing to fit when they got there. Every parts tool asked about
them returned empty, so the desk correctly said it could not help and the call
died there.

Worse, the data contradicted the instructions. The furniture trade note tells
the desk that "frames, mechanisms, fabric, castors and gas lifts all carry
different terms from the same maker", and not one of those existed as a part,
so the desk was primed to discuss components the company did not stock. The AV
note talks about a projector lamp being "a consumable with a rated life in
hours", which is the single most ordered part in that trade, and it was absent
too.

WHAT IS SEEDED AND ON WHAT BASIS

The wear items each trade actually replaces, with prices in the range those
parts really sell for and lead times that reflect whether a thing is a stock
commodity or comes from the maker. A gas lift is a fast commodity; a specific
chair mechanism is not. A projector lamp is the most ordered AV part there is
and is held; a replacement panel is not held by anybody.

`families` is filled on every row, because the fitment guard reads it and a
part with no family can be offered for a machine it does not fit.

    python -m scripts.seed_trade_parts
"""

from __future__ import annotations

from src import db

# sku, name, unit cost, lead time days, families it fits, qty to put on the shelf
FURNITURE = [
    ("FURN-GASLIFT", "Gas lift cylinder", 38.50, 2, "office chair", 14),
    ("FURN-CASTORSET", "Castor set, five", 22.00, 2, "office chair", 20),
    ("FURN-ARMPAD", "Armrest pad, pair", 27.90, 5, "office chair", 8),
    ("FURN-TILTMECH", "Tilt mechanism", 96.00, 12, "office chair", 3),
    ("FURN-SEATFOAM", "Seat foam and cover", 74.50, 14, "office chair", 2),
    ("FURN-DRAWERSLIDE", "Drawer slide pair", 31.00, 4, "filing cabinet,desk", 11),
    ("FURN-LEVELFOOT", "Levelling foot set", 14.25, 2, "desk,conference table,shelving unit", 25),
    ("FURN-CABLEGROM", "Cable grommet", 8.40, 3, "desk,conference table", 30),
]

AUDIOVISUAL = [
    ("AV-PROJLAMP", "Projector lamp module", 189.00, 3, "projector", 9),
    ("AV-AIRFILTER", "Projector air filter", 24.00, 3, "projector", 16),
    ("AV-WALLMOUNT", "Tilting wall mount bracket", 68.00, 2, "television,commercial display", 12),
    ("AV-PSU", "Display power supply board", 142.00, 9, "television,commercial display", 4),
    ("AV-REMOTE", "Replacement remote handset", 29.50, 4, "television,commercial display,projector", 15),
    ("AV-HDMI5M", "HDMI lead, five metre", 18.75, 1, "television,commercial display,projector,digital signage", 40),
    ("AV-SPKRDRIVER", "Speaker driver", 87.00, 11, "sound system", 3),
    ("AV-MEDIAPLAYER", "Signage media player", 214.00, 7, "digital signage", 2),
]


def load() -> dict:
    db.init()

    with db.connect() as c:
        where = c.execute(
            "SELECT id FROM stock_locations LIMIT 1").fetchone()
    location = where["id"] if where else None

    written = []
    with db.txn() as c:
        for dealer, rows in (("D-FURN", FURNITURE), ("D-AV", AUDIOVISUAL)):
            for sku, name, cost, lead, families, qty in rows:
                c.execute(
                    """INSERT INTO parts
                         (sku,name,unit_cost,lead_time_days,dealer_id,families)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(sku) DO UPDATE SET
                         name=excluded.name, unit_cost=excluded.unit_cost,
                         lead_time_days=excluded.lead_time_days,
                         families=excluded.families""",
                    (sku, name, cost, lead, dealer, families))

                if location and qty:
                    c.execute(
                        """INSERT INTO stock (location_id,sku,on_hand)
                           VALUES (?,?,?)
                           ON CONFLICT(location_id,sku) DO UPDATE SET
                             on_hand=excluded.on_hand""",
                        (location, sku, qty))
                written.append((dealer, sku, name, cost, qty))

    with db.connect() as c:
        counts = {d: c.execute("SELECT COUNT(*) n FROM parts WHERE dealer_id=?",
                               (d,)).fetchone()["n"]
                  for d in ("D-REF", "D-IT", "D-FURN", "D-AV")}

    return {"written": written, "counts": counts, "location": location}


if __name__ == "__main__":
    out = load()
    for dealer, sku, name, cost, qty in out["written"]:
        print(f"  {dealer:<8} {sku:<18} {name[:32]:<32} {cost:>7.2f}  x{qty}")
    print()
    print("  parts per business now: "
          + ", ".join(f"{d} {n}" for d, n in out["counts"].items()))
    if not out["location"]:
        print("  no stock location on file, so nothing was put on a shelf")
