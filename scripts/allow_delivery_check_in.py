"""Let `followups` hold a delivery check-in.

WHY A SCRIPT AND NOT A SCHEMA CHANGE

`followups.kind` carries a CHECK listing the kinds that exist, which is right:
`followup.render()` dispatches on it and refuses to send a kind it has no
wording for, so an unknown value would become a message nobody wrote.

SQLite cannot ALTER a CHECK. The constraint is part of the table definition,
so widening it means rebuilding the table and copying the rows. On a fresh
database the updated schema file is enough; on the live VM, which already
holds real follow-ups, this is the only route.

Safe to run twice: it checks the constraint before touching anything.

    python -m scripts.allow_delivery_check_in
"""

from __future__ import annotations

from src import db

WANT = "delivery_check_in"


def already_allowed() -> bool:
    with db.connect() as c:
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='followups'").fetchone()
    return bool(row) and WANT in (row["sql"] or "")


def load() -> dict:
    if already_allowed():
        return {"ok": True, "changed": False,
                "why": f"{WANT} is already an allowed kind"}

    with db.connect() as c:
        before = c.execute("SELECT COUNT(*) n FROM followups").fetchone()["n"]
        cols = [r[1] for r in c.execute("PRAGMA table_info(followups)")]

    names = ",".join(cols)

    with db.txn() as c:
        # Foreign keys OFF for the swap, or the rename cascades into the rows
        # that point at this table. Restored immediately after.
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("ALTER TABLE followups RENAME TO followups_old")

    db.init()                       # rebuilds followups from the schema file

    with db.txn() as c:
        c.execute(f"INSERT INTO followups ({names}) "
                  f"SELECT {names} FROM followups_old")
        c.execute("DROP TABLE followups_old")
        c.execute("PRAGMA foreign_keys=ON")

    with db.connect() as c:
        after = c.execute("SELECT COUNT(*) n FROM followups").fetchone()["n"]

    return {"ok": after == before, "changed": True,
            "rows_before": before, "rows_after": after}


if __name__ == "__main__":
    out = load()
    if not out["changed"]:
        print("  " + out["why"])
    else:
        print(f"  rebuilt followups: {out['rows_before']} rows in, "
              f"{out['rows_after']} out")
        if not out["ok"]:
            raise SystemExit("  ROW COUNT CHANGED, investigate before deploying")
