-- Things coming back.
--
-- Additive. Nothing above this is altered.
--
-- A return is two different facts wearing one word, and conflating them is the
-- mistake this schema exists to avoid.
--
--   A PART coming back is an inventory event. It was ordered, it was not
--   needed or it was wrong, and if it is unopened it goes on the shelf. The
--   restock advice must know, or it reorders something already sitting in a
--   box by the door.
--
--   A MACHINE coming back is evidence. Somebody bought it, lived with it, and
--   gave it back. That is a stronger signal about a model than any complaint
--   and stronger than most service calls, because a complaint is annoyance and
--   a return is a decision.
--
-- So `kind` is not decoration. The two are counted separately everywhere.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS returns (
    id            TEXT PRIMARY KEY,
    dealer_id     TEXT REFERENCES dealers(id),
    account_id    TEXT REFERENCES accounts(id),
    from_call     TEXT REFERENCES calls(id),

    kind          TEXT NOT NULL CHECK (kind IN ('part','machine')),

    -- One of these, depending on kind. A part return names a SKU; a machine
    -- return names the asset and, denormalised, its make and model so the
    -- evidence outlives the asset row being deleted or retired.
    sku           TEXT REFERENCES parts(sku),
    asset_id      TEXT REFERENCES assets(id),
    manufacturer  TEXT,
    model_number  TEXT,
    qty           INTEGER NOT NULL DEFAULT 1,

    -- Why it came back. The distinction that matters for the product signal:
    -- "faulty" and "not_as_described" are the model's fault, "changed_mind"
    -- and "ordered_wrong" are ours or theirs. Counting them together would
    -- make a model look bad because a customer miscounted.
    reason        TEXT NOT NULL CHECK (reason IN (
                    'faulty','not_as_described','damaged_in_transit',
                    'ordered_wrong','changed_mind','duplicate','other')),
    said          TEXT,                    -- their words

    -- Can it be sold again. An unopened part goes back on the shelf; a fitted
    -- one does not, and restocking must not count it as available.
    condition     TEXT NOT NULL DEFAULT 'unopened'
                  CHECK (condition IN ('unopened','opened','used','damaged')),
    restocked     INTEGER NOT NULL DEFAULT 0,

    resolution    TEXT CHECK (resolution IN
                    ('refund','exchange','credit','repair','refused',NULL)),
    amount        REAL,

    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    status        TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','approved','received','closed','refused'))
);

CREATE INDEX IF NOT EXISTS ix_returns_dealer ON returns(dealer_id, status);
CREATE INDEX IF NOT EXISTS ix_returns_sku    ON returns(sku);
CREATE INDEX IF NOT EXISTS ix_returns_model  ON returns(manufacturer, model_number);

-- Parts that came back and went back on the shelf, so the reorder advice does
-- not buy what is already sitting by the door.
DROP VIEW IF EXISTS parts_returned;
CREATE VIEW parts_returned AS
SELECT sku,
       SUM(qty)                                              AS returned,
       SUM(CASE WHEN restocked = 1 THEN qty ELSE 0 END)      AS back_on_shelf,
       SUM(CASE WHEN reason IN ('faulty','not_as_described')
                THEN qty ELSE 0 END)                         AS faulty
FROM returns
WHERE kind = 'part' AND status <> 'refused'
GROUP BY sku;

-- Machines given back, per model. The denominator lives in model_supplied, so
-- three returns out of forty reads differently from three out of four.
DROP VIEW IF EXISTS model_returns;
CREATE VIEW model_returns AS
SELECT manufacturer,
       model_number,
       COUNT(*)                                              AS returns,
       SUM(CASE WHEN reason IN ('faulty','not_as_described')
                THEN 1 ELSE 0 END)                           AS blamed_on_machine,
       GROUP_CONCAT(DISTINCT reason)                         AS reasons
FROM returns
WHERE kind = 'machine' AND status <> 'refused'
GROUP BY manufacturer, model_number;
