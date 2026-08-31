-- Every event that cost us money, in one place, attributed to a product.
--
-- WHY A LEDGER AND NOT FOUR QUERIES
--
-- The losses were all recorded and none of them were together. A service
-- visit's cost sits in visit_cost. A machine sent back sits in returns. A
-- warranty claim sits in warranty_claims. A complaint carries the customer's
-- words and no number at all.
--
-- So the question a dealer actually asks -- "is this model making us money or
-- costing us money" -- could not be answered, because answering it meant
-- joining four tables that share no key beyond a make and a model, and
-- nothing did. `restock_advice` reorders spare parts beautifully and has
-- never once been able to say "stop buying this freezer".
--
-- ONE ROW PER EVENT, POSTED ONCE
--
-- source_table and source_id carry where the money went, and the unique index
-- on them is what makes posting idempotent. A ledger that can double-count is
-- worse than no ledger: it produces a number that looks authoritative and is
-- wrong, and nobody can tell by looking.
--
-- DENORMALISED MAKE AND MODEL, deliberately, for the same reason complaints
-- carry them: the evidence has to outlive the individual machine being
-- retired, sold or replaced. A loss attributed only through an asset id
-- disappears exactly when the customer replaces the machine that caused it.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS losses (
    id           TEXT PRIMARY KEY,
    dealer_id    TEXT,
    happened_on  TEXT NOT NULL,

    -- What kind of loss. Not a free-text field: these are the four things
    -- that actually cost this business money after a sale, and a fifth would
    -- be a deliberate decision rather than a typo.
    kind         TEXT NOT NULL CHECK (kind IN
                 ('service_visit','return','warranty_claim','write_off')),

    manufacturer TEXT,
    model_number TEXT,
    family       TEXT,

    -- At cost, always. What we would have charged is a different number
    -- answered somewhere else, and mixing the two produces a figure that is
    -- neither.
    amount       REAL NOT NULL,

    -- Where it came from, and the reason this table cannot double-count.
    source_table TEXT NOT NULL,
    source_id    TEXT NOT NULL,

    account_id   TEXT,
    complaint_id TEXT,
    note         TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_losses_once
    ON losses(source_table, source_id);
CREATE INDEX IF NOT EXISTS ix_losses_model
    ON losses(dealer_id, manufacturer, model_number);
CREATE INDEX IF NOT EXISTS ix_losses_when
    ON losses(dealer_id, happened_on);
