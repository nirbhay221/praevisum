"""Generate a dealer's book of business: customers, machines, and years of history.

Everything here is invented except the machines, which are drawn from the real
EPA certification catalogue already in the database. So the model numbers, the
manufacturers, the defrost types and the refrigerants are genuine, and only the
customers are fiction.

Why volume matters, beyond looking real:

  RETRIEVAL   the corpus is searched by meaning. Four repairs cannot show that
              working; four hundred can, because a caller's words will land on
              something a different technician wrote about a different machine.

  THE NUMBER  first-visit-fix is a rate. A rate over four jobs is noise. The
              generator targets the Aberdeen industry average of roughly 75%,
              with failures caused by the thing the research says causes them:
              the technician not having the part.

  DISPATCH    proximity only means something when technicians and sites are
              spread across a real map.

    .venv/Scripts/python.exe scripts/generate_book.py
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

RNG = random.Random(20260819)

# Quad Cities and around. Real towns, invented businesses.
TOWNS = [
    ("Moline", "IL", 41.5067, -90.5151), ("Rock Island", "IL", 41.5095, -90.5787),
    ("Davenport", "IA", 41.5236, -90.5776), ("Bettendorf", "IA", 41.5253, -90.5151),
    ("East Moline", "IL", 41.5095, -90.4443), ("Silvis", "IL", 41.5117, -90.4151),
    ("Milan", "IL", 41.4489, -90.5698), ("Coal Valley", "IL", 41.4322, -90.4593),
    ("Geneseo", "IL", 41.4478, -90.1548), ("Eldridge", "IA", 41.6572, -90.5751),
]

BUSINESS = [
    ("{} Diner", "restaurant"), ("The {} Tap", "bar"), ("{} Grill House", "restaurant"),
    ("{} Bakery", "bakery"), ("{} Market", "grocery"), ("Cafe {}", "cafe"),
    ("{} Steakhouse", "restaurant"), ("{} Pizzeria", "restaurant"),
    ("{} Brewing Co", "brewery"), ("{} Country Club", "club"),
    ("{} Elementary School", "school"), ("{} Nursing Home", "care"),
    ("Hotel {}", "hotel"), ("{} Convenience", "grocery"), ("{} Butchers", "butcher"),
    ("{} Ice Cream", "restaurant"), ("{} Catering", "catering"),
    ("{} Sports Bar", "bar"), ("{} Deli", "deli"), ("{} Fish Market", "grocery"),
]
PLACES = ["Riverside", "Maple", "Cedar", "Prospect", "Union", "Harrison", "Lincoln",
          "Blackhawk", "Sunset", "Kimberly", "Locust", "Brady", "Elmwood", "Vine",
          "Grand", "Watertown", "Arsenal", "Bridgeview", "Hilltop", "Greenbrier",
          "Meadow", "Northgate", "Fairview", "Oakdale", "Rockvale"]

FIRST = ["Sam","Denise","Marcus","Sarah","Alan","Priya","Terrence","Bianca","Hector",
         "Nadia","Owen","Lucia","Desmond","Ingrid","Rafael","Yolanda","Callum","Mei",
         "Joel","Amara","Vince","Tess","Omar","Greta","Nathan","Rosa","Emmett","Fiona",
         "Silas","Junie","Marta","Kofi","Ana","Pete","Ruth","Dmitri","Val","Noor"]
LAST = ["Whelan","Cole","Reed","Ortega","Prewitt","Nair","Boyd","Salcedo","Rivas",
        "Haddad","Kearns","Moreau","Blake","Sorensen","Vega","Mbeki","Doyle","Tanaka",
        "Frost","Okonkwo","Marchetti","Lindqvist","Bramble","Ashworth","Nakamura",
        "Duval","Kowalski","Ferreira","Hollis","Attah"]
ROLES = ["kitchen manager","owner","general manager","head chef","facilities manager",
         "bar manager","operations manager","site supervisor"]

STREETS = ["Pearl St","River Dr","Main St","5th Ave","16th St","Avenue of the Cities",
           "John Deere Rd","Kimberly Rd","Brady St","State St","Blackhawk Rd","7th Ave"]

# family -> (which catalogue categories to draw from, how common)
FAMILIES = [
    ("reach-in freezer", "refrigeration", "%Solid Door Freezer%", 22),
    ("reach-in cooler", "refrigeration", "%Solid Door Refrigerator%", 24),
    ("display cooler", "refrigeration", "%Transparent Door Refrigerator%", 18),
    ("walk-in cooler", "refrigeration", "%Refrigerator%", 10),
    ("ice machine", "refrigeration", "%", 9),
    ("dishwasher", "kitchen", "%", 7),
    ("oven", "kitchen", "%", 5),
    ("fryer", "kitchen", "%", 3),
    ("hot holding cabinet", "kitchen", "%", 2),
]

# Real failure modes. symptom the caller uses, what the technician writes,
# which parts, and how likely a first visit fails on that fault.
FAULTS = [
    ("not holding temp overnight, fine during service",
     "defrost termination thermostat open; ice build-up on coil",
     ["defrost-thermostat", "evap-fan"], 0.30),
    ("frost building on the coil, temp climbing at night",
     "termination thermostat failed and heater element pitted, replaced both",
     ["defrost-thermostat", "defrost-heater"], 0.40),
    ("door sweating and freezing shut in the mornings",
     "mullion heater harness open at the connector, frame runs below dew point",
     ["mullion-harness"], 0.25),
    ("compressor running constantly, never cycles off",
     "condenser packed with grease and lint, cleaned and recharged",
     [], 0.10),
    ("warm at open, ice all over the evaporator",
     "evaporator fan motor seized, coil iced over behind it",
     ["evap-fan"], 0.20),
    ("making a loud rattling noise from the back",
     "condenser fan motor bearing gone",
     ["cond-fan"], 0.15),
    ("not cold enough, food spoiling on the top shelf",
     "door gasket perished, warm air infiltration",
     ["door-gasket"], 0.12),
    ("display showing an error code and shutting down",
     "control board failed, no output to compressor relay",
     ["control-board"], 0.45),
    ("water pooling underneath it",
     "drain line blocked with sludge, cleared and treated",
     [], 0.08),
    ("ice machine producing hollow, cloudy cubes",
     "water inlet valve partially blocked, scale on the evaporator plate",
     ["water-valve"], 0.28),
    ("tripping the breaker when it kicks in",
     "compressor start capacitor failed",
     ["start-capacitor"], 0.35),
    ("temperature swinging up and down all day",
     "thermostat sensor out of calibration, drifted 6 degrees",
     ["defrost-thermostat"], 0.22),
]

PART_KINDS = {
    "defrost-thermostat": ("Defrost termination thermostat", 62.40, 2),
    "defrost-heater": ("Defrost heater element", 148.00, 3),
    "mullion-harness": ("Door mullion heater harness", 118.30, 4),
    "evap-fan": ("Evaporator fan motor", 94.10, 2),
    "cond-fan": ("Condenser fan motor", 132.00, 4),
    "door-gasket": ("Door gasket", 88.50, 2),
    "control-board": ("Electronic control board", 386.75, 9),
    "water-valve": ("Water inlet valve", 119.00, 3),
    "start-capacitor": ("Compressor start capacitor", 41.20, 1),
}

TECHS = [
    ("Curtis Okafor", "Moline", ["reach-in freezer","reach-in cooler","walk-in cooler","display cooler"]),
    ("Marisol Vance", "Davenport", ["ice machine","walk-in cooler","reach-in cooler"]),
    ("Ben Kalita", "Rock Island", ["reach-in freezer","display cooler","dishwasher"]),
    ("Priya Raman", "Bettendorf", ["ice machine","dishwasher","oven","fryer"]),
    ("Dale Hutchins", "Moline", ["walk-in cooler","reach-in freezer","hot holding cabinet"]),
    ("Ana Sotelo", "Silvis", ["reach-in cooler","display cooler","reach-in freezer"]),
    ("Wes Tumelty", "East Moline", ["oven","fryer","dishwasher","hot holding cabinet"]),
    ("Grace Ihejirika", "Geneseo", ["reach-in freezer","reach-in cooler","ice machine"]),
]


def nid(p: str) -> str:
    return f"{p}-{uuid.uuid4().hex[:6].upper()}"


def jitter(lat: float, lon: float) -> tuple[float, float]:
    return round(lat + RNG.uniform(-.02, .02), 4), round(lon + RNG.uniform(-.02, .02), 4)


def pick_models(c, category: str, like: str, n: int) -> list[tuple[str, str]]:
    rows = c.execute(
        """SELECT brand, model_number FROM equipment
           WHERE site_visit=1 AND category=? AND product_type LIKE ?
           GROUP BY brand, model_number ORDER BY RANDOM() LIMIT ?""",
        (category, like, n)).fetchall()
    return [(r["brand"], r["model_number"]) for r in rows]


def build() -> None:
    with db.txn() as c:
        # order matters less than turning the constraint off for the wipe:
        # these tables reference each other in both directions.
        c.execute("PRAGMA defer_foreign_keys = ON")
        for t in ("parts_used","reservations","repairs","visits","work_orders","calls",
                  "supplier_offers","promotion_parts","promotions","stock",
                  "stock_locations","fitments","parts","technician_skills",
                  "technicians","assets","phones","contacts","sites","accounts",
                  "suppliers"):
            c.execute(f"DELETE FROM {t}")

        # ---------- suppliers, parts, fitment, stock ----------
        c.executemany("INSERT INTO suppliers (id,name,contact,phone) VALUES (?,?,?,?)", [
            ("SUP-1","Midway Parts Co","Dana Reyes","+15635550142"),
            ("SUP-2","Encompass Supply",None,None),
            ("SUP-3","Great River Refrigeration Supply","Ed Tolliver","+15635550188"),
        ])

        parts: dict[str, str] = {}          # kind -> sku
        rows = []
        for kind, (name, cost, lead) in PART_KINDS.items():
            sku = f"P-{kind.upper().replace('-','')[:10]}"
            parts[kind] = sku
            rows.append((sku, name, cost, lead, RNG.choice(["SUP-1","SUP-2","SUP-3"])))
        c.executemany("INSERT INTO parts (sku,name,unit_cost,lead_time_days,supplier_id) "
                      "VALUES (?,?,?,?,?)", rows)

        c.executemany("INSERT INTO stock_locations (id,kind,label,mobile) VALUES (?,?,?,?)",
                      [("LOC-WH","warehouse","Moline warehouse",0)])
        c.executemany("INSERT INTO stock (location_id,sku,on_hand) VALUES (?,?,?)",
                      [("LOC-WH", sku, RNG.randint(2, 9)) for sku in parts.values()])

        # ---------- technicians and their vans ----------
        tech_ids = []
        for name, town, skills in TECHS:
            tid = nid("T")
            tech_ids.append((tid, skills))
            base = next(t for t in TOWNS if t[0] == town)
            lat, lon = jitter(base[2], base[3])
            van = f"VAN-{tid[-4:]}"
            c.execute("INSERT INTO stock_locations (id,kind,label,mobile) VALUES (?,?,?,?)",
                      (van, "van", f"{name.split()[0]} van", 1))
            c.execute("""INSERT INTO technicians (id,name,phone,home_base,lat,lon,van_location)
                         VALUES (?,?,?,?,?,?,?)""",
                      (tid, name, f"+1309555{RNG.randint(9000,9999)}",
                       f"{town} {base[1]}", lat, lon, van))
            c.executemany("INSERT INTO technician_skills (technician_id,family) VALUES (?,?)",
                          [(tid, s) for s in skills])
            # a couple of parts already rolling around in each van
            for sku in RNG.sample(list(parts.values()), RNG.randint(2, 4)):
                c.execute("INSERT INTO stock (location_id,sku,on_hand) VALUES (?,?,?)",
                          (van, sku, RNG.randint(1, 2)))

        # ---------- the book of business ----------
        used_names: set[str] = set()
        assets: list[dict] = []
        contacts_by_account: dict[str, list[str]] = {}
        weights = [f[3] for f in FAMILIES]

        n_business, n_residential = 58, 14
        for i in range(n_business + n_residential):
            residential = i >= n_business
            town = RNG.choice(TOWNS)

            if residential:
                person = f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
                while person in used_names:
                    person = f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
                used_names.add(person)
                acc_id, acc_name, kind = nid("A"), person, "person"
                n_sites = 1
            else:
                pat, _ = RNG.choice(BUSINESS)
                name = pat.format(RNG.choice(PLACES))
                while name in used_names:
                    pat, _ = RNG.choice(BUSINESS)
                    name = pat.format(RNG.choice(PLACES))
                used_names.add(name)
                acc_id, acc_name, kind = nid("A"), name, "business"
                n_sites = RNG.choices([1, 1, 1, 2, 3], weights=[60, 12, 8, 15, 5])[0]

            opened = date(2026, 8, 1) - timedelta(days=RNG.randint(200, 2200))
            c.execute("INSERT INTO accounts (id,kind,name,trade_terms,opened_on) VALUES (?,?,?,?,?)",
                      (acc_id, kind, acc_name,
                       "net 30" if kind == "business" else "card on file",
                       opened.isoformat()))

            site_ids = []
            for s in range(n_sites):
                sid = nid("S")
                site_ids.append(sid)
                lat, lon = jitter(town[2], town[3])
                label = acc_name if n_sites == 1 else f"{acc_name} ({RNG.choice(PLACES)})"
                c.execute("""INSERT INTO sites (id,account_id,label,address,lat,lon,access_note)
                             VALUES (?,?,?,?,?,?,?)""",
                          (sid, acc_id, label,
                           f"{RNG.randint(2,4800)} {RNG.choice(STREETS)}, {town[0]} {town[1]}",
                           lat, lon,
                           RNG.choice([None, None, None, "deliveries via alley",
                                       "closed 3-4pm", "ask at the bar", "dog in yard"])))

            # people, and their phones
            cids = []
            n_contacts = 1 if residential else RNG.choices([1, 2, 3], weights=[45, 40, 15])[0]
            for k in range(n_contacts):
                cid = nid("C")
                cids.append(cid)
                person = acc_name if residential else f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
                c.execute("""INSERT INTO contacts (id,account_id,site_id,name,role,channel_pref)
                             VALUES (?,?,?,?,?,?)""",
                          (cid, acc_id, site_ids[0] if k == 0 else RNG.choice(site_ids),
                           person, "homeowner" if residential else RNG.choice(ROLES),
                           RNG.choice(["sms", "sms", "sms", "whatsapp", "email"])))
                for label in (["mobile"] if residential or k else
                              RNG.choice([["mobile"], ["mobile", "landline"]])):
                    c.execute("INSERT INTO phones (e164,contact_id,label,verified) VALUES (?,?,?,?)",
                              (f"+1309555{RNG.randint(1000,8999)}", cid, label, 1))
            contacts_by_account[acc_id] = cids

            # their machines, drawn from the real catalogue
            for sid in site_ids:
                n_assets = 1 if residential else RNG.choices([1,2,3,4,5,6],
                                weights=[10,22,26,20,14,8])[0]
                fams = RNG.choices(FAMILIES, weights=weights, k=n_assets)
                for fam, cat, like, _ in fams:
                    picks = pick_models(c, cat, like, 1)
                    if not picks:
                        continue
                    brand, model = picks[0]
                    aid = nid("AST")
                    installed = date(2026, 8, 1) - timedelta(days=RNG.randint(120, 3300))
                    c.execute("""INSERT INTO assets
                        (id,site_id,manufacturer,model_number,family,installed_on,location_note)
                        VALUES (?,?,?,?,?,?,?)""",
                        (aid, sid, brand, model, fam, installed.isoformat(),
                         RNG.choice(["kitchen, back wall","prep line","rear dock","front bar",
                                     "service corridor","walk-in vestibule","under counter"])))
                    assets.append({"id": aid, "site": sid, "account": acc_id,
                                   "brand": brand, "model": model, "family": fam})

        c.execute("""UPDATE assets SET equipment_id=(SELECT e.id FROM equipment e
                     WHERE e.brand=assets.manufacturer AND e.model_number=assets.model_number
                     LIMIT 1)""")

        # fitment: every part kind fits the machine families it belongs to
        seen = set()
        for a in assets:
            for kind, sku in parts.items():
                key = (sku, a["brand"], a["model"])
                if key in seen:
                    continue
                seen.add(key)
                c.execute("INSERT OR IGNORE INTO fitments (sku,manufacturer,model_pattern,source) "
                          "VALUES (?,?,?,?)", (sku, a["brand"], a["model"], "dealer"))

        # ---------- two years of work ----------
        n_jobs = 0
        n_visits = 0
        for _ in range(430):
            a = RNG.choice(assets)
            symptom, cause, kinds, fail_rate = RNG.choice(FAULTS)
            opened = datetime(2026, 8, 1) - timedelta(days=RNG.randint(3, 730),
                                                      hours=RNG.randint(6, 19))
            cid = RNG.choice(contacts_by_account[a["account"]])
            wo = nid("WO")
            n_jobs += 1

            qualified = [t for t, sk in tech_ids if a["family"] in sk] or [t for t, _ in tech_ids]
            tech = RNG.choice(qualified)
            failed_first = RNG.random() < fail_rate

            c.execute("""INSERT INTO work_orders
                (id,account_id,site_id,asset_id,contact_id,reported_symptom,error_code,
                 status,opened_at,closed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (wo, a["account"], a["site"], a["id"], cid, symptom,
                 RNG.choice([None,None,None,"dEF","dF","E4","HP1"]),
                 "closed", opened.isoformat(timespec="seconds"),
                 (opened + timedelta(days=7 if failed_first else 0,
                                     hours=RNG.randint(2,9))).isoformat(timespec="seconds")))

            skus = [parts[k] for k in kinds]
            if failed_first:
                # visit 1 fails for the documented reason: the part was not there
                missing = skus[-1] if skus else parts["evap-fan"]
                v1 = nid("V"); n_visits += 1
                c.execute("""INSERT INTO visits (id,work_order_id,seq,technician_id,
                    promised_window,arrived_at,completed_at,outcome,found_cause,labor_hours,tech_note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (v1, wo, 1, tech, None,
                     (opened+timedelta(hours=RNG.randint(3,26))).isoformat(timespec="seconds"),
                     (opened+timedelta(hours=RNG.randint(4,28))).isoformat(timespec="seconds"),
                     "parts_missing",
                     f"diagnosed {cause.split(';')[0]}, did not have the part on the van",
                     round(RNG.uniform(0.8,1.8),2),
                     "return trip needed, bring the part"))
                if skus[:-1]:
                    c.execute("INSERT OR IGNORE INTO parts_used (visit_id,sku,qty) VALUES (?,?,1)",
                              (v1, skus[0]))
                v2 = nid("V"); n_visits += 1
                done = opened + timedelta(days=7, hours=RNG.randint(2,9))
                c.execute("""INSERT INTO visits (id,work_order_id,seq,technician_id,
                    promised_window,arrived_at,completed_at,outcome,found_cause,labor_hours,tech_note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (v2, wo, 2, tech, None, done.isoformat(timespec="seconds"),
                     (done+timedelta(hours=2)).isoformat(timespec="seconds"),
                     "fixed", cause, round(RNG.uniform(1.0,2.5),2), None))
                for s in skus:
                    c.execute("INSERT OR IGNORE INTO parts_used (visit_id,sku,qty) VALUES (?,?,1)", (v2, s))
                final_visit, fvf, closed = v2, 0, done.date().isoformat()
                hours = round(RNG.uniform(2.2,4.0),2)
            else:
                v1 = nid("V"); n_visits += 1
                arr = opened + timedelta(hours=RNG.randint(2,26))
                c.execute("""INSERT INTO visits (id,work_order_id,seq,technician_id,
                    promised_window,arrived_at,completed_at,outcome,found_cause,labor_hours,tech_note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (v1, wo, 1, tech, None, arr.isoformat(timespec="seconds"),
                     (arr+timedelta(hours=2)).isoformat(timespec="seconds"),
                     "fixed", cause, round(RNG.uniform(0.9,3.2),2),
                     RNG.choice([None,None,None,"customer asked about a PM contract"])))
                for s in skus:
                    c.execute("INSERT OR IGNORE INTO parts_used (visit_id,sku,qty) VALUES (?,?,1)", (v1, s))
                final_visit, fvf, closed = v1, 1, arr.date().isoformat()
                hours = round(RNG.uniform(0.9,3.2),2)

            c.execute("""INSERT INTO repairs
                (id,visit_id,asset_id,manufacturer,model_number,family,reported_symptom,
                 error_code,found_cause,tech_note,parts_consumed,labor_hours,first_visit_fix,
                 closed_on,technician_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (nid("R"), final_visit, a["id"], a["brand"], a["model"], a["family"],
                 symptom, None, cause,
                 "thermostat alone did not hold last time" if "thermostat" in cause and RNG.random()<.3 else None,
                 ",".join(skus), hours, fvf, closed, tech))

        # ---------- promotions ----------
        c.executemany("INSERT INTO promotions (id,headline,detail,ends,terms) VALUES (?,?,?,?,?)", [
            ("P-401","10% off defrost components",
             "termination thermostats, heater elements and harnesses","2026-09-30",
             "trade accounts, while stock lasts"),
            ("P-402","Free first-year labour on planned maintenance",
             "on any walk-in cooler or freezer PM contract signed this quarter",
             "2026-09-30","12 month minimum term"),
            ("P-403","Evaporator fan motors, buy 3 pay for 2",
             "across all fitments","2026-08-31","cannot combine with other offers"),
        ])
        for sku in (parts["defrost-thermostat"], parts["defrost-heater"], parts["mullion-harness"]):
            c.execute("INSERT INTO promotion_parts (promotion_id,sku) VALUES (?,?)", ("P-401", sku))
        c.execute("INSERT INTO promotion_parts (promotion_id,sku) VALUES (?,?)",
                  ("P-403", parts["evap-fan"]))

        print(f"  {n_jobs} jobs, {n_visits} visits")


if __name__ == "__main__":
    db.init()
    build()
    with db.connect() as c:
        print("\nbook of business:")
        for t in ("accounts","sites","contacts","phones","assets","technicians",
                  "parts","fitments","stock_locations","work_orders","visits",
                  "parts_used","repairs","promotions"):
            print(f"  {t:<16} {c.execute(f'SELECT COUNT(*) n FROM {t}').fetchone()['n']:>6}")
        r = c.execute("""SELECT COUNT(*) n, SUM(fixed_first_time) f
                         FROM first_visit_fix""").fetchone()
        print(f"\n  first-visit-fix: {r['f']}/{r['n']} = {100*r['f']/r['n']:.1f}%")
