-- Extended cover somebody actually bought.
--
-- `warranty_options` could price extra years and there was nowhere to record
-- that anyone took them. The desk could quote cover, the customer could say
-- yes, and the next call would compute coverage from the manufacturer term
-- alone and tell them they were out of warranty.
--
-- Kept as its own table rather than a column on `assets`, for the reason
-- standing.py already argues about install dates: WHERE A TERM CAME FROM
-- changes what it is worth. A manufacturer term is published and checkable; an
-- extension is something we sold and owe. Collapsing both into one
-- `warranty_until` loses which of the two is being relied on.
CREATE TABLE IF NOT EXISTS cover_sold (
    id           TEXT PRIMARY KEY,
    -- NULL until the machine exists. Cover is sold at the till and a
    -- machine only becomes an asset when the order is delivered, so the two
    -- cannot be the same row at the same moment. ownership.becomes_theirs
    -- fills this in when the order lands.
    asset_id     TEXT REFERENCES assets(id),
    account_id   TEXT REFERENCES accounts(id),
    po_id        TEXT REFERENCES purchase_orders(id),

    extra_years  REAL NOT NULL,
    price        REAL,
    starts_on    TEXT NOT NULL,      -- normally the install date
    ends_on      TEXT NOT NULL,      -- computed once, so it cannot drift

    -- What it actually covers. Extended cover is usually parts-only, and
    -- selling "5 years cover" that turns out to exclude labour is how a
    -- warranty becomes an argument.
    covers_parts   INTEGER NOT NULL DEFAULT 1,
    covers_labour  INTEGER NOT NULL DEFAULT 0,

    sold_on      TEXT NOT NULL,
    sold_by      TEXT,               -- who agreed it, in their words
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_cover_sold_asset ON cover_sold(asset_id);
