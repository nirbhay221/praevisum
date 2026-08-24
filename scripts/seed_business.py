"""Seed the dealer's own data into the new schema.

Deliberately shaped to exercise the things the old model could not hold:

  - one account with TWO sites          (Pearl Street Restaurant Group)
  - one account that is a PERSON        (residential, no business)
  - one site with THREE phone numbers   (owner, manager, kitchen landline)
  - vans as stock locations             (not a field on the technician)
  - a work order with TWO visits        (failed first trip, then the fix)

That last one is the point. The old schema had one row per job with a
promised_window column, so a return trip was unrepresentable, which meant
first-visit-fix rate was unmeasurable, which meant the product's central
claim could not be evidenced.

    .venv/Scripts/python.exe scripts/seed_business.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402


def wipe(c) -> None:
    for t in ("parts_used", "reservations", "visits", "work_orders", "calls",
              "repairs", "supplier_offers", "promotion_parts", "promotions",
              "stock", "stock_locations", "fitments", "parts",
              "technician_skills", "technicians", "assets", "phones",
              "contacts", "sites", "accounts", "suppliers"):
        c.execute(f"DELETE FROM {t}")


def seed() -> None:
    with db.txn() as c:
        wipe(c)

        # ---------- accounts, sites, people ----------
        c.executemany("INSERT INTO accounts (id,kind,name,trade_terms,opened_on) VALUES (?,?,?,?,?)", [
            ("A-100", "business", "Pearl Street Restaurant Group", "net 30", "2019-03-11"),
            ("A-101", "business", "Rivertown Tap", "net 30", "2021-06-02"),
            ("A-102", "person", "Sarah Ortega", "card on file", "2023-05-19"),
        ])

        c.executemany("INSERT INTO sites (id,account_id,label,address,lat,lon,access_note) VALUES (?,?,?,?,?,?,?)", [
            # one account, two sites. The old model could not do this.
            ("S-1", "A-100", "Pearl Street Kitchen", "412 Pearl St, Moline IL", 41.5067, -90.5151, "deliveries via alley"),
            ("S-2", "A-100", "Pearl Street Riverside", "77 River Dr, Moline IL", 41.5121, -90.5249, "kitchen closed 3-4pm"),
            ("S-3", "A-101", "Rivertown Tap", "88 River Dr, Moline IL", 41.5120, -90.5250, None),
            ("S-4", "A-102", "Ortega residence", "77 Elm Ave, Rock Island IL", 41.5095, -90.5787, "dog in yard"),
        ])

        c.executemany("INSERT INTO contacts (id,account_id,site_id,name,role,channel_pref) VALUES (?,?,?,?,?,?)", [
            ("C-1", "A-100", "S-1", "Sam Whelan", "kitchen manager", "sms"),
            ("C-2", "A-100", None, "Denise Cole", "owner", "email"),
            ("C-3", "A-101", "S-3", "Marcus Reed", "bar manager", "whatsapp"),
            ("C-4", "A-102", "S-4", "Sarah Ortega", "homeowner", "sms"),
        ])

        # one person, several numbers. This is what an inbound call matches on.
        c.executemany("INSERT INTO phones (e164,contact_id,label,verified) VALUES (?,?,?,?)", [
            ("+13095550101", "C-1", "mobile", 1),
            ("+13095550111", "C-1", "kitchen landline", 1),
            ("+13095550120", "C-2", "mobile", 1),
            ("+13095550102", "C-3", "mobile", 1),
            ("+13095550103", "C-4", "mobile", 1),
        ])

        c.executemany("INSERT INTO suppliers (id,name,contact,phone) VALUES (?,?,?,?)", [
            ("SUP-1", "Midway Parts Co", "Dana Reyes", "+15635550142"),
            ("SUP-2", "Encompass", None, None),
        ])

        # ---------- the machines our customers own ----------
        # manufacturer + model_number point at the catalogue. Real Traulsen
        # model numbers, from the EPA certification data.
        c.executemany("""INSERT INTO assets
            (id,site_id,manufacturer,model_number,family,installed_on,location_note)
            VALUES (?,?,?,?,?,?,?)""", [
            ("TRL-8871", "S-1", "Traulsen", "CLBM-23F-FS", "reach-in freezer", "2019-04-02", "kitchen, back wall"),
            ("TRU-4402", "S-1", "True Refrigeration", "TWT-48F", "walk-in cooler", "2021-08-15", "rear dock"),
            # same model as TRL-8871 but a different site, which is what makes
            # cross-site learning demonstrable
            ("TRL-9903", "S-3", "Traulsen", "CLBM-23F-FS", "reach-in freezer", "2020-09-14", "kitchen pass"),
            ("BEV-1190", "S-3", "Beverage-Air", "DD68HC-1-S", "back bar cooler", "2022-02-01", "front bar"),
            ("TRL-7742", "S-2", "Traulsen", "CLBM-49F-FS", "reach-in freezer", "2022-11-30", "prep line"),
            ("WHP-2210", "S-4", "Whirlpool", "WRS588FIHZ", "side-by-side refrigerator", "2023-06-10", "kitchen"),
        ])

        # link assets to the certified catalogue where a match exists
        c.execute("""UPDATE assets SET equipment_id = (
                       SELECT e.id FROM equipment e
                       WHERE e.brand = assets.manufacturer
                         AND e.model_number = assets.model_number LIMIT 1)""")

        # ---------- parts, fitment, stock ----------
        c.executemany("INSERT INTO parts (sku,name,unit_cost,lead_time_days,supplier_id) VALUES (?,?,?,?,?)", [
            ("TRL-329410", "Defrost termination thermostat", 62.40, 2, "SUP-1"),
            ("TRL-334862", "Defrost heater element", 148.00, 3, "SUP-1"),
            ("TRL-401255", "Defrost timer / control board", 386.75, 9, "SUP-2"),
            ("TRL-220118", "Evaporator fan motor", 94.10, 2, "SUP-1"),
            ("TRL-556700", "Door mullion heater harness", 118.30, 4, "SUP-1"),
            ("TRU-988201", "Condenser fan motor", 132.00, 4, "SUP-1"),
            ("TRU-771043", "Door gasket, 48in", 88.50, 2, "SUP-1"),
            ("BEV-556120", "Thermostat control", 71.25, 5, "SUP-2"),
            ("WHP-W11024", "Defrost control board", 143.90, 4, "SUP-2"),
            ("WHP-W10919", "Evaporator fan motor", 76.55, 2, "SUP-2"),
        ])

        # fitment as facts, not a startswith() guess
        c.executemany("INSERT INTO fitments (sku,manufacturer,model_pattern,source) VALUES (?,?,?,?)", [
            ("TRL-329410", "Traulsen", "CLBM-%", "manufacturer"),
            ("TRL-334862", "Traulsen", "CLBM-%", "manufacturer"),
            ("TRL-401255", "Traulsen", "CLBM-%", "manufacturer"),
            ("TRL-220118", "Traulsen", "CLBM-%", "manufacturer"),
            ("TRL-556700", "Traulsen", "CLBM-%", "manufacturer"),
            ("TRU-988201", "True Refrigeration", "TWT-%", "manufacturer"),
            ("TRU-771043", "True Refrigeration", "TWT-48%", "manufacturer"),
            ("BEV-556120", "Beverage-Air", "DD68%", "manufacturer"),
            ("WHP-W11024", "Whirlpool", "WRS588%", "manufacturer"),
            ("WHP-W10919", "Whirlpool", "WRS588%", "manufacturer"),
        ])

        # a van is a stock location, the same kind of thing as the warehouse
        c.executemany("INSERT INTO stock_locations (id,kind,label,mobile) VALUES (?,?,?,?)", [
            ("LOC-WH", "warehouse", "Moline warehouse", 0),
            ("VAN-01", "van", "Curtis van", 1),
            ("VAN-02", "van", "Marisol van", 1),
            ("VAN-03", "van", "Ben van", 1),
        ])

        c.executemany("INSERT INTO stock (location_id,sku,on_hand) VALUES (?,?,?)", [
            ("LOC-WH", "TRL-329410", 4), ("LOC-WH", "TRL-334862", 2),
            ("LOC-WH", "TRL-401255", 1),   # deliberately scarce
            ("LOC-WH", "TRL-220118", 6), ("LOC-WH", "TRL-556700", 3),
            ("LOC-WH", "TRU-988201", 3), ("LOC-WH", "TRU-771043", 5),
            ("LOC-WH", "BEV-556120", 2), ("LOC-WH", "WHP-W11024", 2),
            ("LOC-WH", "WHP-W10919", 4),
            # what is already rolling around in the vans
            ("VAN-01", "TRL-220118", 1), ("VAN-01", "TRU-771043", 1),
            ("VAN-02", "TRU-988201", 1),
        ])

        # ---------- technicians ----------
        c.executemany("""INSERT INTO technicians (id,name,phone,home_base,lat,lon,van_location)
                         VALUES (?,?,?,?,?,?,?)""", [
            ("T-01", "Curtis Okafor", "+13095559001", "Moline IL", 41.5040, -90.5100, "VAN-01"),
            ("T-02", "Marisol Vance", "+13095559002", "Davenport IA", 41.5236, -90.5776, "VAN-02"),
            ("T-03", "Ben Kalita", "+13095559003", "Rock Island IL", 41.5095, -90.5787, "VAN-03"),
        ])
        c.executemany("INSERT INTO technician_skills (technician_id,family) VALUES (?,?)", [
            ("T-01", "reach-in freezer"), ("T-01", "walk-in cooler"), ("T-01", "back bar cooler"),
            ("T-02", "ice machine"), ("T-02", "walk-in cooler"),
            ("T-03", "side-by-side refrigerator"), ("T-03", "reach-in freezer"),
        ])

        # ---------- history, including a job that took TWO visits ----------
        c.executemany("""INSERT INTO work_orders
            (id,account_id,site_id,asset_id,contact_id,reported_symptom,error_code,status,opened_at,closed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", [
            ("WO-2411", "A-100", "S-1", "TRL-8871", "C-1",
             "not holding temp overnight, fine during service", "dEF", "closed",
             "2024-11-12T08:10:00", "2024-11-12T11:40:00"),
            # THE ONE THAT MATTERS: first visit failed, second fixed it
            ("WO-2507", "A-100", "S-1", "TRL-8871", "C-1",
             "frost on coil, temp climbing at night", "dEF", "closed",
             "2025-07-28T07:55:00", "2025-08-04T15:20:00"),
            ("WO-2503", "A-101", "S-3", "TRL-9903", "C-3",
             "warm at open, ice on evaporator", "dEF", "closed",
             "2025-03-04T09:00:00", "2025-03-04T12:10:00"),
            ("WO-2509", "A-102", "S-4", "WHP-2210", "C-4",
             "freezer cold, fridge warm", "dF", "closed",
             "2025-09-30T13:00:00", "2025-09-30T15:15:00"),
        ])

        c.executemany("""INSERT INTO visits
            (id,work_order_id,seq,technician_id,promised_window,arrived_at,completed_at,
             outcome,found_cause,labor_hours,tech_note) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", [
            ("V-2411-1", "WO-2411", 1, "T-01", "Tue 9-11am",
             "2024-11-12T09:20:00", "2024-11-12T11:40:00", "fixed",
             "defrost termination thermostat open; ice build-up on coil", 2.5, None),

            # visit 1 failed for exactly the reason this product exists
            ("V-2507-1", "WO-2507", 1, "T-01", "Mon 8-10am",
             "2025-07-28T08:35:00", "2025-07-28T10:05:00", "parts_missing",
             "replaced termination thermostat, but the heater element is pitted "
             "and I did not have one on the van", 1.5,
             "come back with a heater element, thermostat alone will not hold"),
            ("V-2507-2", "WO-2507", 2, "T-01", "Mon 1-3pm",
             "2025-08-04T13:10:00", "2025-08-04T15:20:00", "fixed",
             "fitted heater element; thermostat alone did not hold last time", 1.5, None),

            ("V-2503-1", "WO-2503", 1, "T-03", "Tue 9-11am",
             "2025-03-04T09:40:00", "2025-03-04T12:10:00", "fixed",
             "termination thermostat; heater element also failed within 3 months "
             "on the prior call, fitted both this time", 2.75, None),
            ("V-2509-1", "WO-2509", 1, "T-03", "Tue 1-3pm",
             "2025-09-30T13:25:00", "2025-09-30T15:15:00", "fixed",
             "defrost control board failed; evap fan seized", 1.75, None),
        ])

        c.executemany("INSERT INTO parts_used (visit_id,sku,qty) VALUES (?,?,?)", [
            ("V-2411-1", "TRL-329410", 1), ("V-2411-1", "TRL-220118", 1),
            ("V-2507-1", "TRL-329410", 1),
            ("V-2507-2", "TRL-334862", 1),
            ("V-2503-1", "TRL-329410", 1), ("V-2503-1", "TRL-334862", 1),
            ("V-2509-1", "WHP-W11024", 1), ("V-2509-1", "WHP-W10919", 1),
        ])

        # the corpus: one row per completed visit that found something
        c.executemany("""INSERT INTO repairs
            (id,visit_id,asset_id,manufacturer,model_number,family,reported_symptom,
             error_code,found_cause,tech_note,parts_consumed,labor_hours,
             first_visit_fix,closed_on,technician_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
            ("R-2411", "V-2411-1", "TRL-8871", "Traulsen", "CLBM-23F-FS", "reach-in freezer",
             "not holding temp overnight, fine during service", "dEF",
             "defrost termination thermostat open; ice build-up on coil", None,
             "TRL-329410,TRL-220118", 2.5, 1, "2024-11-12", "T-01"),
            ("R-2507", "V-2507-2", "TRL-8871", "Traulsen", "CLBM-23F-FS", "reach-in freezer",
             "frost on coil, temp climbing at night", "dEF",
             "termination thermostat failed again; heater element pitted, replaced both",
             "thermostat alone did not hold last time",
             "TRL-329410,TRL-334862", 3.0, 0, "2025-08-04", "T-01"),
            ("R-2503", "V-2503-1", "TRL-9903", "Traulsen", "CLBM-23F-FS", "reach-in freezer",
             "warm at open, ice on evaporator", "dEF",
             "termination thermostat; heater element also failed within 3 months on the prior call",
             "fitted both this time", "TRL-329410,TRL-334862", 2.75, 1, "2025-03-04", "T-03"),
            ("R-2509", "V-2509-1", "WHP-2210", "Whirlpool", "WRS588FIHZ", "side-by-side refrigerator",
             "freezer cold, fridge warm", "dF",
             "defrost control board failed; evap fan seized", None,
             "WHP-W11024,WHP-W10919", 1.75, 1, "2025-09-30", "T-03"),
        ])

        # ---------- promotions ----------
        c.executemany("INSERT INTO promotions (id,headline,detail,ends,terms) VALUES (?,?,?,?,?)", [
            ("P-401", "10% off all Traulsen defrost components",
             "termination thermostats, heater elements and harnesses",
             "2026-09-30", "trade accounts, while stock lasts"),
            ("P-402", "Free first-year labour on planned maintenance",
             "on any walk-in cooler or freezer PM contract signed this quarter",
             "2026-09-30", "12 month minimum term"),
        ])
        c.executemany("INSERT INTO promotion_parts (promotion_id,sku) VALUES (?,?)", [
            ("P-401", "TRL-329410"), ("P-401", "TRL-334862"), ("P-401", "TRL-556700"),
        ])


if __name__ == "__main__":
    db.init()
    seed()
    with db.connect() as c:
        print("seeded:\n")
        for t in ("accounts", "sites", "contacts", "phones", "assets", "parts",
                  "fitments", "stock_locations", "stock", "technicians",
                  "work_orders", "visits", "parts_used", "repairs",
                  "suppliers", "promotions"):
            n = c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
            print(f"  {t:<17} {n}")
