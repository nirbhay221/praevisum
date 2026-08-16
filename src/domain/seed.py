"""Seed data for a mid-size commercial refrigeration dealer.

Five manufacturers across commercial and residential so the brand-agnostic
claim is visible, but the demo drives one unit deeply.

The repair history is the point. It is shaped so that the honest answer to
"what does this fault usually need" is *two* parts, not one - because the
second part is exactly what a technician forgets, and forgotten parts are 51%
of failed first visits.
"""

from __future__ import annotations

from .models import Customer, Part, Repair, Technician, Unit
from .store import STORE


def load() -> None:
    for c in [
        Customer("c-101", "Pearl Street Kitchen", "+13095550101",
                 "412 Pearl St, Moline IL", "sms", 41.5067, -90.5151),
        Customer("c-102", "Rivertown Tap", "+13095550102",
                 "88 River Dr, Moline IL", "whatsapp", 41.5120, -90.5250),
        Customer("c-103", "S. Ortega residence", "+13095550103",
                 "77 Elm Ave, Rock Island IL", "sms", 41.5095, -90.5787),
    ]:
        STORE.customers[c.id] = c

    for u in [
        # the demo unit
        Unit("TRL-G24-8871", "c-101", "Traulsen", "G22010",
             "reach-in freezer", "2019-04-02", "kitchen, back wall"),
        Unit("TRU-WI-4402", "c-101", "True", "TWT-48F",
             "walk-in cooler", "2021-08-15", "rear dock"),
        Unit("BEV-DD68-1190", "c-102", "Beverage-Air", "DD68HC-1-S",
             "back bar cooler", "2022-02-01", "front bar"),
        # second unit of the SAME model as Pearl Street's, at a different site.
        # This is what makes cross-site learning demonstrable.
        Unit("TRL-G24-9903", "c-102", "Traulsen", "G22010",
             "reach-in freezer", "2020-09-14", "kitchen pass"),
        Unit("HOS-KM515-7781", "c-102", "Hoshizaki", "KM-515MAJ",
             "ice machine", "2020-11-20", "service corridor"),
        # residential, proves the engine is not commercial-only
        Unit("WHP-WRS588-2210", "c-103", "Whirlpool", "WRS588FIHZ",
             "side-by-side refrigerator", "2023-06-10", "kitchen"),
    ]:
        STORE.units[u.serial] = u

    for p in [
        Part("TRL-329410", "Defrost termination thermostat", ("G22", "G24"), 4, 2, 62.40),
        Part("TRL-334862", "Defrost heater element", ("G22", "G24"), 2, 3, 148.00),
        # deliberately scarce - this is the part the demo pulls out from under us
        Part("TRL-401255", "Defrost timer / control board", ("G22", "G24"), 1, 9, 386.75),
        Part("TRL-220118", "Evaporator fan motor", ("G22", "G24"), 6, 2, 94.10),
        Part("TRL-556700", "Door mullion heater harness", ("G22", "G24"), 3, 4, 118.30),
        Part("TRU-988201", "Condenser fan motor", ("TWT",), 3, 4, 132.00),
        Part("TRU-771043", "Door gasket, 48in", ("TWT",), 5, 2, 88.50),
        Part("BEV-556120", "Thermostat control", ("DD68",), 2, 5, 71.25),
        Part("HOS-2A3456", "Water inlet valve", ("KM-515",), 3, 3, 119.00),
        Part("WHP-W11024", "Defrost control board", ("WRS588",), 2, 4, 143.90),
        Part("WHP-W10919", "Evaporator fan motor", ("WRS588",), 4, 2, 76.55),
    ]:
        STORE.parts[p.sku] = p

    for t in [
        Technician("t-01", "Curtis Okafor", "+13095559001",
                   ("reach-in freezer", "walk-in cooler", "back bar cooler"),
                   "Moline IL", ("TRL-220118", "TRU-771043"), 41.5040, -90.5100),
        Technician("t-02", "Marisol Vance", "+13095559002",
                   ("ice machine", "walk-in cooler"), "Davenport IA",
                   ("HOS-2A3456",), 41.5236, -90.5776),
        Technician("t-03", "Ben Kalita", "+13095559003",
                   ("side-by-side refrigerator", "reach-in freezer"),
                   "Rock Island IL", (), 41.5095, -90.5787),
    ]:
        STORE.technicians[t.id] = t

    STORE.repairs.extend([
        # --- this exact unit, twice before, same underlying fault ---
        Repair("r-9001", "TRL-G24-8871", "Traulsen", "G22010",
               "not holding temp overnight, fine during service",
               "dEF", "defrost termination thermostat open; ice build-up on coil",
               ("TRL-329410", "TRL-220118"), 2.5, "2024-11-12", "t-01"),
        Repair("r-9002", "TRL-G24-8871", "Traulsen", "G22010",
               "frost on coil, temp climbing at night",
               "dEF", "termination thermostat failed again; heater element pitted, "
                      "replaced both. Thermostat alone did not hold last time",
               ("TRL-329410", "TRL-334862"), 3.0, "2025-07-28", "t-01"),
        # --- same model, other sites: the pattern that makes it a model fault ---
        Repair("r-9003", "TRL-G24-5540", "Traulsen", "G22010",
               "warm at open, ice on evaporator", "dEF",
               "termination thermostat; heater element also failed within 3 months "
               "on the prior call, fitted both this time",
               ("TRL-329410", "TRL-334862"), 2.75, "2025-03-04", "t-03"),
        Repair("r-9004", "TRL-G24-6612", "Traulsen", "G22010",
               "compressor short cycling", None, "condenser blocked with grease; cleaned",
               (), 1.25, "2025-05-19", "t-01"),
        # --- other brands, so history retrieval has to discriminate ---
        Repair("r-9010", "TRU-WI-4402", "True", "TWT-48F",
               "condenser fan noisy", None, "fan motor bearing", ("TRU-988201",),
               1.5, "2025-02-11", "t-02"),
        Repair("r-9020", "WHP-WRS588-2210", "Whirlpool", "WRS588FIHZ",
               "freezer cold, fridge warm", "dF",
               "defrost control board failed; evap fan seized",
               ("WHP-W11024", "WHP-W10919"), 1.75, "2025-09-30", "t-03"),
    ])

    # Everything the company has learned so far goes into the retrievable
    # corpus. From here on, every closed work order adds to it.
    from ..memory import INDEX
    for r in STORE.repairs:
        INDEX.add(r)


load()
