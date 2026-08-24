-- The dealer's own premises, and customers who come to them.
--
-- Everything in this file is additive. Nothing above it is altered, because
-- the two obvious places to put this both refuse to take it and are load
-- bearing:
--
--   stock_locations.kind  CHECK (kind IN ('warehouse','van','consignment'))
--   appointments.kind     CHECK (kind IN ('visit','travel','leave',
--                                         'training','hold'))
--   appointments.technician_id  NOT NULL
--
-- Widening either CHECK means rebuilding the table in SQLite, and a counter
-- booking has no technician at all, so it would break the NOT NULL as well.
-- A person walking into a trade counter is genuinely a different thing from a
-- van being dispatched: nobody drives, nobody is assigned, and the customer
-- carries the part out. It gets its own table rather than being forced into
-- one that was built for dispatch.
--
-- What it does reuse: a branch points at the stock_location it draws from, so
-- "is the part actually at that counter" is answered by the existing stock
-- tables rather than a second copy of them.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS branches (
    id            TEXT PRIMARY KEY,
    dealer_id     TEXT REFERENCES dealers(id),
    label         TEXT NOT NULL,
    address       TEXT,
    lat           REAL,
    lon           REAL,
    phone_e164    TEXT,

    -- Which shelf this counter sells off. Points at the existing stock
    -- locations rather than duplicating them, so telling a customer the part
    -- is waiting for them is the same query the parts desk already runs.
    stock_location_id TEXT REFERENCES stock_locations(id),

    -- Not every site a dealer owns has a counter a member of the public can
    -- walk into. A warehouse with a loading bay is not a trade counter, and
    -- sending somebody there is worse than not offering.
    has_counter   INTEGER NOT NULL DEFAULT 0,

    -- Opening hours as minutes past midnight, matching technician_hours so the
    -- two can be reasoned about the same way.
    opens_min     INTEGER DEFAULT 480,      -- 08:00
    closes_min    INTEGER DEFAULT 1020,     -- 17:00
    open_days     TEXT DEFAULT '0,1,2,3,4', -- Monday is 0, as Python has it
    closed_note   TEXT
);

CREATE INDEX IF NOT EXISTS ix_branches_dealer ON branches(dealer_id);

-- Somebody bringing a machine, or a part, to the counter themselves.
--
-- Deliberately not an appointment row. No technician is assigned, no travel
-- is booked, and no diary is blocked, because none of those things happen
-- when a customer drives to us.
CREATE TABLE IF NOT EXISTS counter_bookings (
    id            TEXT PRIMARY KEY,
    dealer_id     TEXT REFERENCES dealers(id),
    branch_id     TEXT NOT NULL REFERENCES branches(id),
    account_id    TEXT REFERENCES accounts(id),
    contact_id    TEXT REFERENCES contacts(id),
    asset_id      TEXT REFERENCES assets(id),
    work_order_id TEXT REFERENCES work_orders(id),
    from_call     TEXT REFERENCES calls(id),

    slot_at       TEXT NOT NULL,
    reason        TEXT,                     -- what they are bringing and why
    booked_at     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'booked'
                  CHECK (status IN ('booked','arrived','done','no_show','cancelled'))
);

CREATE INDEX IF NOT EXISTS ix_counter_branch ON counter_bookings(branch_id, slot_at);
CREATE INDEX IF NOT EXISTS ix_counter_account ON counter_bookings(account_id);
