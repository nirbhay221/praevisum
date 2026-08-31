-- A job we cannot staff, handed to somebody who can do something about it.
--
-- Additive.
--
-- WHAT THIS REPLACES
--
-- One hardcoded line in scheduling.py:
--
--     "advice": "Say so plainly and offer to have a supervisor call back."
--
-- On a live call a restaurant with a freezer sitting at fifteen degrees was
-- told exactly that. No supervisor was named, no callback was recorded
-- anywhere, nothing was queued, and nobody was going to ring. It was a shrug
-- with a job title on it, and the customer had already been quoted and had a
-- work order opened for a visit that could never have been staffed.
--
-- An escalation that is not written down is not an escalation.

PRAGMA foreign_keys = ON;

-- Who actually picks these up. A name, because "a supervisor will call you"
-- is not something a customer can hold us to and "Dale Brenner will ring you
-- before six" is.
ALTER TABLE dealers ADD COLUMN manager_name TEXT;
ALTER TABLE dealers ADD COLUMN manager_phone TEXT;

CREATE TABLE IF NOT EXISTS escalations (
    id         TEXT PRIMARY KEY,
    dealer_id  TEXT REFERENCES dealers(id),
    call_id    TEXT,
    account_id TEXT REFERENCES accounts(id),
    asset_id   TEXT REFERENCES assets(id),
    work_order_id TEXT REFERENCES work_orders(id),

    reason     TEXT NOT NULL,      -- no_qualified_technician, no_slot, other
    detail     TEXT,               -- what certification, which family

    -- What we told the customer would happen, in the words they heard, so the
    -- person picking it up knows what has already been promised on their
    -- behalf.
    promised   TEXT,
    promised_by TEXT,              -- when somebody said they would ring

    state      TEXT NOT NULL DEFAULT 'open'
               CHECK (state IN ('open','picked_up','resolved','abandoned')),
    taken_by   TEXT,
    taken_at   TEXT,
    outcome    TEXT,

    opened_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_escalations_state ON escalations(dealer_id, state);
