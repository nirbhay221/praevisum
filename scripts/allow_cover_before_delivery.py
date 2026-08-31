"""Let extended cover exist before the machine does.

`cover_sold.asset_id` was NOT NULL, written before it was clear that cover is
sold at the till and a machine only becomes an asset on delivery. A customer
saying yes to three extra years while buying had nowhere to be recorded, and
the desk told them so on a live call.

SQLite cannot drop a NOT NULL, so the table is rebuilt and the rows copied.
Safe to run twice: it checks first.

    python -m scripts.allow_cover_before_delivery
"""

from __future__ import annotations

from src import db


def already_allowed() -> bool:
    with db.connect() as c:
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                        "AND name='cover_sold'").fetchone()
    if not row:
        return False
    sql = row["sql"] or ""
    return "asset_id     TEXT REFERENCES" in sql or (
        "asset_id" in sql and "asset_id     TEXT NOT NULL" not in sql
        and "asset_id TEXT NOT NULL" not in sql)


def load() -> dict:
    if already_allowed():
        return {"ok": True, "changed": False,
                "why": "cover can already be sold before delivery"}

    with db.connect() as c:
        before = c.execute("SELECT COUNT(*) n FROM cover_sold").fetchone()["n"]
        cols = [r[1] for r in c.execute("PRAGMA table_info(cover_sold)")]
    names = ",".join(cols)

    with db.txn() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("ALTER TABLE cover_sold RENAME TO cover_sold_old")

    db.init()

    with db.txn() as c:
        c.execute(f"INSERT INTO cover_sold ({names}) "
                  f"SELECT {names} FROM cover_sold_old")
        c.execute("DROP TABLE cover_sold_old")
        c.execute("PRAGMA foreign_keys=ON")

    with db.connect() as c:
        after = c.execute("SELECT COUNT(*) n FROM cover_sold").fetchone()["n"]

    return {"ok": after == before, "changed": True,
            "rows_before": before, "rows_after": after}


if __name__ == "__main__":
    out = load()
    print("  " + (out["why"] if not out["changed"]
                  else f"rebuilt cover_sold: {out['rows_before']} in, "
                       f"{out['rows_after']} out"))
