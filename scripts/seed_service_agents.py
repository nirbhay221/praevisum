"""Give the furniture and display vendors people who can actually attend.

Both were added with stock, real published warranty terms and a real BLS wage,
and no technicians at all. They could sell and could not serve, which a live
call would have found the first time anybody asked for a chair to be looked at.

WHY NO CERTIFICATES ON THESE SIX

Because the trades genuinely differ. Refrigeration needs EPA 608 to open a
sealed circuit and it is a legal requirement, so cover.NEEDS_CERT lists the
refrigeration families and can_work_on refuses anybody without a valid one.
Furniture installation and AV installation carry no federal licence of that
kind, which is why those families are absent from NEEDS_CERT and can_work_on
correctly answers "no refrigerant certification is required for this family".

Inventing a certificate for them to hold would be inventing a fact.

Run: python -m scripts.seed_service_agents
"""

from __future__ import annotations

from src import db

PEOPLE = [
    ("T-FURN01", "Marta Ilves",    "+13095552201", "Davenport",   "D-FURN"),
    ("T-FURN02", "Dez Okonkwo",    "+13095552202", "Moline",      "D-FURN"),
    ("T-FURN03", "Ruth Sandoval",  "+13095552203", "Bettendorf",  "D-FURN"),
    ("T-AV01",   "Nils Bergstrom", "+13095552301", "Rock Island", "D-AV"),
    ("T-AV02",   "Priya Raghavan", "+13095552302", "Davenport",   "D-AV"),
    ("T-AV03",   "Cal Whitmore",   "+13095552303", "Moline",      "D-AV"),
]


def load() -> dict:
    db.init()
    added = []
    with db.txn() as c:
        for tid, name, phone, base, dealer in PEOPLE:
            if c.execute("SELECT id FROM technicians WHERE id=?",
                         (tid,)).fetchone():
                continue
            c.execute(
                "INSERT INTO technicians (id,name,phone,home_base,active,dealer_id) "
                "VALUES (?,?,?,?,1,?)", (tid, name, phone, base, dealer))
            added.append(f"{name} ({dealer})")

    with db.connect() as c:
        per = {r["dealer_id"]: r["n"] for r in c.execute(
            "SELECT dealer_id, COUNT(*) n FROM technicians WHERE active=1 "
            "GROUP BY dealer_id")}
    return {"added": added, "per_vendor": per}


if __name__ == "__main__":
    out = load()
    for d, n in sorted(out["per_vendor"].items()):
        print(f"  {d:<8} {n} technicians")
    print("added:", out["added"] or "none, already present")
