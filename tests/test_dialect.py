"""SQL translation from SQLite to Postgres.

Six constructs, translated at the boundary rather than rewriting 327 queries
into a common subset that would read worse everywhere to make one deployment
target possible.

Every case here is one that actually broke against a real Cloud SQL instance.
None were predicted by reading the code.
"""

from __future__ import annotations

from src.dialect import split_script, to_postgres


def test_pragmas_are_dropped():
    """No write-ahead log to checkpoint, and foreign keys are always on."""
    assert to_postgres("PRAGMA foreign_keys = ON;").strip() == ""


def test_group_concat_becomes_string_agg():
    assert "string_agg(p.name, ', ')" in to_postgres(
        "SELECT GROUP_CONCAT(p.name, ', ') parts FROM x")
    assert "string_agg(DISTINCT category, ',')" in to_postgres(
        "SELECT GROUP_CONCAT(DISTINCT category) FROM complaints")


def test_julianday_becomes_interval_arithmetic():
    out = to_postgres("WHERE JULIANDAY('now') - JULIANDAY(raised_at) <= ?")
    assert "EXTRACT(EPOCH FROM (now() - raised_at::timestamp)) / 86400.0" in out
    assert "?" in out, "the placeholder must survive translation"


def test_integer_primary_key_becomes_identity():
    """SQLite's INTEGER PRIMARY KEY auto-increments. Postgres leaves it null.

    Untranslated, the reference loader inserts 88,544 equipment rows all
    claiming id 0.
    """
    assert "GENERATED ALWAYS AS IDENTITY" in to_postgres(
        "CREATE TABLE t (id INTEGER PRIMARY KEY)")


def test_blob_becomes_bytea():
    assert "BYTEA" in to_postgres("CREATE TABLE t (embedding BLOB)")


def test_create_view_if_not_exists_becomes_or_replace():
    """Postgres takes IF NOT EXISTS on tables and indexes, but not on views.

    It reports `syntax error at or near "NOT"` and applies nothing.
    """
    out = to_postgres("CREATE VIEW IF NOT EXISTS v AS SELECT 1")
    assert out.startswith("CREATE OR REPLACE VIEW v")
    assert "IF NOT EXISTS" not in out


def test_two_argument_round_is_cast_to_numeric():
    """Postgres has no ROUND(double precision, int)."""
    out = to_postgres("ROUND(AVG(r.labor_hours), 2) AS avg_hours")
    assert "::numeric, 2)" in out


def test_single_argument_round_is_left_alone():
    """Postgres has ROUND(double precision), so casting would be noise."""
    out = to_postgres("ROUND(AVG(x) * 100) AS pct")
    assert "numeric" not in out


def test_insert_or_ignore_becomes_on_conflict():
    out = to_postgres("INSERT OR IGNORE INTO parts (sku) VALUES (?)")
    assert out.endswith("ON CONFLICT DO NOTHING")
    assert "OR IGNORE" not in out


def test_a_semicolon_inside_a_comment_does_not_split_the_script():
    """The bug that made Postgres reject `now a fact per part` as a statement.

    Comments must be stripped BEFORE splitting, not after.
    """
    script = (
        "-- Was a string-prefix guess; now a fact per part\n"
        "CREATE TABLE a (id TEXT);\n"
        "CREATE TABLE b (id TEXT);\n"
    )
    stmts = split_script(script)
    assert len(stmts) == 2
    assert all(s.upper().startswith("CREATE TABLE") for s in stmts)


def test_a_semicolon_inside_a_string_literal_survives():
    stmts = split_script("INSERT INTO t VALUES ('a;b');")
    assert len(stmts) == 1
    assert "'a;b'" in stmts[0]


def test_placeholders_are_never_rewritten():
    """pg8000 is put into qmark mode, so all 327 `?` placeholders stand."""
    out = to_postgres("SELECT * FROM t WHERE a=? AND b=? AND c=?")
    assert out.count("?") == 3
