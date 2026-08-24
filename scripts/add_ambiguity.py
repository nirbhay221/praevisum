"""Make the corpus honest about how ambiguous real faults are.

The first generator mapped one symptom to one cause, so every distribution came
back 100% certain and the reasoning had nothing to weigh. That is not how a
service desk works. "Not holding temperature overnight" is a defrost fault most
of the time, an evaporator fan sometimes, a door seal occasionally, and once in
a while a control board that costs six times as much and takes nine days.

That spread is the whole reason a senior technician's van looks different from
a junior's, so the data has to contain it.

This rewrites a slice of existing closed repairs onto alternative causes, in
proportions taken from how these faults actually distribute. Nothing else about
the jobs changes: same machines, same customers, same dates.

    .venv/Scripts/python.exe scripts/add_ambiguity.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

RNG = random.Random(31337)

# symptom fragment -> alternative causes and how often each really is the answer
AMBIGUITY = {
    "not holding temp overnight": [
        (0.45, "defrost termination thermostat open; ice build-up on coil",
         ["P-DEFROSTTHE"]),
        (0.25, "evaporator fan motor seized, coil iced over behind it",
         ["P-EVAPFAN"]),
        (0.18, "door gasket perished, warm air infiltration overnight",
         ["P-DOORGASKET"]),
        (0.12, "control board failed, defrost cycle never initiating",
         ["P-CONTROLBOA"]),
    ],
    "frost building on the coil": [
        (0.50, "termination thermostat failed and heater element pitted, replaced both",
         ["P-DEFROSTTHE", "P-DEFROSTHEA"]),
        (0.30, "defrost heater element open circuit",
         ["P-DEFROSTHEA"]),
        (0.20, "evaporator fan motor seized, no air across the coil",
         ["P-EVAPFAN"]),
    ],
    "warm at open, ice all over": [
        (0.40, "evaporator fan motor seized, coil iced over behind it", ["P-EVAPFAN"]),
        (0.35, "defrost termination thermostat open; ice build-up on coil",
         ["P-DEFROSTTHE"]),
        (0.25, "door mullion heater harness open, frame icing and door not sealing",
         ["P-MULLIONHAR"]),
    ],
    "temperature swinging": [
        (0.45, "thermostat sensor out of calibration, drifted 6 degrees",
         ["P-DEFROSTTHE"]),
        (0.30, "control board failed, erratic compressor relay output",
         ["P-CONTROLBOA"]),
        (0.25, "condenser packed with grease and lint, cleaned and recharged", []),
    ],
    "loud rattling noise": [
        (0.55, "condenser fan motor bearing gone", ["P-CONDFAN"]),
        (0.45, "evaporator fan motor bearing dry and knocking", ["P-EVAPFAN"]),
    ],
    "display showing an error code": [
        (0.60, "control board failed, no output to compressor relay",
         ["P-CONTROLBOA"]),
        (0.25, "thermostat sensor open circuit, board reporting a probe fault",
         ["P-DEFROSTTHE"]),
        (0.15, "compressor start capacitor failed, board locked out on overload",
         ["P-STARTCAPA"]),
    ],
    # IT dealer
    "screen has gone black": [
        (0.55, "LCD panel failed, backlight dead. Replaced the display assembly",
         ["IT-LCDPANEL"]),
        (0.30, "mainboard fault, no video output. Board swap under warranty",
         ["IT-MAINBOARD"]),
        (0.15, "display cable chafed at the hinge, reseated and replaced",
         ["IT-LCDPANEL"]),
    ],
    "keeps shutting itself off": [
        (0.45, "thermal shutdown, fan seized and heatsink packed with dust",
         ["IT-FANASSEMB"]),
        (0.35, "battery swollen and cutting out under load, replaced",
         ["IT-BATTERY"]),
        (0.20, "mainboard power delivery fault", ["IT-MAINBOARD"]),
    ],
    "running incredibly slowly": [
        (0.40, "RAM module intermittent, failed extended memory test, replaced",
         ["IT-RAM"]),
        (0.40, "SSD failing SMART, cloned and replaced the drive", ["IT-SSD"]),
        (0.20, "thermal throttling, fan assembly clogged", ["IT-FANASSEMB"]),
    ],
}


def pick(options):
    r, acc = RNG.random(), 0.0
    for p, cause, parts in options:
        acc += p
        if r <= acc:
            return cause, parts
    return options[-1][1], options[-1][2]


def main() -> None:
    changed = 0
    with db.txn() as c:
        known = {r["sku"] for r in c.execute("SELECT sku FROM parts")}
        rows = c.execute(
            "SELECT id, reported_symptom, dealer_id FROM repairs").fetchall()

        for r in rows:
            sym = (r["reported_symptom"] or "").lower()
            match = next((v for k, v in AMBIGUITY.items() if k in sym), None)
            if not match:
                continue
            cause, parts = pick(match)
            parts = [p for p in parts if p in known]
            c.execute(
                """UPDATE repairs SET found_cause=?, parts_consumed=?, embedding=NULL
                   WHERE id=?""",
                (cause, ",".join(parts), r["id"]))
            changed += 1

    print(f"  rewrote {changed} repairs onto realistic alternative causes")

    with db.connect() as c:
        print("\n  how ambiguous the corpus is now:")
        for row in c.execute(
                """SELECT reported_symptom, COUNT(DISTINCT found_cause) causes,
                          COUNT(*) n
                   FROM repairs GROUP BY reported_symptom
                   HAVING causes > 1 ORDER BY n DESC LIMIT 6"""):
            print(f"    {row['n']:>3} jobs, {row['causes']} different causes  "
                  f"{row['reported_symptom'][:46]}")


if __name__ == "__main__":
    main()
