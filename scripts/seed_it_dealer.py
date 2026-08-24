"""A second, entirely separate business on the same software.

Quad City IT Services does onsite warranty work on laptops, desktops and
servers. They have never touched a walk-in cooler and never will. Different
customers, different engineers, different parts, different phone number.

The point of seeding them is not breadth for its own sake. It is that the
engine has no idea what a refrigerator is. It works on

    (manufacturer, model, symptom) -> what actually fixed it -> what fits

and that sentence is as true of a Lenovo ThinkPad under a next-business-day
warranty as it is of a Traulsen freezer. Dell ProSupport dispatches an engineer
with parts chosen from a remote diagnosis, and the first thing that surfaces
when you search for complaints about it is dispatch sending the wrong part and
the engineer being unable to finish. Same problem, different industry.

What the two dealers share: the public equipment catalogue, because it is
federal certification data about machines that exist in the world.

What they never share: the repair corpus. What breaks and what fixed it is the
accumulated experience of one company's own engineers, and it is the whole
reason this software is worth paying for.

    .venv/Scripts/python.exe scripts/seed_it_dealer.py
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

RNG = random.Random(4242)
DEALER = "D-IT"

TOWNS = [
    ("Moline", "IL", 41.5067, -90.5151), ("Rock Island", "IL", 41.5095, -90.5787),
    ("Davenport", "IA", 41.5236, -90.5776), ("Bettendorf", "IA", 41.5253, -90.5151),
    ("Silvis", "IL", 41.5117, -90.4151), ("Geneseo", "IL", 41.4478, -90.1548),
]

# who buys onsite IT warranty support: offices, clinics, schools, small firms
BUSINESS = [
    ("{} Law Group", "legal"), ("{} Dental", "clinic"), ("{} Accounting", "finance"),
    ("{} Insurance Agency", "finance"), ("{} Engineering", "professional"),
    ("{} Family Practice", "clinic"), ("{} Realty", "professional"),
    ("{} Community College", "education"), ("{} Credit Union", "finance"),
    ("{} Logistics", "logistics"), ("{} Architects", "professional"),
    ("{} Veterinary Clinic", "clinic"), ("{} Staffing", "professional"),
    ("{} Public Library", "education"), ("{} Physical Therapy", "clinic"),
]
PLACES = ["Riverbend", "Cornerstone", "Meridian", "Bluffside", "Wildwood", "Copperfield",
          "Harborview", "Stonebridge", "Westgate", "Ashland", "Fairmount", "Trinity",
          "Sycamore", "Kingsley", "Redwood", "Halloran", "Pinehurst", "Brookfield"]

FIRST = ["Anika", "Roy", "Talia", "Marcus", "Jen", "Oscar", "Priya", "Devon", "Claire",
         "Hugo", "Nia", "Ravi", "Elena", "Tomas", "Grace", "Sean", "Mira", "Colin"]
LAST = ["Barros", "Nyquist", "Adeyemi", "Sandoval", "Kirby", "Chaudhry", "Lindholm",
        "Osei", "Fabbri", "Waller", "Novak", "Ivers", "Domingo", "Sato", "Reddick"]
ROLES = ["office manager", "IT coordinator", "practice manager", "operations lead",
         "principal", "managing partner", "facilities lead"]
STREETS = ["Main St", "18th Ave", "River Dr", "Kimberly Rd", "Brady St", "7th Ave",
           "Blackhawk Rd", "State St"]

# family -> catalogue category, how common in an office
FAMILIES = [
    ("laptop", "it", "%Notebook%", 40),
    ("desktop", "it", "%Desktop%", 22),
    ("server", "it", "%Server%", 8),
    ("printer", "it", "%", 16),
    ("ups", "it", "%", 14),
]

# what actually goes wrong with onsite-warranty IT hardware, and how often the
# first visit fails because the engineer did not have the part
FAULTS = [
    ("screen has gone black but you can hear it running",
     "LCD panel failed, backlight dead. Replaced the display assembly",
     ["lcd-panel"], 0.35),
    ("it will not hold a charge, dies within twenty minutes",
     "battery swollen and below 40% design capacity, replaced",
     ["battery"], 0.20),
    ("keeps shutting itself off under load",
     "thermal shutdown, fan seized and heatsink packed with dust",
     ["fan-assembly"], 0.25),
    ("blue screen on startup, will not boot",
     "SSD failing SMART, cloned and replaced the drive",
     ["ssd"], 0.40),
    ("a few keys have stopped working",
     "liquid damage under the keyboard membrane, replaced keyboard",
     ["keyboard"], 0.18),
    ("running incredibly slowly since the last update",
     "RAM module intermittent, failed extended memory test, replaced",
     ["ram"], 0.30),
    ("will not connect to wifi anywhere",
     "wireless card failed, replaced the M.2 module",
     ["wifi-card"], 0.28),
    ("the power light comes on but nothing else happens",
     "motherboard fault, no POST. Board swap under warranty",
     ["mainboard"], 0.55),
    ("battery backup beeps constantly and shows a fault",
     "UPS battery pack past end of life, replaced the cartridge",
     ["ups-battery"], 0.15),
    ("printer keeps jamming and streaking the page",
     "fuser assembly worn past service life, replaced",
     ["fuser"], 0.22),
]

PART_KINDS = {
    "lcd-panel": ("LCD display assembly", 218.00, 3),
    "battery": ("Replacement battery", 96.50, 2),
    "fan-assembly": ("Fan and heatsink assembly", 74.20, 3),
    "ssd": ("NVMe solid state drive", 128.00, 1),
    "keyboard": ("Keyboard assembly", 88.40, 4),
    "ram": ("SODIMM memory module", 61.00, 1),
    "wifi-card": ("M.2 wireless card", 39.90, 2),
    "mainboard": ("System mainboard", 486.00, 8),
    "ups-battery": ("UPS battery cartridge", 172.00, 4),
    "fuser": ("Fuser assembly", 245.00, 6),
}

ENGINEERS = [
    ("Roy Nyquist", "Davenport", ["laptop", "desktop", "printer"]),
    ("Anika Barros", "Moline", ["laptop", "desktop", "server", "ups"]),
    ("Devon Kirby", "Bettendorf", ["laptop", "printer", "ups"]),
    ("Priya Chaudhry", "Rock Island", ["laptop", "desktop", "server"]),
    ("Tomas Fabbri", "Silvis", ["desktop", "printer", "ups"]),
]


def nid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:6].upper()}"


def jitter(lat, lon):
    return round(lat + RNG.uniform(-.02, .02), 4), round(lon + RNG.uniform(-.02, .02), 4)


def build() -> None:
    with db.txn() as c:
        c.execute("PRAGMA defer_foreign_keys = ON")
        # clear only this dealer, never the other one
        c.execute("""DELETE FROM parts_used WHERE visit_id IN
                     (SELECT v.id FROM visits v JOIN work_orders w ON w.id=v.work_order_id
                      WHERE w.dealer_id=?)""", (DEALER,))
        c.execute("""DELETE FROM visits WHERE work_order_id IN
                     (SELECT id FROM work_orders WHERE dealer_id=?)""", (DEALER,))
        c.execute("DELETE FROM repairs WHERE dealer_id=?", (DEALER,))
        c.execute("DELETE FROM work_orders WHERE dealer_id=?", (DEALER,))
        c.execute("""DELETE FROM assets WHERE site_id IN
                     (SELECT s.id FROM sites s JOIN accounts a ON a.id=s.account_id
                      WHERE a.dealer_id=?)""", (DEALER,))
        c.execute("""DELETE FROM phones WHERE contact_id IN
                     (SELECT ct.id FROM contacts ct JOIN accounts a ON a.id=ct.account_id
                      WHERE a.dealer_id=?)""", (DEALER,))
        c.execute("""DELETE FROM contacts WHERE account_id IN
                     (SELECT id FROM accounts WHERE dealer_id=?)""", (DEALER,))
        c.execute("""DELETE FROM sites WHERE account_id IN
                     (SELECT id FROM accounts WHERE dealer_id=?)""", (DEALER,))
        c.execute("DELETE FROM accounts WHERE dealer_id=?", (DEALER,))
        c.execute("""DELETE FROM technician_skills WHERE technician_id IN
                     (SELECT id FROM technicians WHERE dealer_id=?)""", (DEALER,))
        c.execute("""DELETE FROM stock WHERE location_id IN
                     (SELECT id FROM stock_locations WHERE dealer_id=?)""", (DEALER,))
        c.execute("DELETE FROM technicians WHERE dealer_id=?", (DEALER,))
        c.execute("DELETE FROM stock_locations WHERE dealer_id=?", (DEALER,))
        c.execute("DELETE FROM fitments WHERE sku IN (SELECT sku FROM parts WHERE dealer_id=?)",
                  (DEALER,))
        c.execute("DELETE FROM parts WHERE dealer_id=?", (DEALER,))

        c.execute("""INSERT OR REPLACE INTO dealers
                     (id,name,trade,phone_e164,greeting_name,families)
                     VALUES (?,?,?,?,?,?)""",
                  (DEALER, "Quad City IT Services", "it", "+18573617166",
                   "Quad City I T Services",
                   "laptop,desktop,server,printer,ups"))

        # parts
        parts = {}
        for kind, (name, cost, lead) in PART_KINDS.items():
            sku = f"IT-{kind.upper().replace('-','')[:10]}"
            parts[kind] = sku
            c.execute("""INSERT INTO parts (sku,name,unit_cost,lead_time_days,dealer_id)
                         VALUES (?,?,?,?,?)""", (sku, name, cost, lead, DEALER))

        c.execute("""INSERT INTO stock_locations (id,kind,label,mobile,dealer_id)
                     VALUES (?,?,?,?,?)""", ("LOC-IT-WH", "warehouse", "Davenport depot", 0, DEALER))
        for sku in parts.values():
            c.execute("INSERT INTO stock (location_id,sku,on_hand) VALUES (?,?,?)",
                      ("LOC-IT-WH", sku, RNG.randint(2, 8)))

        # engineers
        eng = []
        for name, town, skills in ENGINEERS:
            tid = nid("T")
            eng.append((tid, skills))
            base = next(t for t in TOWNS if t[0] == town)
            lat, lon = jitter(base[2], base[3])
            van = f"VAN-{tid[-4:]}"
            c.execute("""INSERT INTO stock_locations (id,kind,label,mobile,dealer_id)
                         VALUES (?,?,?,?,?)""",
                      (van, "van", f"{name.split()[0]} car", 1, DEALER))
            c.execute("""INSERT INTO technicians
                         (id,name,phone,home_base,lat,lon,van_location,dealer_id)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (tid, name, f"+1309556{RNG.randint(1000,9999)}",
                       f"{town} {base[1]}", lat, lon, van, DEALER))
            c.executemany("INSERT INTO technician_skills (technician_id,family) VALUES (?,?)",
                          [(tid, s) for s in skills])
            for d in range(5):
                c.execute("INSERT OR IGNORE INTO technician_hours VALUES (?,?,?,?)",
                          (tid, d, 480, 1020))
            for sku in RNG.sample(list(parts.values()), 3):
                c.execute("INSERT INTO stock (location_id,sku,on_hand) VALUES (?,?,?)",
                          (van, sku, 1))

        # customers, sites, people, hardware
        assets, contacts_by_acc, used = [], {}, set()
        weights = [f[3] for f in FAMILIES]
        for _ in range(34):
            pat, _kind = RNG.choice(BUSINESS)
            name = pat.format(RNG.choice(PLACES))
            while name in used:
                pat, _kind = RNG.choice(BUSINESS)
                name = pat.format(RNG.choice(PLACES))
            used.add(name)
            town = RNG.choice(TOWNS)
            acc = nid("A")
            c.execute("""INSERT INTO accounts (id,kind,name,trade_terms,opened_on,dealer_id)
                         VALUES (?,?,?,?,?,?)""",
                      (acc, "business", name, "net 30",
                       (date(2026, 8, 1) - timedelta(days=RNG.randint(200, 1800))).isoformat(),
                       DEALER))
            sid = nid("S")
            lat, lon = jitter(town[2], town[3])
            c.execute("""INSERT INTO sites (id,account_id,label,address,lat,lon)
                         VALUES (?,?,?,?,?,?)""",
                      (sid, acc, name,
                       f"{RNG.randint(10,3200)} {RNG.choice(STREETS)}, {town[0]} {town[1]}",
                       lat, lon))
            cids = []
            for _k in range(RNG.choice([1, 1, 2])):
                cid = nid("C")
                cids.append(cid)
                c.execute("""INSERT INTO contacts (id,account_id,site_id,name,role,channel_pref)
                             VALUES (?,?,?,?,?,?)""",
                          (cid, acc, sid, f"{RNG.choice(FIRST)} {RNG.choice(LAST)}",
                           RNG.choice(ROLES), "email"))
                c.execute("INSERT INTO phones (e164,contact_id,label,verified) VALUES (?,?,?,?)",
                          (f"+1563555{RNG.randint(1000,8999)}", cid, "mobile", 1))
            contacts_by_acc[acc] = cids

            for fam, cat, like, _w in RNG.choices(FAMILIES, weights=weights,
                                                  k=RNG.randint(2, 9)):
                pick = c.execute(
                    """SELECT brand, model_number FROM equipment
                       WHERE site_visit=1 AND category=? AND product_type LIKE ?
                       GROUP BY brand, model_number ORDER BY RANDOM() LIMIT 1""",
                    (cat, like)).fetchone()
                if not pick:
                    continue
                aid = nid("AST")
                c.execute("""INSERT INTO assets
                    (id,site_id,manufacturer,model_number,family,installed_on,location_note)
                    VALUES (?,?,?,?,?,?,?)""",
                    (aid, sid, pick["brand"], pick["model_number"], fam,
                     (date(2026, 8, 1) - timedelta(days=RNG.randint(90, 1600))).isoformat(),
                     RNG.choice(["reception", "back office", "server closet",
                                 "consulting room", "front desk", "workshop"])))
                assets.append({"id": aid, "site": sid, "account": acc,
                               "brand": pick["brand"], "model": pick["model_number"],
                               "family": fam})

        c.execute("""UPDATE assets SET equipment_id=(SELECT e.id FROM equipment e
                     WHERE e.brand=assets.manufacturer AND e.model_number=assets.model_number
                     LIMIT 1) WHERE equipment_id IS NULL""")

        seen = set()
        for a in assets:
            for sku in parts.values():
                key = (sku, a["brand"], a["model"])
                if key not in seen:
                    seen.add(key)
                    c.execute("""INSERT OR IGNORE INTO fitments
                                 (sku,manufacturer,model_pattern,source)
                                 VALUES (?,?,?,?)""",
                              (sku, a["brand"], a["model"], "dealer"))

        # two years of warranty callouts
        jobs = 0
        for _ in range(240):
            a = RNG.choice(assets)
            symptom, cause, kinds, fail = RNG.choice(FAULTS)
            opened = datetime(2026, 8, 1) - timedelta(days=RNG.randint(3, 700),
                                                      hours=RNG.randint(8, 17))
            wo, cid = nid("WO"), RNG.choice(contacts_by_acc[a["account"]])
            qualified = [t for t, sk in eng if a["family"] in sk] or [t for t, _ in eng]
            tech = RNG.choice(qualified)
            failed_first = RNG.random() < fail
            skus = [parts[k] for k in kinds]
            jobs += 1

            c.execute("""INSERT INTO work_orders
                (id,account_id,site_id,asset_id,contact_id,reported_symptom,status,
                 opened_at,closed_at,dealer_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (wo, a["account"], a["site"], a["id"], cid, symptom, "closed",
                 opened.isoformat(timespec="seconds"),
                 (opened + timedelta(days=5 if failed_first else 0,
                                     hours=RNG.randint(2, 8))).isoformat(timespec="seconds"),
                 DEALER))

            if failed_first:
                v1 = nid("V")
                c.execute("""INSERT INTO visits (id,work_order_id,seq,technician_id,
                    arrived_at,completed_at,outcome,found_cause,labor_hours,tech_note)
                    VALUES (?,?,1,?,?,?,?,?,?,?)""",
                    (v1, wo, tech,
                     (opened + timedelta(hours=RNG.randint(3, 20))).isoformat(timespec="seconds"),
                     (opened + timedelta(hours=RNG.randint(4, 22))).isoformat(timespec="seconds"),
                     "parts_missing",
                     f"diagnosed {cause.split(',')[0].lower()}, part not on the car",
                     round(RNG.uniform(0.5, 1.2), 2), "return with the part"))
                v2 = nid("V")
                done = opened + timedelta(days=5, hours=RNG.randint(2, 8))
                c.execute("""INSERT INTO visits (id,work_order_id,seq,technician_id,
                    arrived_at,completed_at,outcome,found_cause,labor_hours)
                    VALUES (?,?,2,?,?,?,?,?,?)""",
                    (v2, wo, tech, done.isoformat(timespec="seconds"),
                     (done + timedelta(hours=1)).isoformat(timespec="seconds"),
                     "fixed", cause, round(RNG.uniform(0.6, 1.8), 2)))
                final, fvf, closed = v2, 0, done.date().isoformat()
            else:
                v1 = nid("V")
                arr = opened + timedelta(hours=RNG.randint(2, 20))
                c.execute("""INSERT INTO visits (id,work_order_id,seq,technician_id,
                    arrived_at,completed_at,outcome,found_cause,labor_hours)
                    VALUES (?,?,1,?,?,?,?,?,?)""",
                    (v1, wo, tech, arr.isoformat(timespec="seconds"),
                     (arr + timedelta(hours=1)).isoformat(timespec="seconds"),
                     "fixed", cause, round(RNG.uniform(0.5, 2.2), 2)))
                final, fvf, closed = v1, 1, arr.date().isoformat()

            for s in skus:
                c.execute("INSERT OR IGNORE INTO parts_used (visit_id,sku,qty) VALUES (?,?,1)",
                          (final, s))
            c.execute("""INSERT INTO repairs
                (id,visit_id,asset_id,manufacturer,model_number,family,reported_symptom,
                 found_cause,parts_consumed,labor_hours,first_visit_fix,closed_on,
                 technician_id,dealer_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (nid("R"), final, a["id"], a["brand"], a["model"], a["family"],
                 symptom, cause, ",".join(skus), round(RNG.uniform(0.5, 2.2), 2),
                 fvf, closed, tech, DEALER))

        print(f"  {jobs} warranty callouts")


if __name__ == "__main__":
    build()
    with db.connect() as c:
        print("\ntwo separate businesses on one system:\n")
        for d in c.execute("SELECT * FROM dealers ORDER BY id"):
            acc = c.execute("SELECT COUNT(*) n FROM accounts WHERE dealer_id=?", (d["id"],)).fetchone()["n"]
            ast = c.execute("""SELECT COUNT(*) n FROM assets ast JOIN sites s ON s.id=ast.site_id
                               JOIN accounts a ON a.id=s.account_id WHERE a.dealer_id=?""",
                            (d["id"],)).fetchone()["n"]
            tech = c.execute("SELECT COUNT(*) n FROM technicians WHERE dealer_id=?", (d["id"],)).fetchone()["n"]
            rep = c.execute("SELECT COUNT(*) n FROM repairs WHERE dealer_id=?", (d["id"],)).fetchone()["n"]
            print(f"  {d['name']}")
            print(f"    {d['phone_e164']}   trade: {d['trade']}")
            print(f"    {acc} customers, {ast} machines, {tech} engineers, {rep} repairs on record")
