-- The three things this phone line actually does, beyond taking a fault report.
--
-- SERVICE   find the machine, raise a ticket, find the nearest qualified
--           technician who is genuinely free, and promise a window that
--           exists in a calendar rather than one the model invented.
--
-- SUPPLY    they want to buy. Recommend from what we know, take the order,
--           ship it, and give an honest delivery date.
--
-- OUTREACH  ring an existing customer occasionally about what they own, at a
--           sane hour, with a reason. Consent is a column, not a policy
--           document: no row, no call.
--
-- The recommendation engine deserves a note. Retailers rank products by
-- review scores written by people who owned the thing for a week. We rank by
-- what broke, because we are the ones who drove out and fixed it. A model that
-- has failed eight times in our own book is not a five star product no matter
-- what a website says, and telling a customer that is the most useful thing a
-- parts desk can do.

PRAGMA foreign_keys = ON;

-- ===================================================================
-- WHEN A TECHNICIAN IS ACTUALLY FREE
-- ===================================================================

-- Regular working hours. dow: 0=Monday.
CREATE TABLE IF NOT EXISTS technician_hours (
    technician_id TEXT NOT NULL REFERENCES technicians(id),
    dow           INTEGER NOT NULL CHECK (dow BETWEEN 0 AND 6),
    start_min     INTEGER NOT NULL,      -- minutes from midnight
    end_min       INTEGER NOT NULL,
    PRIMARY KEY (technician_id, dow)
);

-- Everything already in the diary: booked visits, holidays, training.
-- A promise is only honest if it was checked against this.
CREATE TABLE IF NOT EXISTS appointments (
    id            TEXT PRIMARY KEY,
    technician_id TEXT NOT NULL REFERENCES technicians(id),
    visit_id      TEXT REFERENCES visits(id),
    starts_at     TEXT NOT NULL,
    ends_at       TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'visit'
                  CHECK (kind IN ('visit','travel','leave','training','hold')),
    site_id       TEXT REFERENCES sites(id),
    note          TEXT
);
CREATE INDEX IF NOT EXISTS ix_appt_tech ON appointments(technician_id, starts_at);

-- ===================================================================
-- BUYING, NOT BREAKING
-- ===================================================================

CREATE TABLE IF NOT EXISTS purchase_orders (
    id             TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL REFERENCES accounts(id),
    site_id        TEXT REFERENCES sites(id),
    contact_id     TEXT REFERENCES contacts(id),
    from_call      TEXT REFERENCES calls(id),
    status         TEXT NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','confirmed','picked','shipped','delivered','cancelled')),
    subtotal       REAL,
    placed_at      TEXT NOT NULL,
    confirmed_at   TEXT,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS ix_po_account ON purchase_orders(account_id);

CREATE TABLE IF NOT EXISTS purchase_lines (
    po_id        TEXT NOT NULL REFERENCES purchase_orders(id),
    line_no      INTEGER NOT NULL,
    sku          TEXT REFERENCES parts(sku),
    equipment_id INTEGER REFERENCES equipment(id),   -- buying a whole machine
    description  TEXT NOT NULL,
    qty          INTEGER NOT NULL DEFAULT 1,
    unit_price   REAL,
    PRIMARY KEY (po_id, line_no)
);

CREATE TABLE IF NOT EXISTS shipments (
    id            TEXT PRIMARY KEY,
    po_id         TEXT NOT NULL REFERENCES purchase_orders(id),
    carrier       TEXT NOT NULL CHECK (carrier IN ('UPS','FedEx','USPS','local van')),
    service_level TEXT,
    tracking      TEXT,
    shipped_at    TEXT,
    eta_date      TEXT,
    delivered_at  TEXT,
    cost          REAL
);
CREATE INDEX IF NOT EXISTS ix_ship_po ON shipments(po_id);

-- Carrier options, so a delivery date is quoted from a table rather than
-- guessed by a model. Days are business days.
CREATE TABLE IF NOT EXISTS carrier_options (
    carrier       TEXT NOT NULL,
    service_level TEXT NOT NULL,
    days_min      INTEGER NOT NULL,
    days_max      INTEGER NOT NULL,
    cost          REAL NOT NULL,
    max_lbs       REAL,
    PRIMARY KEY (carrier, service_level)
);

-- ===================================================================
-- WHAT TO TALK TO THEM ABOUT NEXT
-- ===================================================================

-- Built from what they said on a call, not from a marketing list. If a caller
-- mentions their ice machine is on its last legs, that is a row here.
CREATE TABLE IF NOT EXISTS wishlist (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    from_call   TEXT REFERENCES calls(id),
    want        TEXT NOT NULL,          -- "replacement ice machine"
    family      TEXT,
    reason      TEXT,                   -- what they actually said
    noted_at    TEXT NOT NULL,
    status      TEXT DEFAULT 'open' CHECK (status IN ('open','quoted','bought','dropped'))
);
CREATE INDEX IF NOT EXISTS ix_wish_account ON wishlist(account_id);

-- Permission to ring them, recorded per account. TCPA treats an AI voice as an
-- artificial or prerecorded voice, so outbound needs prior express consent and
-- several states want an AI disclosure at the top of the call. No row here, or
-- a revoked one, means the outreach agent does not dial. This is enforced in
-- code, not left to a prompt.
CREATE TABLE IF NOT EXISTS outreach_consent (
    account_id   TEXT PRIMARY KEY REFERENCES accounts(id),
    granted      INTEGER NOT NULL DEFAULT 0,
    granted_on   TEXT,
    granted_via  TEXT,                  -- "asked on call CALL-1234"
    revoked_on   TEXT,
    quiet_before INTEGER DEFAULT 540,   -- minutes: no calls before 9am
    quiet_after  INTEGER DEFAULT 1020,  -- no calls after 5pm
    max_per_days INTEGER DEFAULT 30     -- and not more than once a month
);

CREATE TABLE IF NOT EXISTS outreach_queue (
    id           TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(id),
    contact_id   TEXT REFERENCES contacts(id),
    reason       TEXT NOT NULL,          -- why this call is worth making
    wishlist_id  TEXT REFERENCES wishlist(id),
    due_after    TEXT NOT NULL,
    status       TEXT DEFAULT 'queued'
                 CHECK (status IN ('queued','called','skipped','blocked')),
    called_at    TEXT,
    outcome      TEXT
);
CREATE INDEX IF NOT EXISTS ix_outreach_due ON outreach_queue(status, due_after);

-- ===================================================================
-- VIEWS
-- ===================================================================

-- Reliability, from our own vans rather than from strangers on the internet.
-- A model we have fixed repeatedly is one we should think twice about selling.
CREATE VIEW IF NOT EXISTS model_reliability AS
SELECT r.manufacturer,
       r.model_number,
       COUNT(*)                                   AS faults,
       COUNT(DISTINCT r.asset_id)                 AS units_affected,
       ROUND(AVG(r.first_visit_fix) * 100)        AS first_visit_fix_pct,
       ROUND(AVG(r.labor_hours), 2)               AS avg_hours,
       MAX(r.closed_on)                           AS last_fault
FROM repairs r
GROUP BY r.manufacturer, r.model_number;

-- How many of each model are out there, so "8 faults" can be read against
-- "on 2 machines" rather than in a vacuum.
CREATE VIEW IF NOT EXISTS model_installed AS
SELECT manufacturer, model_number, COUNT(*) AS installed
FROM assets WHERE retired_on IS NULL
GROUP BY manufacturer, model_number;
