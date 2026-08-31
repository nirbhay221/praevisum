"""Give the furniture and audio-visual companies a book of business.

WHY THEY WERE EMPTY

Two of the four companies were seeded one at a time, each by its own script:
seed_business.py for refrigeration and seed_it_dealer.py for IT. Furniture and
audio-visual were added later, by add_vendors.py, which loads a CATALOGUE. Both
ended up with more products than either of the older two (278 and 272 against
212 and 161), a parts shelf and engineers, and not one customer.

So they could quote all day and could not take an order, because an order
attaches to an account and there were none. A complaint had nowhere to land
either: no sites, no machines, no history.

WHAT THIS INVENTS AND WHAT IT DOES NOT

The customers are fiction. The MACHINES ARE NOT: every asset is drawn from
that company's own product_stock, so a caller asking about a chair they own is
asking about a chair we actually sell, and the model numbers match the
catalogue rather than being invented alongside it.

History is thinner than refrigeration's on purpose. A furniture dealer does
not generate 434 service calls, and inventing that many would make the numbers
look impressive while lying about the trade.

    .venv/Scripts/python.exe scripts/seed_the_quiet_two.py
"""

from __future__ import annotations

import random
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

RNG = random.Random(20260831)

# The same map the other two companies sit on, so dispatch and drive times
# mean something across all four rather than only the older half.
TOWNS = [
    ("Davenport", "IA", 41.5236, -90.5776),
    ("Bettendorf", "IA", 41.5253, -90.5151),
    ("Moline", "IL", 41.5067, -90.5151),
    ("Rock Island", "IL", 41.5095, -90.5787),
    ("East Moline", "IL", 41.5095, -90.4443),
    ("Silvis", "IL", 41.5117, -90.4154),
    ("Eldridge", "IA", 41.6567, -90.5748),
    ("Milan", "IL", 41.4506, -90.5690),
]

STREETS = ["Brady St", "Harrison St", "Kimberly Rd", "River Dr", "16th St",
           "Avenue of the Cities", "5th Ave", "State St", "Locust St",
           "Middle Rd", "53rd St", "Blackhawk Rd"]

# Who actually buys office furniture and who actually buys AV kit. Kept apart
# because a school district and a law firm do not want the same things, and a
# book where every customer is interchangeable teaches the desk nothing.
BUYERS = {
    "D-FURN": [
        ("{} Law Group", "business"), ("{} Dental", "business"),
        ("{} Insurance Agency", "business"), ("{} Public Library", "business"),
        ("{} Community Bank", "business"), ("{} Accounting", "business"),
        ("{} Realty", "business"), ("{} Medical Clinic", "business"),
        ("{} Coworking", "business"), ("{} School District", "business"),
        ("{} Credit Union", "business"), ("{} Architects", "business"),
    ],
    "D-AV": [
        ("{} Conference Center", "business"), ("{} Baptist Church", "business"),
        ("{} High School", "business"), ("{} Sports Bar", "business"),
        ("{} Hotel", "business"), ("{} Theatre", "business"),
        ("{} Event Hall", "business"), ("{} Casino", "business"),
        ("{} Fitness Club", "business"), ("{} City Council", "business"),
        ("{} Brewing Co", "business"), ("{} Auditorium", "business"),
    ],
}

FIRST = ["Ada", "Marcus", "Elena", "Dev", "Priya", "Owen", "Nina", "Curtis",
         "Rosa", "Ibrahim", "Grace", "Tomas", "Fiona", "Hank", "Leah", "Omar"]
LAST = ["Brady", "Whitaker", "Ortiz", "Nakamura", "Delgado", "Hoffman",
        "Okafor", "Lindqvist", "Vasquez", "Mbeki", "Carlisle", "Rourke"]

