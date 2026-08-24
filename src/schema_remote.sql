-- Fixes that do not need a van.
--
-- Additive. Every service call in this system ends in a visit. There is no
-- path where the desk resolves something and hangs up, which means the
-- industry base rate of 14% avoidable dispatches is waste this product cannot
-- currently even detect, at $200 to $300 a time.
--
-- Two tables, because a documented procedure and a record of trying one are
-- different things and conflating them would let a failed attempt look like
-- evidence that the fix works.

PRAGMA foreign_keys = ON;

-- Things a customer can do themselves, grounded in a source we can name.
--
-- `source` is not decoration. An unattended agent telling somebody to open a
-- panel must be able to say where the instruction came from, and a procedure
-- with no provenance does not belong in here at all.
CREATE TABLE IF NOT EXISTS remote_fixes (
    id            TEXT PRIMARY KEY,
    dealer_id     TEXT REFERENCES dealers(id),

    -- Which machines it applies to. Family is the coarse net; the component
    -- profile from the certification data is what actually decides whether a
    -- procedure written for one machine is safe on another.
    family        TEXT,
    product_type  TEXT,
    defrost_type  TEXT,
    manufacturer  TEXT,                -- null means any make of this design

    symptom       TEXT NOT NULL,       -- what the caller describes
    check_first   TEXT,                -- the question that confirms it applies
    instruction   TEXT NOT NULL,       -- what to tell them, in plain words

    source        TEXT NOT NULL,       -- manual, recall remedy, or our own notes
    source_ref    TEXT,                -- page, recall number, repair id

    -- Nobody should be told to open a live panel over the phone.
    requires_tools    INTEGER NOT NULL DEFAULT 0,
    safety_note       TEXT,

    -- Filled in from outcomes. A procedure that keeps failing must stop being
    -- offered, and that only happens if we count.
    attempts      INTEGER NOT NULL DEFAULT 0,
    resolved      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_remote_family ON remote_fixes(family);
CREATE INDEX IF NOT EXISTS ix_remote_profile ON remote_fixes(product_type, defrost_type);

-- What happened when we tried one. Kept separate from the procedure so a run
-- of failures is visible as history rather than silently averaged away.
CREATE TABLE IF NOT EXISTS remote_attempts (
    id            TEXT PRIMARY KEY,
    dealer_id     TEXT REFERENCES dealers(id),
    fix_id        TEXT REFERENCES remote_fixes(id),
    asset_id      TEXT REFERENCES assets(id),
    from_call     TEXT REFERENCES calls(id),
    work_order_id TEXT REFERENCES work_orders(id),

    symptom       TEXT,
    outcome       TEXT CHECK (outcome IN
                    ('resolved','not_resolved','refused','unsafe',NULL)),
    said          TEXT,
    attempted_at  TEXT NOT NULL,

    -- The number this whole feature exists to produce.
    saved_a_visit INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_attempts_fix ON remote_attempts(fix_id, outcome);

-- How often a documented fix actually works, with the denominator.
DROP VIEW IF EXISTS remote_fix_record;
CREATE VIEW remote_fix_record AS
SELECT f.id, f.symptom, f.instruction, f.source,
       COUNT(a.id)                                              AS tried,
       SUM(CASE WHEN a.outcome='resolved' THEN 1 ELSE 0 END)     AS worked,
       SUM(a.saved_a_visit)                                      AS visits_saved
FROM remote_fixes f
LEFT JOIN remote_attempts a ON a.fix_id = f.id
GROUP BY f.id;
