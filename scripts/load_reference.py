"""Load the public federal equipment data into SQLite.

Only equipment that gets a site visit. The test is one question: does a human
travel to the broken thing, having already decided what to bring? That keeps
commercial kitchen, HVAC, boilers, water heaters, enterprise IT under onsite
warranty, copiers and UPS. It excludes televisions, telephones, ceiling fans
and light bulbs, because nobody dispatches a van for those.

    .venv/Scripts/python.exe scripts/load_reference.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

# dataset id -> (our category, human name)
ENERGY_STAR = {
    # commercial kitchen
    "wati-2tfp": ("refrigeration", "Commercial Refrigerators and Freezers"),
    "g242-ysjw": ("refrigeration", "Laboratory Grade Refrigerators and Freezers"),
    "nak5-fsjf": ("refrigeration", "Commercial Ice Machines"),
    "pk8q-dim8": ("kitchen", "Commercial Dishwashers"),
    "c8av-ccf7": ("kitchen", "Commercial Ovens"),
    "wyw6-sr4d": ("kitchen", "Commercial Hot Food Holding Cabinets"),
    "vtsv-aq9u": ("kitchen", "Commercial Steam Cookers"),
    "edi8-b5vk": ("kitchen", "Commercial Fryers"),
    "nw5s-r5ca": ("kitchen", "Commercial Griddles"),
    "6xa2-5c2t": ("kitchen", "Commercial Coffee Brewers"),
    "nt9t-yxu3": ("kitchen", "Commercial Electric Cooktops"),
    "9g6r-cpdt": ("laundry", "Commercial Clothes Washers"),
    "j624-u8ux": ("vending", "Vending Machines"),
    "qsc8-7f7k": ("vending", "Water Coolers"),
    # climate and plant
    "e4mh-a2u3": ("hvac", "Light Commercial HVAC"),
    "3393-mxju": ("plant", "Commercial Boilers"),
    "xmq6-bm79": ("plant", "Commercial Water Heaters"),
    # IT, serviced under onsite warranty
    "rxdj-2c88": ("it", "Computers"),
    "qifb-fcj2": ("it", "Enterprise Servers"),
    "3uec-2gqf": ("it", "Data Center Storage Block IO"),
    "put7-uu67": ("it", "Data Center Storage File IO"),
    "n8cx-m62r": ("it", "Large Network Equipment"),
    "t2v6-g4nf": ("it", "Imaging Equipment"),
    "ifxy-2uty": ("it", "Uninterruptible Power Supplies"),
    "wjtt-3zwd": ("medical", "Medical Imaging Equipment"),
}

# the field names vary between datasets, so try several
CAP = ("total_volume_cu_ft", "capacity_cu_ft", "capacity", "total_display_area_sq_ft",
       "rated_storage_volume_cu_ft", "storage_volume_gallons")
KWH = ("reported_daily_energy_consumption_kwh_day", "daily_energy_consumption_kwh_day",
       "annual_energy_use_kwh_yr", "measured_energy_consumption_kwh_yr")
TYPE = ("product_type", "type", "product_class", "equipment_type", "category")
REFRIG = ("refrigerant_type", "refrigerant", "refrigerant_with_gwp")
DEFROST = ("defrost_type", "defrost")
CERT = ("date_certified", "date_available_on_market", "certification_date")


def first(rec: dict, keys) -> str | None:
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", []):
            return str(v)
    return None


def fetch(sid: str) -> list[dict]:
    url = f"https://data.energystar.gov/resource/{sid}.json?%24limit=50000"
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def load_equipment() -> None:
    total = 0
    with db.txn() as c:
        for sid, (category, dataset) in ENERGY_STAR.items():
            try:
                rows = fetch(sid)
            except Exception as e:
                print(f"  skip {dataset}: {type(e).__name__}")
                continue

            n = 0
            for r in rows:
                brand = (r.get("brand_name") or r.get("brand") or "").strip()
                model = (r.get("model_number") or r.get("model_name") or "").strip()
                if not brand or not model:
                    continue
                try:
                    c.execute(
                        """INSERT OR IGNORE INTO equipment
                           (source,dataset,category,brand,model_number,product_type,
                            defrost_type,refrigerant,capacity,daily_kwh,certified_on,raw)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("energystar", dataset, category, brand, model,
                         first(r, TYPE), first(r, DEFROST), first(r, REFRIG),
                         first(r, CAP),
                         float(first(r, KWH)) if (first(r, KWH) or "").replace(".", "", 1).isdigit() else None,
                         (first(r, CERT) or "")[:10] or None,
                         json.dumps(r, separators=(",", ":"))),
                    )
                    n += 1
                except Exception:
                    pass
            total += n
            print(f"  {n:>6}  {dataset}")
            time.sleep(0.2)
    print(f"\n  {total:,} equipment rows")


def load_recalls() -> None:
    n = 0
    with db.txn() as c:
        for term in ("refrigerator", "freezer", "ice maker", "dishwasher",
                     "oven", "fryer", "air conditioner", "water heater",
                     "laptop", "battery", "power supply"):
            try:
                url = ("https://www.saferproducts.gov/RestWebServices/Recall"
                       f"?format=json&ProductName={urllib.parse.quote(term)}")
                with urllib.request.urlopen(url, timeout=90) as r:
                    data = json.load(r)
            except Exception as e:
                print(f"  skip recalls '{term}': {type(e).__name__}")
                continue

            for rec in data:
                try:
                    c.execute(
                        """INSERT OR IGNORE INTO recalls
                           (recall_number,recall_date,title,hazard,remedy,brands,models,url)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (rec.get("RecallNumber"), (rec.get("RecallDate") or "")[:10],
                         rec.get("Title"),
                         "; ".join(h.get("Name", "") for h in rec.get("Hazards", []))[:500],
                         "; ".join(x.get("Name", "") for x in rec.get("Remedies", []))[:300],
                         "; ".join(p.get("Name", "") for p in rec.get("Products", []))[:400],
                         "; ".join(p.get("Model", "") for p in rec.get("Products", []) if p.get("Model"))[:400],
                         rec.get("URL")),
                    )
                    n += 1
                except Exception:
                    pass
            time.sleep(0.3)
    print(f"  {n:,} recall rows")


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (used in load_recalls)

    print(f"database: {db.DB_PATH}")
    db.init()
    print("\nequipment (public federal certification data):")
    load_equipment()
    print("\nrecalls (CPSC):")
    load_recalls()
    print("\n--- loaded ---")
    for k, v in db.stats().items():
        print(f"  {k:<14} {v}")