# What goes wrong with this kind of kit. Deliberately not refrigeration
# symptoms: a chair does not lose refrigerant and a projector does not ice up.
SYMPTOMS = {
    "D-FURN": ["gas lift will not hold height", "castor sheared off",
               "seat fabric splitting at the seam", "desk frame wobbles",
               "drawer runner jammed", "height adjustment stuck",
               "armrest cracked", "laminate lifting at the edge"],
    "D-AV": ["projector shows a red tint", "no signal over HDMI",
             "lamp hours exhausted", "speaker buzzes at volume",
             "screen will not retract", "microphone drops out intermittently",
             "fan noise louder than spec", "image out of focus at one corner"],
}


def _addr() -> tuple[str, float, float]:
    town, st, lat, lon = RNG.choice(TOWNS)
    n = RNG.randint(100, 4800)
    street = RNG.choice(STREETS)
    # Jittered so sites are not all stacked on the town centre, which would
    # make every drive time identical and nearest-engineer meaningless.
    return (f"{n} {street}, {town} {st}",
            round(lat + RNG.uniform(-0.03, 0.03), 6),
            round(lon + RNG.uniform(-0.03, 0.03), 6))


def _stock_for(dealer: str) -> list:
    with db.connect() as c:
        return c.execute(
            """SELECT manufacturer, model_number, family FROM product_stock
               WHERE dealer_id = ? AND model_number IS NOT NULL
                 AND TRIM(COALESCE(manufacturer,'')) != ''""",
            (dealer,)).fetchall()


def seed(dealer: str, how_many: int) -> dict:
    stock = _stock_for(dealer)
    if not stock:
        return {"dealer": dealer, "why": "no catalogue to draw machines from"}

    names = BUYERS[dealer]
    symptoms = SYMPTOMS[dealer]
    made = {"accounts": 0, "sites": 0, "assets": 0, "work_orders": 0}
    today = date.today()

    with db.txn() as c:
        for i in range(how_many):
            pattern, kind = names[i % len(names)]
            town = RNG.choice(TOWNS)[0]
            name = pattern.format(town if i < len(names) else RNG.choice(LAST))

            aid = f"A-{dealer[2:5]}-{uuid.uuid4().hex[:6].upper()}"
            opened = today - timedelta(days=RNG.randint(200, 2600))
            c.execute(
                """INSERT INTO accounts (id,dealer_id,kind,name,opened_on,
                                         trade_terms)
                   VALUES (?,?,?,?,?,?)""",
                (aid, dealer, kind, name, opened.isoformat(),
                 RNG.choice(["net 30", "net 30", "net 15", "on account"])))
            made["accounts"] += 1

            for s in range(RNG.choice([1, 1, 1, 2])):
                addr, lat, lon = _addr()
                sid = f"S-{uuid.uuid4().hex[:8].upper()}"
                c.execute(
                    """INSERT INTO sites (id,account_id,label,address,lat,lon)
                       VALUES (?,?,?,?,?,?)""",
                    (sid, aid, "main office" if s == 0 else "second site",
                     addr, lat, lon))
                made["sites"] += 1

                who = f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
                c.execute(
                    """INSERT INTO contacts (id,account_id,site_id,name,role,
                                             channel_pref)
                       VALUES (?,?,?,?,?,?)""",
                    (f"C-{uuid.uuid4().hex[:8].upper()}", aid, sid, who,
                     RNG.choice(["owner", "office manager", "facilities",
                                 "operations"]),
                     RNG.choice(["sms", "email", "phone"])))

                for _ in range(RNG.randint(2, 5)):
                    m = RNG.choice(stock)
                    installed = today - timedelta(days=RNG.randint(30, 2200))
                    asset = f"AS-{uuid.uuid4().hex[:8].upper()}"
                    c.execute(
                        """INSERT INTO assets (id,site_id,manufacturer,
                              model_number,family,installed_on,
                              installed_source,location_note)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (asset, sid, m["manufacturer"], m["model_number"],
                         m["family"], installed.isoformat(), "sold_by_us",
                         RNG.choice(["reception", "boardroom", "main hall",
                                     "open plan", "training room"])))
                    made["assets"] += 1

                    # Only a minority have ever needed anything. Furniture and
                    # AV break far less often than refrigeration, and a book
                    # where every item carries a fault history is not this
                    # trade.
                    if RNG.random() < 0.28:
                        opened_at = datetime.now() - timedelta(
                            days=RNG.randint(5, 900))
                        closed = RNG.random() < 0.85
                        c.execute(
                            """INSERT INTO work_orders (id,account_id,site_id,
                                  asset_id,reported_symptom,status,opened_at,
                                  closed_at,dealer_id)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (f"WO-{uuid.uuid4().hex[:8].upper()}", aid, sid,
                             asset, RNG.choice(symptoms),
                             "closed" if closed else "open",
                             opened_at.isoformat(timespec="seconds"),
                             (opened_at + timedelta(days=RNG.randint(1, 9))
                              ).isoformat(timespec="seconds") if closed
                             else None,
                             dealer))
                        made["work_orders"] += 1

    return {"dealer": dealer, **made}


