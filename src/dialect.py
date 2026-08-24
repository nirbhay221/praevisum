"""Translating this project's SQL from SQLite to Postgres.

Small on purpose, because the port turned out to be small. Measured before
writing anything:

    327 SQL statements       ? placeholders work unchanged, pg8000 speaks qmark
     12 PRAGMA               no equivalent needed, dropped
      4 GROUP_CONCAT         -> string_agg
      1 JULIANDAY            -> interval arithmetic
      2 INTEGER PRIMARY KEY  -> GENERATED ALWAYS AS IDENTITY
      1 BLOB                 -> BYTEA
      0 INSERT OR REPLACE    in src/, only in scripts and tests
     13 strftime             all Python, none SQL

The alternative was rewriting 327 statements to a common subset, which would
have made every query worse to read in order to make one deployment target
possible. Translating six constructs at the boundary is a better trade, and it
keeps SQLite exactly as it is for tests and offline work.

This is deliberately a string rewriter and not a SQL parser. A parser would be
correct in general and this only has to be correct for the SQL in this
repository, which is checked in and testable. The moment somebody writes SQL
this cannot handle, a test fails rather than production behaving oddly.
"""

from __future__ import annotations

import re

# PRAGMA is SQLite housekeeping with no Postgres equivalent that matters here:
# foreign keys are always enforced, and there is no write-ahead log to
# checkpoint. Dropping them is correct rather than a shortcut.
_PRAGMA = re.compile(r"^\s*PRAGMA\b[^;]*;?\s*$", re.IGNORECASE | re.MULTILINE)

# GROUP_CONCAT(x) and GROUP_CONCAT(x, sep) and GROUP_CONCAT(DISTINCT x)
_GC_SEP = re.compile(r"GROUP_CONCAT\s*\(\s*([^,()]+?)\s*,\s*('[^']*')\s*\)",
                     re.IGNORECASE)
_GC_PLAIN = re.compile(r"GROUP_CONCAT\s*\(\s*(DISTINCT\s+)?([^,()]+?)\s*\)",
                       re.IGNORECASE)

# JULIANDAY('now') - JULIANDAY(col) gives whole days as a float in SQLite.
_JULIAN = re.compile(
    r"JULIANDAY\s*\(\s*'now'\s*\)\s*-\s*JULIANDAY\s*\(\s*([A-Za-z_][\w.]*)\s*\)",
    re.IGNORECASE)

# Postgres takes IF NOT EXISTS on tables and indexes but NOT on views, where
# the equivalent is CREATE OR REPLACE. Nothing warns you: it simply reports a
# syntax error at the word "NOT" and applies nothing.
_VIEW_IF_NOT_EXISTS = re.compile(
    r"CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS", re.IGNORECASE)

# SQLite rounds anything to any number of places. Postgres only has two-argument
# ROUND for `numeric`, so ROUND(AVG(x), 2) over a float column fails with
# "function round(double precision, integer) does not exist". Casting the value
# is the documented fix.
_ROUND_2ARG = re.compile(
    r"ROUND\s*\(\s*(AVG|SUM|MIN|MAX|CAST)\s*\((?P<inner>[^()]*)\)\s*,\s*(?P<places>\d+)\s*\)",
    re.IGNORECASE)


def _round_numeric(m: re.Match) -> str:
    fn = m.group(0).split("(", 1)[0]
    agg = m.group(1)
    return f"ROUND(({agg}({m.group('inner')}))::numeric, {m.group('places')})"


_SQLITE_ONLY_TYPES = (
    # SQLite's INTEGER PRIMARY KEY is an implicit auto-incrementing rowid
    # alias. Postgres treats it as a plain integer that stays null, so the
    # reference loaders would insert 88,544 rows all claiming id 0.
    (re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\b", re.IGNORECASE),
     "INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"),
    (re.compile(r"\bBLOB\b", re.IGNORECASE), "BYTEA"),
    (re.compile(r"\bCAST\s*\(([^()]+?)\s+AS\s+REAL\s*\)", re.IGNORECASE),
     r"CAST(\1 AS DOUBLE PRECISION)"),
)


def to_postgres(sql: str) -> str:
    """Rewrite one statement, or a whole schema script, for Postgres."""
    sql = _PRAGMA.sub("", sql)

    sql = _GC_SEP.sub(r"string_agg(\1, \2)", sql)
    sql = _GC_PLAIN.sub(lambda m: f"string_agg({m.group(1) or ''}{m.group(2)}, ',')",
                        sql)

    # SQLite compares dates as floating point days; Postgres does interval
    # arithmetic. Both sides end up as "days elapsed" so callers are unchanged.
    sql = _JULIAN.sub(
        r"EXTRACT(EPOCH FROM (now() - \1::timestamp)) / 86400.0", sql)

    sql = _VIEW_IF_NOT_EXISTS.sub("CREATE OR REPLACE VIEW", sql)
    sql = _ROUND_2ARG.sub(_round_numeric, sql)

    for pattern, replacement in _SQLITE_ONLY_TYPES:
        sql = pattern.sub(replacement, sql)

    # SQLite tolerates INSERT OR IGNORE; Postgres wants the conflict clause at
    # the end. Only appears in scripts and tests, never on the call path, but
    # translating it keeps those runnable against either backend.
    if re.search(r"\bINSERT\s+OR\s+IGNORE\b", sql, re.IGNORECASE):
        sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql,
                     flags=re.IGNORECASE)
        if "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    return sql


def split_script(sql: str) -> list[str]:
    """Split a schema file into statements.

    Comments are stripped BEFORE splitting, and the order matters. Splitting
    first put a semicolon from inside this comment in schema.sql:

        -- Which parts fit which machines. Was a string-prefix guess; now a
        -- fact per part

    in the middle of the script, so "now a fact per part" arrived at Postgres
    as a statement and it said, reasonably, `syntax error at or near "now"`.

    Still not a SQL parser. It handles line comments and quoted strings, which
    is what this schema contains, and anything stranger fails at deploy time
    rather than applying half a schema.
    """
    statements, current, in_string = [], [], False

    for line in sql.splitlines():
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                in_string = not in_string
                current.append(ch)
            elif not in_string and line[i:i + 2] == "--":
                break                      # rest of the line is a comment
            elif ch == ";" and not in_string:
                # A statement ends only at a semicolon that is not inside a
                # string literal. Stripping comments and then splitting on
                # every semicolon got the comment case right and the literal
                # case wrong, so both are decided in one pass.
                statements.append("".join(current))
                current = []
            else:
                current.append(ch)
            i += 1
        current.append("\n")

    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


ALREADY_EXISTS = ("already exists", "duplicate column")
