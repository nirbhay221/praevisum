"""Load every ENERGY STAR dataset, and mark which ones get a site visit.

Earlier I filtered at load time and excluded things twice that I should not
have: enterprise laptops under onsite warranty, then residential HVAC and
appliances. A home heat pump absolutely gets a technician in a van.

So the filter moves out of the loader and into a column. Load everything,
tag each row `site_visit` 1 or 0, and let the query decide. Being wrong about
a category then costs an UPDATE rather than a re-download.

    .venv/Scripts/python.exe scripts/load_all.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

CATALOG = "https://data.energystar.gov/api/catalog/v1?limit=200&search_context=data.energystar.gov"

# Does a human travel to the broken thing carrying parts chosen in advance?
NO_SITE_VISIT = (
    "television", "telephone", "light bulb", "light fixture", "ceiling fan",
    "insulation", "storm window", "room air cleaner", "connected light",
    "model index", "developer resources", "upc codes", "most efficient",
    "tax credit",
)

CATEGORY = [
    (("refrigerat", "freezer", "ice machine"), "refrigeration"),
    (("dishwash", "oven", "fryer", "griddle", "steam cook", "hot food",
      "coffee brewer", "cooktop", "cooking"), "kitchen"),
    (("hvac", "heat pump", "air conditioner", "furnace", "ventilating",
      "thermostat", "dehumidifier"), "hvac"),
    (("boiler", "water heater", "pool pump"), "plant"),
    (("computer", "server", "storage", "network", "imaging", "uninterruptible",
      "display"), "it"),
    (("medical",), "medical"),
    (("clothes wash", "clothes dryer", "washer-dryer", "combo"), "laundry"),
    (("vending", "water cooler"), "vending"),
    (("vehicle supply", "electric vehicle"), "ev"),
]


def classify(name: str) -> tuple[str, int]:
    low = name.lower()
    serviceable = 0 if any(k in low for k in NO_SITE_VISIT) else 1
    for keys, cat in CATEGORY:
        if any(k in low for k in keys):
            return cat, serviceable
    return "other", serviceable


CAP = ("total_volume_cu_ft", "capacity_cu_ft", "capacity", "total_display_area_sq_ft",
       "rated_storage_volume_cu_ft", "storage_volume_gallons", "screen_size_in")
KWH = ("reported_daily_energy_consumption_kwh_day", "daily_energy_consumption_kwh_day",
       "annual_energy_use_kwh_yr", "measured_energy_consumption_kwh_yr",
       "typical_electricity_consumption_kwh_yr")
TYPE = ("product_type", "type", "product_class", "equipment_type", "category",
        "product_subtype")
REFRIG = ("refrigerant_type", "refrigerant", "refrigerant_with_gwp")
DEFROST = ("defrost_type", "defrost")
CERT = ("date_certified", "date_available_on_market", "certification_date")


def first(rec: dict, keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", []):
            return str(v)
    return None


def main() -> None:
    db.init()
    with db.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(equipment)")}
        if "site_visit" not in cols:
            c.execute("ALTER TABLE equipment ADD COLUMN site_visit INTEGER DEFAULT 1")
            print("added site_visit column")

    with urllib.request.urlopen(CATALOG, timeout=90) as r:
        cat = json.load(r)
    sets = {res["resource"]["id"]: res["resource"]["name"] for res in cat.get("results", [])}
    print(f"{len(sets)} datasets published\n")

    total = skipped = 0
    for sid, name in sorted(sets.items(), key=lambda x: x[1]):
        category, serviceable = classify(name)
        try:
            u = f"https://data.energystar.gov/resource/{sid}.json?%24limit=50000"
            with urllib.request.urlopen(u, timeout=150) as r:
                rows = json.load(r)
        except Exception as e:
            print(f"  ---- {name[:52]:<52} {type(e).__name__}")
            skipped += 1
            continue

        n = 0
        with db.txn() as c:
            for rec in rows:
                brand = (rec.get("brand_name") or rec.get("brand") or "").strip()
                model = (rec.get("model_number") or rec.get("model_name") or "").strip()
                if not brand or not model:
                    continue
                kwh = first(rec, KWH)
                try:
                    kwh = float(kwh) if kwh and kwh.replace(".", "", 1).replace("-", "", 1).isdigit() else None
                except Exception:
                    kwh = None
                try:
                    c.execute(
                        """INSERT OR IGNORE INTO equipment
                           (source,dataset,category,brand,model_number,product_type,
                            defrost_type,refrigerant,capacity,daily_kwh,certified_on,
                            raw,site_visit)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("energystar", name.replace("ENERGY STAR ", ""), category,
                         brand, model, first(rec, TYPE), first(rec, DEFROST),
                         first(rec, REFRIG), first(rec, CAP), kwh,
                         (first(rec, CERT) or "")[:10] or None,
                         json.dumps(rec, separators=(",", ":")), serviceable),
                    )
                    n += 1
                except Exception:
                    pass
        total += n
        flag = "site" if serviceable else "  - "
        print(f"  {flag} {n:>6}  {category:<13} {name.replace('ENERGY STAR ','')[:46]}")
        time.sleep(0.15)

    print(f"\n  {total:,} rows read, {skipped} datasets unavailable")
    for k, v in db.stats().items():
        print(f"  {k:<14} {v}")


if __name__ == "__main__":
    main()
