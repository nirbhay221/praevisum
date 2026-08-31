-- Praevisum is not a refrigeration application. It is the phone desk for a
-- field service business, and there are many of those.
--
-- A refrigeration dealer does not fix laptops. An IT warranty provider does
-- not touch a walk-in cooler. They are separate companies with separate
-- technicians, separate stock and separate customers, and they would never
-- share any of it.
--
-- What they DO share is the equipment catalogue, because that is public
-- federal certification data about machines that exist in the world. Nobody
-- owns it and everybody benefits from it.
--
-- What they never share is the repair corpus. Which faults recur, what
-- actually fixed them, and which parts a technician should load are the
-- accumulated experience of one company's own vans. That is the single most
-- valuable thing a dealer has and the reason this product is worth anything,
-- so it is scoped per dealer and enforced in the schema rather than in a
-- WHERE clause somebody might forget.
--
-- An inbound call knows which dealer it belongs to from the number that was
-- dialled. Each dealer has their own.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dealers (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    trade         TEXT NOT NULL,        -- refrigeration, it, hvac
    phone_e164    TEXT UNIQUE,          -- the line customers ring
    greeting_name TEXT,                 -- what the agent says out loud
    families      TEXT,                 -- comma separated: what they service
    timezone      TEXT DEFAULT 'America/Chicago',
    -- WHAT THIS TRADE KNOWS THAT THE OTHERS DO NOT.
    --
    -- Every vendor received a byte-identical instruction: 25,544 characters,
    -- the same for all four. So a customer buying an office chair was
    -- governed by rules mentioning refrigerant four times, EPA certification
    -- five times, R-290, NSF and compressors -- and the word "chair" once.
    --
    -- The tenancy was in the data and in the routing and absent from the only
    -- part that decides how the desk actually talks. This column is where a
    -- trade's own knowledge lives, appended per call, so adding a fifth
    -- business is a row rather than an agent.
    trade_notes   TEXT
);

-- Every operational table belongs to exactly one dealer. Added as a column
-- rather than a separate database per tenant because the whole point is that
-- one running service answers several companies' phones.
ALTER TABLE accounts        ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE technicians     ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE parts           ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE stock_locations ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE work_orders     ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE repairs         ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE calls           ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE promotions      ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE suppliers       ADD COLUMN dealer_id TEXT REFERENCES dealers(id);

-- Which equipment families a part can legitimately go on. Without this a
-- fitment row is only (part, manufacturer, model pattern), and a seed that
-- generated one row per part per asset put an LCD panel and a laptop battery
-- on an uninterruptible power supply. The fitment join is what decides van
-- contents, so bad rows there are not cosmetic: they are a technician driving
-- an hour with the wrong box.
--
-- This lived only as a live ALTER for a while, which meant a rebuild from
-- these files would have quietly reintroduced the bad data.
ALTER TABLE parts ADD COLUMN families TEXT;

CREATE INDEX IF NOT EXISTS ix_acc_dealer   ON accounts(dealer_id);
CREATE INDEX IF NOT EXISTS ix_tech_dealer  ON technicians(dealer_id);
CREATE INDEX IF NOT EXISTS ix_parts_dealer ON parts(dealer_id);
CREATE INDEX IF NOT EXISTS ix_wo_dealer    ON work_orders(dealer_id);
CREATE INDEX IF NOT EXISTS ix_rep_dealer   ON repairs(dealer_id);
CREATE INDEX IF NOT EXISTS ix_calls_dealer ON calls(dealer_id);

-- The same phone number can belong to a contact at two different dealers:
-- a restaurant owner rings the refrigeration company about the walk-in and
-- the IT company about the laptops. Resolution is always (number, dealer).
CREATE INDEX IF NOT EXISTS ix_phones_lookup ON phones(e164);
