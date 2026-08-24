"""SQLite. One file, real transactions, survives a restart.

Two kinds of table live here and they behave differently:

  REFERENCE   equipment and recalls, loaded from public federal data. Read
              only at runtime, rebuilt by scripts/load_reference.py.

  OPERATIONAL work orders, reservations, repairs, transcripts. Written during
              calls, and the reason this file exists at all: until now every
              one of these was a Python dict that died with the process, so
              the system forgot everything it had learned on every restart.

The reservation problem is the other reason. `available()` then `INSERT` as
two separate statements is a race: two callers can both be promised the last
defrost timer. Here it is one IMMEDIATE transaction, so the loser is refused
rather than silently double-booked.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import dialect

DB_PATH = Path(os.getenv("PRAEVISUM_DB", Path(__file__).resolve().parents[1] / "praevisum.db"))

HERE = Path(__file__).resolve().parent

# Order matters. Reference data first, because `assets` has a foreign key into
# `equipment`; then the operational core; then the tables layered on top of it;
# then tenancy, which adds dealer_id to everything already defined.
SCHEMA_FILES = [
    "schema_reference.sql",
    "schema.sql",
    "schema_ops.sql",
    "schema_tenant.sql",
    # Last, because its views read dealer_id and the retired_on column that
    # the files above introduce.
    "schema_sales.sql",
    # Additive only. See the file header for why a counter booking cannot be
    # an appointment row without rebuilding tables that already work.
    "schema_branches.sql",
    "schema_outreach.sql",
    "schema_returns.sql",
    "schema_consent.sql",
    "schema_reviews.sql",
    "schema_remote.sql",
    "schema_channels.sql",
    "schema_review.sql",
    "schema_followup.sql",
]

SCHEMA_PATH = HERE / "schema.sql"

# Which database. SQLite unless explicitly told otherwise, because it is what
# the 139 tests run against, what works with no credentials, and what is
# actually deployed. Postgres is real and exercised, not aspirational, but it
# is opt-in so that nothing changes shape without somebody asking.
def backend() -> str:
    return os.getenv("PRAEVISUM_DB_BACKEND", "sqlite").strip().lower()


class Row(dict):
    """A row that answers to both a column name and a position.

    sqlite3.Row does both and the codebase relies on both: `r["sku"]` in most
    places, `r[0]` in a handful. pg8000 returns plain tuples, so without this
    the port would mean touching every read rather than the boundary.
    """

    __slots__ = ("_order",)

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._order = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._order[key]
        return super().__getitem__(key)

    def keys(self):
        return list(super().keys())


class _PgCursor:
    """A cursor that speaks SQLite's dialect and sqlite3's shape."""

    def __init__(self, cur):
        self._cur = cur

    def _rows(self):
        if self._cur.description is None:
            return []
        cols = [d[0] for d in self._cur.description]
        return [Row(cols, v) for v in self._cur.fetchall()]

    def fetchone(self):
        rows = self._rows()
        return rows[0] if rows else None

    def fetchall(self):
        return self._rows()

    def __iter__(self):
        return iter(self._rows())

    @property
    def rowcount(self):
        return self._cur.rowcount


class _PgConnection:
    """Enough of sqlite3.Connection for this codebase, over Postgres."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        cur = self._raw.cursor()
        # An empty tuple, never None. pg8000 calls len() on whatever it is
        # given, so None fails inside the driver rather than at the call site.
        cur.execute(dialect.to_postgres(sql), tuple(params or ()))
        return _PgCursor(cur)

    def executemany(self, sql, seq):
        cur = self._raw.cursor()
        translated = dialect.to_postgres(sql)
        for params in seq:
            cur.execute(translated, tuple(params))
        return _PgCursor(cur)

    def executescript(self, sql):
        """Apply a schema file, one committed statement at a time.

        Committing per statement is the whole point. Postgres aborts the entire
        transaction on any error, and rolling back to recover from an expected
        "already exists" discards every statement that succeeded before it.
        Batching them silently created 36 tables and then threw away four views
        when a later duplicate index rolled the transaction back. Nothing
        raised; the views were simply absent.
        """
        for stmt in dialect.split_script(dialect.to_postgres(sql)):
            cur = self._raw.cursor()
            try:
                cur.execute(stmt)
                self._raw.commit()
            except Exception as e:
                self._raw.rollback()
                if not any(w in str(e).lower() for w in dialect.ALREADY_EXISTS):
                    raise
        return self

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False


_connector = None


def _pg_connect():
    """Open a Cloud SQL connection through the Google connector.

    The connector handles IAM auth and TLS, so no password sits in the
    environment and no IP has to be allow-listed.
    """
    global _connector
    from google.cloud.sql.connector import Connector

    if _connector is None:
        _connector = Connector()

    import pg8000.dbapi
    pg8000.dbapi.paramstyle = "qmark"      # so 327 `?` placeholders still work

    return _connector.connect(
        os.environ["PRAEVISUM_PG_INSTANCE"],       # project:region:instance
        "pg8000",
        user=os.getenv("PRAEVISUM_PG_USER", "postgres"),
        password=os.getenv("PRAEVISUM_PG_PASSWORD", ""),
        db=os.getenv("PRAEVISUM_PG_DB", "praevisum"),
    )


def connect():
    """A connection to whichever backend is configured."""
    if backend() == "postgres":
        return _PgConnection(_pg_connect())

    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    """Build the database from the schema files. Safe to run on a live one.

    Every CREATE is IF NOT EXISTS, so this is idempotent apart from the ALTERs
    in schema_tenant.sql: SQLite has no ADD COLUMN IF NOT EXISTS, and a second
    run raises "duplicate column name". That one error means the column is
    already there, which is the desired state, so it is the only failure
    swallowed here. Anything else is a real schema problem and should stop the
    run rather than leave a half-built database behind.
    """
    with connect() as c:
        for name in SCHEMA_FILES:
            sql = (HERE / name).read_text(encoding="utf-8")
            try:
                c.executescript(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
                # Re-run statement by statement, skipping only the ALTERs that
                # have already been applied.
                for stmt in sql.split(";"):
                    if not stmt.strip():
                        continue
                    try:
                        c.execute(stmt)
                    except sqlite3.OperationalError as inner:
                        if "duplicate column name" not in str(inner):
                            raise


@contextmanager
def txn() -> Iterator[sqlite3.Connection]:
    """A write transaction that actually locks.

    BEGIN IMMEDIATE takes the write lock up front rather than on first write,
    which is what makes reserve-or-refuse correct under two concurrent calls.
    """
    conn = connect()
    if backend() == "postgres":
        # Postgres is transactional by default and takes row locks on write,
        # so the reserve-or-refuse guarantee holds without BEGIN IMMEDIATE.
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def stats() -> dict:
    with connect() as c:
        out = {}
        for t in ("equipment", "recalls", "accounts", "sites", "contacts",
                  "phones", "assets", "parts", "technicians", "calls",
                  "work_orders", "visits", "reservations", "repairs"):
            out[t] = c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
        out["brands"] = c.execute(
            "SELECT COUNT(DISTINCT brand) n FROM equipment").fetchone()["n"]
        out["db_mb"] = round(DB_PATH.stat().st_size / 1e6, 2) if DB_PATH.exists() else 0
        return out