def main() -> None:
    with db.connect() as c:
        already = {r[0]: r[1] for r in c.execute(
            "SELECT dealer_id, COUNT(*) FROM accounts GROUP BY dealer_id")}

    for dealer, n in (("D-FURN", 26), ("D-AV", 22)):
        if already.get(dealer):
            print(f"  {dealer} already has {already[dealer]} accounts, "
                  "leaving it alone")
            continue
        print(" ", seed(dealer, n))

    with db.connect() as c:
        print()
        for d in ("D-REF", "D-IT", "D-FURN", "D-AV"):
            a = c.execute("SELECT COUNT(*) FROM accounts WHERE dealer_id=?",
                          (d,)).fetchone()[0]
            s = c.execute("""SELECT COUNT(*) FROM sites s JOIN accounts a
                             ON a.id=s.account_id WHERE a.dealer_id=?""",
                          (d,)).fetchone()[0]
            x = c.execute("""SELECT COUNT(*) FROM assets x
                             JOIN sites s ON s.id=x.site_id
                             JOIN accounts a ON a.id=s.account_id
                             WHERE a.dealer_id=?""", (d,)).fetchone()[0]
            print(f"  {d:8} accounts={a:<5} sites={s:<5} machines={x}")


if __name__ == "__main__":
    main()


def qualify(dealer: str) -> dict:
    """Give a company's engineers the skills for what that company sells.

    WHY THIS WAS MISSING AND WHAT IT COST

    `next_available_slot` will not offer a technician who is not qualified on
    the equipment family, which is right: sending somebody who cannot work on
    an ice machine to an ice machine is a wasted truck roll and, for anything
    with refrigerant in it, illegal.

    Refrigeration and IT were seeded with skills. Furniture and audio-visual
    were not, so their engineers were qualified on nothing, and the scheduler
    answered every request the same way:

        nobody is qualified on office chair

    A customer could be sold a chair, report a fault, have a work order opened
    against it, and never be offered a visit -- because the check that exists
    to stop the wrong engineer being sent had nobody to compare against.

    Spread rather than given to everyone: a crew where every member can do
    every job makes the qualification check meaningless, and the whole point
    of `next_available_slot` is that WHO can go is a real constraint.
    """
    with db.connect() as c:
        crew = [r[0] for r in c.execute(
            "SELECT id FROM technicians WHERE dealer_id = ? ORDER BY id",
            (dealer,))]
        families = [r[0] for r in c.execute(
            """SELECT DISTINCT family FROM product_stock
               WHERE dealer_id = ? AND family IS NOT NULL AND family != ''
               ORDER BY family""", (dealer,))]

    if not crew or not families:
        return {"dealer": dealer, "why": "no crew or no families"}

    made = 0
    with db.txn() as c:
        for i, fam in enumerate(families):
            # Everything is covered by at least two people, so one engineer
            # being busy does not make a whole family unserviceable.
            for who in {crew[i % len(crew)], crew[(i + 1) % len(crew)]}:
                try:
                    c.execute(
                        "INSERT INTO technician_skills (technician_id, family) "
                        "VALUES (?,?)", (who, fam))
                    made += 1
                except Exception:
                    pass          # already qualified
    return {"dealer": dealer, "crew": len(crew), "families": len(families),
            "skills_added": made}


