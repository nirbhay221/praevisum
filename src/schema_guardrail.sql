-- What the guards actually did, kept rather than printed.
--
-- WHY THIS TABLE HAD TO EXIST
--
-- guards.py is the load-bearing part of this system. It fills in identifiers
-- so nobody is ever asked for an Asset ID, it fills in the routed vendor that
-- twelve tools quietly got wrong, it refuses a tool that would reach into
-- another customer's machine, it stops the desk escalating over a fact a tool
-- had just disproved, and it catches the desk reading the same answer out
-- twice.
--
-- Every one of those was printed to a log and discarded. The file contained no
-- INSERT of any kind. So the central claim about this product, that it refuses
-- rather than invents, was true in the code and unevidenced everywhere else:
-- there was no way to answer "how often does that happen", "is it getting
-- better", or "which of these guards has ever fired in production".
--
-- A guard nobody can count is indistinguishable from a guard that does not
-- work, and the ones that matter most here fire on the rarest calls.
--
-- WHAT IS DELIBERATELY NOT STORED
--
-- No argument values. A tool call carries customer names, addresses and phone
-- numbers, and this table is for counting interventions, not for building a
-- second copy of the call record. `detail` is a written reason, and `args_seen`
-- is the argument NAMES only, so a change in shape is visible without the
-- contents coming along.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS interventions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    call_id     TEXT,
    dealer_id   TEXT,
    tool        TEXT NOT NULL,

    -- Which guard, in the vocabulary the code uses:
    --   filled_id        an identifier supplied so nobody was asked for one
    --   filled_dealer    the routed vendor supplied to a tool that ignored it
    --   resolved_asset   a blanked machine matched to one they actually own
    --   not_theirs       refused: that machine belongs to another customer
    --   disproved        refused: contradicts what a tool just answered
    --   repeat           the same tool, same arguments, same call
    --   no_intent        a write before the call was classified
    --   wrong_intent     a write belonging to a different kind of call
    kind        TEXT NOT NULL,

    -- Whether the call was stopped or quietly corrected. The difference
    -- matters: a substitution means the customer never noticed, a block means
    -- the model was told no and had to do something else.
    outcome     TEXT NOT NULL,          -- blocked | corrected

    detail      TEXT,
    args_seen   TEXT                    -- argument NAMES only, never values
);

CREATE INDEX IF NOT EXISTS ix_intervention_when
    ON interventions(at DESC);
CREATE INDEX IF NOT EXISTS ix_intervention_kind
    ON interventions(dealer_id, kind);
CREATE INDEX IF NOT EXISTS ix_intervention_call
    ON interventions(call_id);
