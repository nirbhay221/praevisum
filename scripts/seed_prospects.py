"""Prospects to look at without spending anything.

WHY THIS EXISTS

`prospect.sweep_prospects` costs two paid searches per business it considers:
one to find them, one to read what the public has said about them. That is the
right design for real use and the wrong thing to run repeatedly while building
a demonstration, so this writes a handful of rows in the shape the sweep
produces.

WHAT IS REAL AND WHAT IS NOT

The businesses and the review text here are invented, and `source` says so on
every row, so nothing can later mistake them for something the sweep found.

Everything the feature actually decides is real and is computed, not seeded:
the distress vocabulary comes from our own work orders and complaints, the
matcher picks the sentence out of the review text, and the gates run
unmodified. The line types are the point of the exercise: two of these five
are mobiles and cannot be rung by an artificial voice whoever answers them,
one has asked us to stop, and the honest result is that a five-business sweep
yields two calls.

That ratio is not pessimism. Most small restaurants publish a mobile, and a
prospecting tool that pretends otherwise is one that gets its owner fined.

    python -m scripts.seed_prospects
"""

from __future__ import annotations

import uuid
from datetime import datetime

from src import db, linetype, prospect

DEALER = "D-REF"

# name, kind, address, phone, line type, and what the public said
BUSINESSES = [
    ("Riverbend Diner", "restaurant", "1204 River Drive, Davenport, IA",
     "+15635550101", "landline",
     "Food was good but the walk-in must be struggling, the salads were not "
     "cold and there was water pooling by the freezer door. Third visit this "
     "summer with the same thing."),

    ("Brady Street Cafe", "cafe", "2300 Brady Street, Davenport, IA",
     "+15635550102", "landline",
     "Lovely coffee. They apologised that the display fridge is showing an "
     "error code and keeps shutting down, so no cold drinks today."),

    ("Harbour Fish House", "restaurant", "88 Front Street, Bettendorf, IA",
     "+15635550103", "mobile",
     "Great fish. Staff said the freezer has been down since Friday and they "
     "are running off ice, temp climbing every night."),

    ("The Lantern Room", "restaurant", "515 Main Street, Rock Island, IL",
     "+15635550104", "mobile",
     "Nice room. The ice machine was out again and the compressor is running "
     "constantly, you can hear it from the dining room."),

    ("Corner Grocers", "grocery", "77 Locust Street, Davenport, IA",
     "+15635550105", "landline",
     "Handy shop. The chest freezer has frost building on the coil and half "
     "the shelf was defrosting."),
]

# One of them has already told us to stop. It stays in the book and is never
# rung, which is the only correct behaviour and the one worth demonstrating.
ASKED_US_TO_STOP = "+15635550105"


def load() -> dict:
    db.init()

    words = prospect.distress_words(DEALER)
    if len(words) < 5:
        return {"ok": False,
                "why": "no complaint or work order history for D-REF, so "
                       "there is no vocabulary to match against"}

    # Line types are seeded into the cache rather than looked up, so this
    # script never spends money either.
    with db.txn() as c:
        for _, _, _, phone, kind, _ in BUSINESSES:
            c.execute(
                """INSERT INTO line_type_cache
                     (e164,line_type,carrier,checked_on,source)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(e164) DO UPDATE SET
                     line_type=excluded.line_type, source=excluded.source""",
                (phone, kind, "seeded",
                 datetime.now().isoformat(timespec="seconds"),
                 "seeded for demonstration"))

    linetype.stop_calling(ASKED_US_TO_STOP, asked_by="the owner",
                          note="asked us not to ring again")

    written, no_signal = [], []
    for name, kind, address, phone, _line, said in BUSINESSES:
        sig = prospect.read_the_signal(said, words)
        if not sig["signal"]:
            no_signal.append(name)
            continue

        gate = prospect.may_we_approach(phone, "America/Chicago",
                                        at="2026-08-29T11:00:00",
                                        allow_lookup=False)

        pid = f"P-{uuid.uuid4().hex[:8].upper()}"
        with db.txn() as c:
            c.execute(
                """INSERT INTO prospects
                     (id,dealer_id,name,kind,address,phone_e164,line_type,
                      source,found_on,signal,signal_kind,signal_score,
                      signal_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(dealer_id,phone_e164) DO UPDATE SET
                     signal=excluded.signal,
                     signal_score=excluded.signal_score,
                     signal_seen=excluded.signal_seen""",
                (pid, DEALER, name, kind, address, phone,
                 gate.get("line_type"), "seeded for demonstration",
                 datetime.now().date().isoformat(),
                 ", ".join(sig["terms"]), "public_complaint",
                 sig["score"], sig["quote"]))

        written.append({
            "name": name, "score": sig["score"],
            "may_call": gate["may_call"], "why": gate.get("why", ""),
            "terms": sig["terms"], "quote": sig["quote"],
        })

    return {"ok": True, "written": written, "no_signal": no_signal,
            "vocabulary": words[:10]}


if __name__ == "__main__":
    out = load()
    if not out.get("ok"):
        print(out["why"])
        raise SystemExit(1)

    print(f"  matched against {len(out['vocabulary'])} terms taken from our "
          "own work orders:")
    print(f"    {', '.join(out['vocabulary'])}")
    print()
    for p in out["written"]:
        mark = "RING" if p["may_call"] else "no  "
        print(f"  [{mark}] {p['name']:<22} {p['score']:.2f}  "
              f"{', '.join(p['terms'][:4])}")
        if not p["may_call"]:
            print(f"           {p['why']}")
    if out["no_signal"]:
        print(f"  nothing public suggested a need: {', '.join(out['no_signal'])}")

    callable_now = sum(1 for p in out["written"] if p["may_call"])
    print()
    print(f"  {callable_now} of {len(BUSINESSES)} may lawfully be rung.")
