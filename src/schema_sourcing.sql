-- What a supplier can actually be asked, and what they answered.
--
-- Sourcing a part worked like this:
--
--     supplier = c.execute("SELECT id FROM suppliers LIMIT 1").fetchone()
--     LEAD_DAYS = {"part": 3, "specialised": 15, "machine": 21}
--
-- The supplier was whichever row came first and the date was a constant
-- chosen by matching words in a description. Four suppliers existed, three
-- had phone numbers, and none of them was ever asked anything. A customer
-- was told "about 21 days" by a table.
--
-- These two tables are what makes the question askable: what each supplier
-- carries, and what they said when we asked.

-- What a supplier will quote on. Their book, not ours: their price is what
-- THEY charge us, which is not the same as parts.unit_cost, and their lead
-- time is theirs to promise rather than ours to assume.
CREATE TABLE IF NOT EXISTS supplier_catalogue (
    supplier_id    TEXT NOT NULL REFERENCES suppliers(id),
    sku            TEXT NOT NULL,             -- our sku, so we can compare
    their_ref      TEXT,                      -- their part number
    unit_price     REAL,                      -- what they charge us
    lead_time_days INTEGER,                   -- what they promise
    on_hand        INTEGER DEFAULT 0,         -- their shelf, not ours
    min_order_qty  INTEGER DEFAULT 1,
    updated_at     TEXT,
    PRIMARY KEY (supplier_id, sku)
);
CREATE INDEX IF NOT EXISTS ix_supcat_sku ON supplier_catalogue(sku);

-- Every time we asked, and what came back. Kept because a promise made by
-- another company's agent, that our customer is then invoiced against, is
-- exactly the thing that has to be on the record: who said what, when, and
-- whether they turned out to be right.
CREATE TABLE IF NOT EXISTS sourcing_requests (
    id             TEXT PRIMARY KEY,
    sku            TEXT,
    description    TEXT,
    for_call       TEXT,                      -- the conversation it came from
    dealer_id      TEXT,
    asked_at       TEXT NOT NULL,
    chosen         TEXT REFERENCES suppliers(id),
    chosen_because TEXT
);

CREATE TABLE IF NOT EXISTS sourcing_replies (
    request_id     TEXT NOT NULL REFERENCES sourcing_requests(id),
    supplier_id    TEXT NOT NULL REFERENCES suppliers(id),
    answered_at    TEXT,
    available      INTEGER,                   -- 1 yes, 0 no, null no answer
    unit_price     REAL,
    lead_time_days INTEGER,
    arrives_on     TEXT,
    note           TEXT,
    -- How the answer reached us. Today both halves are ours, so this says
    -- so plainly rather than implying a partnership that does not exist.
    via            TEXT,                      -- a2a | table | phone
    PRIMARY KEY (request_id, supplier_id)
);
