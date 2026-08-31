-- Closing the loops that were left open after a sale.
--
-- Three things happened and none of them were recorded anywhere:
--
--   the thing arrived        nothing marked an order delivered, so orders
--                            stayed confirmed forever and cover was dated
--                            from the PROMISE rather than the delivery
--   the job was done well    the after-visit call asked whether the fix held
--                            and the answer never became a judgement about
--                            who did the work
--   somebody disagreed       no way to record two accounts of one visit, and
--                            nothing that reassigns or makes good

CREATE TABLE IF NOT EXISTS deliveries (
    id             TEXT PRIMARY KEY,
    po_id          TEXT NOT NULL REFERENCES purchase_orders(id),
    carrier        TEXT,
    carrier_ref    TEXT,                    -- their tracking number
    delivered_on   TEXT NOT NULL,           -- the REAL date, from the carrier
    notified_at    TEXT,                    -- when the carrier told us
    checked_in_at  TEXT,                    -- when we rang the customer
    confirmed_by   TEXT,                    -- who said yes, it is here
    condition      TEXT,                    -- ok | damaged | wrong | missing
    note           TEXT
);
CREATE INDEX IF NOT EXISTS ix_deliveries_po ON deliveries(po_id);

-- What the customer said about the work, attributed to whoever did it.
-- Deliberately not a star rating: the useful question is whether it held.
CREATE TABLE IF NOT EXISTS workmanship (
    id             TEXT PRIMARY KEY,
    work_order_id  TEXT NOT NULL REFERENCES work_orders(id),
    visit_id       TEXT REFERENCES visits(id),
    technician_id  TEXT REFERENCES technicians(id),
    asked_at       TEXT NOT NULL,
    still_working  INTEGER,                 -- 1 yes, 0 no, null not reached
    on_time        INTEGER,
    customer_said  TEXT,
    dealer_id      TEXT
);
CREATE INDEX IF NOT EXISTS ix_workmanship_tech ON workmanship(technician_id);

-- Two accounts of one visit, and what was done about it.
CREATE TABLE IF NOT EXISTS disputes (
    id              TEXT PRIMARY KEY,
    work_order_id   TEXT NOT NULL REFERENCES work_orders(id),
    visit_id        TEXT REFERENCES visits(id),
    raised_at       TEXT NOT NULL,
    customer_says   TEXT,
    technician_says TEXT,
    -- outcome: the machine is still broken. process: how it was done.
    -- The research is consistent that these are not the same failure and
    -- must not draw the same make-good.
    kind            TEXT,
    severity        TEXT,
    reassigned_to   TEXT REFERENCES technicians(id),
    revisit_id      TEXT REFERENCES visits(id),
    made_good       TEXT,                   -- what we actually gave them
    made_good_value REAL,
    settled_at      TEXT,
    dealer_id       TEXT
);
CREATE INDEX IF NOT EXISTS ix_disputes_wo ON disputes(work_order_id);
