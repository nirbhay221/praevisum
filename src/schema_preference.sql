-- Who a customer wants sent, and who they would rather not see again.
--
-- `find_technician` picks on skills and drive time, which is the right
-- default and misses the commonest request a service desk receives: send the
-- same person as last time, or please not him again.
--
-- The two are not the same strength and the table does not pretend they are.
-- An exclusion REMOVES somebody from consideration; a preference REORDERS
-- what is left. Holding a job for three days waiting for one engineer while a
-- freezer is warm serves nobody, and a preference that silently outranked
-- availability would have the desk making promises it cannot keep.
--
-- Neither can override EPA 608. cover.py decides who may legally open a
-- machine, and this only ever sees candidates that already passed it.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS crew_preference (
    account_id    TEXT NOT NULL REFERENCES accounts(id),
    technician_id TEXT NOT NULL REFERENCES technicians(id),

    kind          TEXT NOT NULL CHECK (kind IN ('prefer','exclude')),

    -- Their words, not a category. "He was very good with our chef" and "he
    -- left the door open" are different facts and a dropdown would lose both.
    because       TEXT,

    from_call     TEXT,
    noted_on      TEXT NOT NULL,

    PRIMARY KEY (account_id, technician_id)
);

CREATE INDEX IF NOT EXISTS ix_crew_pref_account
    ON crew_preference(account_id, kind);
