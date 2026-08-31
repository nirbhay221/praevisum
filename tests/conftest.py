"""A real database, built from the real schema, thrown away afterwards.

Deliberately not mocks. Every bug these tests exist to catch was a bug about
what the database actually contained or actually enforced: a foreign key that
let a promise be half-written, a fitment row that put laptop parts on a UPS, a
retrieval index that answered a refrigeration caller with Dell repairs. A mock
of the database would have agreed with the code and passed.

The schema files are applied in the same order the deploy applies them, so a
column added to schema.sql but forgotten in schema_tenant.sql fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REF = "D-REF"
IT = "D-IT"


@pytest.fixture()
def dbfile(tmp_path, monkeypatch):
    """A fresh database for one test, seeded with two dealers."""
    from src import db

    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", path)

    # The same call the deploy makes. Building the test database any other way
    # would let the schema files drift from what production runs, which is
    # precisely the bug this fixture found on its first run: `equipment` and
    # `recalls` existed only in the live file, so a rebuild from source could
    # not take a single asset.
    db.init()
    _seed(db)
    return path


def _seed(db) -> None:
    """The smallest world in which the interesting failures can happen.

    Two dealers who must never see each other's data, two machines of different
    families, and parts that fit one and not the other.
    """
    with db.txn() as c:
        c.executemany(
            """INSERT OR REPLACE INTO dealers (id,name,trade,phone_e164,families)
               VALUES (?,?,?,?,?)""",
            [(REF, "Coldline Refrigeration Service", "refrigeration",
              "+13095550000", "reach-in freezer,walk-in cooler"),
             (IT, "Northgate IT Services", "it",
              "+15635550000", "laptop,ups,printer")])

        c.executemany(
            """INSERT INTO accounts (id,dealer_id,name,kind)
               VALUES (?,?,?,?)""",
            [("A-1", REF, "Marino's Kitchen", "business"),
             ("A-2", IT, "Fairview Clinic", "business")])

        c.executemany(
            """INSERT INTO sites (id,account_id,label,address,lat,lon)
               VALUES (?,?,?,?,?,?)""",
            [("S-1", "A-1", "Main kitchen", "12 Adams St", 41.52, -90.57),
             ("S-2", "A-2", "Reception", "40 Brady St", 41.52, -90.58)])

        # A phone number is how this product identifies anybody: the caller is
        # resolved from it before a word is spoken, a technician is recognised
        # by it whichever channel they reply on, and a follow-up has nowhere to
        # go without one. The fixture had accounts and sites and no people at
        # all, so anything touching identity had to skip.
        c.executemany(
            """INSERT INTO contacts (id,account_id,name,role)
               VALUES (?,?,?,?)""",
            [("CT-1", "A-1", "Dana Marino", "owner"),
             ("CT-2", "A-2", "Priya Raman", "office manager")])

        c.executemany(
            """INSERT INTO phones (contact_id,e164,label)
               VALUES (?,?,?)""",
            [("CT-1", "+13095550101", "mobile"),
             ("CT-2", "+15635550202", "mobile")])

        c.executemany(
            """INSERT INTO assets
               (id,site_id,manufacturer,model_number,family)
               VALUES (?,?,?,?,?)""",
            [("AS-FREEZER", "S-1", "Traulsen", "G12010", "reach-in freezer"),
             ("AS-LAPTOP", "S-2", "Dell", "Latitude 5440", "laptop"),
             ("AS-UPS", "S-2", "MSI", "PRO DP10 A14MG", "ups")])

        c.executemany(
            """INSERT INTO parts
               (sku,dealer_id,name,unit_cost,lead_time_days,families)
               VALUES (?,?,?,?,?,?)""",
            [("P-DEFROSTTHE", REF, "Defrost termination thermostat", 68.0, 2,
              "reach-in freezer,walk-in cooler"),
             ("P-EVAPFAN", REF, "Evaporator fan motor", 142.0, 3,
              "reach-in freezer,walk-in cooler"),
             ("P-CONTROLBOA", REF, "Electronic control board", 386.0, 9,
              "reach-in freezer,walk-in cooler"),
             ("IT-LCDPANEL", IT, "LCD display assembly", 210.0, 4, "laptop"),
             ("IT-BATTERY", IT, "Replacement battery", 95.0, 2, "laptop")])

        # Fitments are the join that decides what can go on a machine. The
        # laptop parts are deliberately given a fitment row against the UPS as
        # well, which is exactly the bad data that shipped once.
        c.executemany(
            """INSERT INTO fitments (sku,manufacturer,model_pattern)
               VALUES (?,?,?)""",
            [("P-DEFROSTTHE", "Traulsen", "G120%"),
             ("P-EVAPFAN", "Traulsen", "G120%"),
             ("P-CONTROLBOA", "Traulsen", "G120%"),
             ("IT-LCDPANEL", "Dell", "Latitude%"),
             ("IT-BATTERY", "Dell", "Latitude%"),
             ("IT-LCDPANEL", "MSI", "PRO DP10%"),
             ("IT-BATTERY", "MSI", "PRO DP10%")])

        c.executemany(
            """INSERT INTO stock_locations (id,dealer_id,label,kind)
               VALUES (?,?,?,?)""",
            [("L-REF-WH", REF, "Warehouse", "warehouse"),
             ("L-REF-VAN1", REF, "Van 1", "van"),
             ("L-IT-WH", IT, "Warehouse", "warehouse")])

        c.executemany(
            "INSERT INTO stock (sku,location_id,on_hand) VALUES (?,?,?)",
            [("P-DEFROSTTHE", "L-REF-WH", 6),
             ("P-EVAPFAN", "L-REF-WH", 2),
             ("P-CONTROLBOA", "L-REF-WH", 0),
             ("P-EVAPFAN", "L-REF-VAN1", 1),
             ("IT-LCDPANEL", "L-IT-WH", 3),
             ("IT-BATTERY", "L-IT-WH", 4)])

        c.executemany(
            """INSERT INTO technicians
               (id,dealer_id,name,phone,van_location,home_base,lat,lon)
               VALUES (?,?,?,?,?,?,?,?)""",
            [("T-1", REF, "Ray Delgado", "+13095551001", "L-REF-VAN1",
              "Davenport", 41.50, -90.55),
             ("T-2", IT, "Sam Ortiz", "+15635551002", None,
              "Davenport", 41.51, -90.56)])


@pytest.fixture()
def corpus(dbfile):
    """Closed repairs for both dealers, and the indexes built over them.

    The two corpora describe genuinely different worlds. If retrieval ever
    crosses them a refrigeration caller hears about LCD panels, which is the
    leak this fixture exists to keep catching.
    """
    from src import db, memory

    rows = [
        # dealer, id, asset, mfr, model, symptom, cause, parts
        (REF, "R-1", "AS-FREEZER", "Traulsen", "G12010",
         "not holding temp overnight",
         "defrost termination thermostat open; ice build-up on coil",
         "P-DEFROSTTHE"),
        (REF, "R-2", "AS-FREEZER", "Traulsen", "G12010",
         "not holding temp overnight",
         "defrost termination thermostat open; ice build-up on coil",
         "P-DEFROSTTHE"),
        (REF, "R-3", "AS-FREEZER", "Traulsen", "G12010",
         "not holding temp overnight",
         "evaporator fan motor seized, coil iced over behind it",
         "P-EVAPFAN"),
        (REF, "R-4", "AS-FREEZER", "Traulsen", "G12010",
         "loud rattling noise from the back",
         "condenser fan motor bearing gone", "P-EVAPFAN"),
        (IT, "R-5", "AS-LAPTOP", "Dell", "Latitude 5440",
         "screen has gone black but you can hear it running",
         "LCD panel failed, backlight dead. Replaced the display assembly",
         "IT-LCDPANEL"),
        (IT, "R-6", "AS-LAPTOP", "Dell", "Latitude 5440",
         "keeps shutting itself off",
         "battery swollen and cutting out under load, replaced",
         "IT-BATTERY"),
    ]
    with db.txn() as c:
        for d, rid, asset, mfr, model, sym, cause, parts in rows:
            c.execute(
                """INSERT INTO repairs
                   (id,dealer_id,asset_id,manufacturer,model_number,family,
                    reported_symptom,found_cause,parts_consumed,
                    labor_hours,first_visit_fix,closed_on,technician_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, d, asset, mfr, model,
                 "reach-in freezer" if d == REF else "laptop",
                 sym, cause, parts, 1.5, 1,
                 "2026-05-01", "T-1" if d == REF else "T-2"))

    memory.INDEXES.clear()
    memory.load_from_db()
    return memory.INDEXES

@pytest.fixture(autouse=True)
def _no_routing_leaks_between_tests():
    """The routed vendor is a ContextVar, and one test routing to a vendor
    left every test after it inheriting the routing: four offer tests failed
    in the suite and passed alone.

    That is the same mechanism a live call would use to inherit another
    call's vendor, which is why the bridge now states it at the start of every
    call rather than only setting it. This keeps the suite honest about it.
    """
    from src.tenancy import routed_to

    routed_to("")
    yield
    routed_to("")
