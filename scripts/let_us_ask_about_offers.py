"""Let `followups` carry the offer-consent question.

WHY THIS IS A SCRIPT AND NOT A SCHEMA LINE

`followups.kind` has a CHECK constraint naming the kinds allowed, and SQLite
cannot alter a CHECK in place -- the documented route is to rebuild the table.
So adding a new kind of follow-up means this, once, rather than a line in a
.sql file that would silently do nothing on a database that already exists.

WHAT IT COST TO FIND OUT

`offer_consent` was added as a kind and the CHECK was not, so every insert
failed. And `_queue` catches the failure like this:

    if "unique" in str(e).lower() or "constraint" in str(e).lower():
        return {"ok": True, "already_queued": True}

"CHECK constraint failed" contains the word constraint. So a row that could
never be written was reported as one that already existed: the console showed
nothing waiting, the customer was never asked, and the code said it was fine.
That handler is fixed too -- only a UNIQUE violation means "already queued".

    python scripts/let_us_ask_about_offers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

KINDS = ("missed_call", "dropped_call", "after_visit", "escalation",
         "review_ask", "delivery_check_in", "offer_consent")


def main() -> None:
    with db.connect() as c:
        sql = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='followups'"
        ).fetchone()
        if sql is None:
            print("  no followups table")
            return
        if "offer_consent" in (sql["sql"] or ""):
            print("  followups already allows offer_consent")
            return
        cols = [r[1] for r in c.execute("PRAGMA table_info(followups)")]
        n = c.execute("SELECT COUNT(*) FROM followups").fetchone()[0]

    print(f"  rebuilding followups ({n} rows) to allow offer_consent")

    kinds = ",".join(f"'{k}'" for k in KINDS)
    names = ",".join(cols)

    # The documented rebuild: new table, copy, drop, rename, indexes back.
    # Foreign keys off for the swap only, and everything inside one
    # transaction so a failure leaves the original in place.
    with db.connect() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        try:
            c.execute("BEGIN")
            c.execute(f"""
                CREATE TABLE followups_new (
                    id            TEXT PRIMARY KEY,
                    dealer_id     TEXT,
                    kind          TEXT NOT NULL CHECK (kind IN ({kinds})),
                    account_id    TEXT REFERENCES accounts(id),
                    contact_id    TEXT REFERENCES contacts(id),
                    phone         TEXT NOT NULL,
                    from_call     TEXT,
                    work_order_id TEXT,
                    context       TEXT,
                    due_after     TEXT,
                    status        TEXT NOT NULL DEFAULT 'queued',
                    sent_at       TEXT,
                    sent_via      TEXT,
                    reply         TEXT,
                    created_at    TEXT
                )""")
            c.execute(f"INSERT INTO followups_new ({names}) "
                      f"SELECT {names} FROM followups")
            c.execute("DROP TABLE followups")
            c.execute("ALTER TABLE followups_new RENAME TO followups")
            c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_followups_once
                         ON followups(kind, phone,
                                      COALESCE(from_call, work_order_id, ''))""")
            c.execute("COMMIT")
        except Exception as e:
            c.execute("ROLLBACK")
            print(f"  rebuild failed, nothing changed: {type(e).__name__}: {e}")
            return
        finally:
            c.execute("PRAGMA foreign_keys=ON")

    with db.connect() as c:
        after = c.execute("SELECT COUNT(*) FROM followups").fetchone()[0]
        ok = "offer_consent" in (c.execute(
            "SELECT sql FROM sqlite_master WHERE name='followups'"
        ).fetchone()["sql"] or "")
    print(f"  done: {after} rows kept, offer_consent allowed = {ok}")


if __name__ == "__main__":
    main()