def qualify_everyone() -> None:
    for d in ("D-FURN", "D-AV", "D-IT", "D-REF"):
        print(" ", qualify(d))


def put_the_crew_on_the_map() -> None:
    """Give every engineer a position, because dispatch is a distance problem.

    The furniture and audio-visual crews were created with a home_base town
    and no coordinates. `next_available_slot` ranks by drive time, so an
    engineer with no position cannot be compared to one with a position and
    drops out of every search. The scheduler then said

        no qualified technician has a free slot in that window

    which is true and misleading: they were free, they were qualified, and
    they were invisible.

    Placed on the town they are actually based in, with a small offset so two
    engineers in the same town are not at identical coordinates.
    """
    here = {t[0]: (t[2], t[3]) for t in TOWNS}

    with db.connect() as c:
        crew = [dict(r) for r in c.execute(
            """SELECT id, name, home_base, dealer_id FROM technicians
               WHERE lat IS NULL OR lon IS NULL""")]

    if not crew:
        print("  every engineer already has a position")
        return

    placed = 0
    with db.txn() as c:
        for t in crew:
            lat, lon = here.get((t["home_base"] or "").strip(),
                                here["Davenport"])
            c.execute("UPDATE technicians SET lat=?, lon=? WHERE id=?",
                      (round(lat + RNG.uniform(-0.02, 0.02), 6),
                       round(lon + RNG.uniform(-0.02, 0.02), 6), t["id"]))
            placed += 1
            print(f"    {t['dealer_id']:8} {t['name'][:22]:24} -> "
                  f"{t['home_base']}")
    print(f"  placed {placed} engineers")


def give_them_a_working_week() -> None:
    """Working hours, without which nobody is ever free.

    THE LAST OF THREE THINGS THE TWO NEW COMPANIES WERE MISSING.

    `next_available_slot` walks forward day by day and skips any day the
    engineer has no hours for. With no rows at all it skips every day, finds
    nothing, and reports

        no qualified technician has a free slot in that window

    which reads like a busy team and means an empty table. Furniture and
    audio-visual had 0 rows between six engineers, so a furniture customer
    could be sold a chair, report a fault, and never be offered a visit.

    Monday to Friday, 8am to 5pm, in minutes from midnight, which is the shape
    the refrigeration crew already uses. Saturdays are left off deliberately:
    a scheduler that can always find a slot is not modelling anything.
    """
    with db.connect() as c:
        crew = [dict(r) for r in c.execute(
            """SELECT t.id, t.name, t.dealer_id FROM technicians t
               WHERE NOT EXISTS (SELECT 1 FROM technician_hours h
                                 WHERE h.technician_id = t.id)""")]
    if not crew:
        print("  everybody already has hours")
        return

    made = 0
    with db.txn() as c:
        for t in crew:
            for dow in range(5):                       # Monday to Friday
                # A little spread so the whole crew is not identical: one
                # starts early, one finishes late, which is what makes an
                # early slot and a late slot different answers.
                start = 480 + (30 * (hash(t["id"]) % 3))     # 8:00 to 9:00
                end = 1020 + (30 * ((hash(t["id"]) + 1) % 3))  # 17:00 to 18:00
                try:
                    c.execute(
                        """INSERT INTO technician_hours
                           (technician_id, dow, start_min, end_min)
                           VALUES (?,?,?,?)""", (t["id"], dow, start, end))
                    made += 1
                except Exception:
                    pass
            print(f"    {t['dealer_id']:8} {t['name'][:22]:24} Mon-Fri")
    print(f"  {made} working days written")
