-- What became of a call, derived rather than declared.
--
-- Additive. `calls` already has `intent` and `outcome` columns and both were
-- always NULL: set_intent wrote only to session state, and nothing ever wrote
-- an outcome at all. So the two fields designed for exactly this question have
-- never held anything.
--
-- WHY A SEPARATE TABLE RATHER THAN JUST FILLING THOSE IN
--
-- Because an outcome is not one field. A service call that ends with no work
-- order is a failure if the desk lost the thread, and the best possible result
-- if a documented remote fix worked and no van had to move. Those need
-- different rows, not different strings.
--
-- WHY DERIVED
--
-- Nothing here is a model's opinion of how the call went. Every column is read
-- back out of the tables the call actually wrote: a work order exists or it
-- does not, a slot was promised or it was not, a remote attempt resolved or it
-- did not. An agent grading its own conversation is not measurement.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS call_outcomes (
    call_id      TEXT PRIMARY KEY REFERENCES calls(id),
    dealer_id    TEXT,

    intent       TEXT,        -- service, order, product, supplier, or null
    outcome      TEXT NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0,

    -- A service call that ended with no van because a documented fix worked.
    -- Counted apart from everything else because every off-the-shelf metric
    -- would score it as a failed call, and it is the opposite.
    avoided_visit INTEGER NOT NULL DEFAULT 0,

    -- The desk tried and broke mid-flow, as against a call that was always
    -- going to need a person. The industry keeps these apart and so do we:
    -- forced escalation is the number that says the product is not working.
    escalation   TEXT,         -- 'forced', 'planned', or null

    -- Structural evidence, not sentiment. Facts a person can check against
    -- the transcript rather than a judgment about how somebody sounded.
    caller_repeats INTEGER NOT NULL DEFAULT 0,
    agent_repeats  INTEGER NOT NULL DEFAULT 0,
    turns          INTEGER NOT NULL DEFAULT 0,
    seconds        REAL,

    note         TEXT,
    settled_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_call_outcomes_dealer ON call_outcomes(dealer_id);
CREATE INDEX IF NOT EXISTS ix_call_outcomes_intent ON call_outcomes(intent);
