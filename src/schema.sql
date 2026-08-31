-- Praevisum data model.
--
-- Shaped after the Salesforce Field Service core model, because that model has
-- already survived contact with real dispatch operations and mine had not.
--
-- The correction that mattered: Salesforce keeps WORK ORDER (what needs doing)
-- separate from SERVICE APPOINTMENT (when and where a technician turns up).
-- My first attempt merged them, which meant one job could only ever have one
-- visit. This product exists because a failed first visit becomes 2.7 visits
-- and 13 extra days, so a schema that cannot record a second visit cannot
-- record the problem, and cannot prove it was improved. Hence `visits`.
--
-- Vocabulary, fixed once so it stops drifting:
--   manufacturer  makes the machine        Traulsen, Lenovo
--   account       owns the machine, calls us. A business OR a person
--   contact       a human being with a phone number
--   supplier      sells parts to us
--   us            the dealer

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ===================================================================
-- WHO
-- ===================================================================

-- A customer. May be a business or an individual, exactly like a Salesforce
-- Account with Person Accounts enabled. `kind` is the only difference.
CREATE TABLE IF NOT EXISTS accounts (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('business','person')),
    name         TEXT NOT NULL,
    trade_terms  TEXT,
    opened_on    TEXT,
    notes        TEXT
);

-- A physical place where machines live. One account can have many.
-- A residential account usually has exactly one.
CREATE TABLE IF NOT EXISTS sites (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    label       TEXT NOT NULL,
    address     TEXT,
    lat         REAL,
    lon         REAL,
    access_note TEXT
);
CREATE INDEX IF NOT EXISTS ix_sites_account ON sites(account_id);

-- A human being. People ring us, not buildings.
CREATE TABLE IF NOT EXISTS contacts (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    site_id     TEXT REFERENCES sites(id),
    name        TEXT NOT NULL,
    role        TEXT,
    email       TEXT,
    channel_pref TEXT DEFAULT 'sms',

    -- What they would rather be spoken to in, once we have heard them.
    --
    -- Null until a call establishes it, because guessing from a name is worse
    -- than asking: plenty of people called Ramirez would rather do this in
    -- English, and being addressed in Spanish on that assumption is its own
    -- insult. Set from what they actually said, not from who they are.
    language     TEXT
);
CREATE INDEX IF NOT EXISTS ix_contacts_account ON contacts(account_id);

-- One person, several numbers: desk, mobile, the kitchen landline.
-- This is what an inbound call is matched against.
CREATE TABLE IF NOT EXISTS phones (
    e164        TEXT PRIMARY KEY,
    contact_id  TEXT NOT NULL REFERENCES contacts(id),
    label       TEXT,
    verified    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_phones_contact ON phones(contact_id);

-- Vendors who ring us to sell things. Not customers, and never confused with
-- them: a supplier call must never be able to book one of our technicians.
CREATE TABLE IF NOT EXISTS suppliers (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    contact  TEXT,
    phone    TEXT,
    notes    TEXT
);

-- ===================================================================
-- WHAT
-- ===================================================================

-- `equipment` (the 88,544 certified models from EPA data) already exists and
-- is created by src/db.py. It is reference data: what machines exist in the
-- world. `assets` below is what OUR customers actually own.

-- One specific physical machine, at one site, pointing at a catalogue model.
-- Salesforce calls this an Asset.
CREATE TABLE IF NOT EXISTS assets (
    id            TEXT PRIMARY KEY,          -- serial number
    site_id       TEXT NOT NULL REFERENCES sites(id),
    manufacturer  TEXT NOT NULL,
    model_number  TEXT NOT NULL,
    equipment_id  INTEGER REFERENCES equipment(id),   -- null if not certified
    family        TEXT,                      -- reach-in freezer, rooftop unit
    installed_on  TEXT,
    location_note TEXT,                      -- "kitchen, back wall"
    retired_on    TEXT,
    -- Which order put this machine here, when we sold it. Without it there
    -- was no way to ask "have I already registered this delivery?", so a
    -- carrier retry or a second click of the console button minted a second
    -- identical machine on the customer's account.
    from_order    TEXT REFERENCES purchase_orders(id)
);
CREATE INDEX IF NOT EXISTS ix_assets_site  ON assets(site_id);
CREATE INDEX IF NOT EXISTS ix_assets_model ON assets(manufacturer, model_number);

CREATE TABLE IF NOT EXISTS parts (
    sku            TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    unit_cost      REAL,
    lead_time_days INTEGER DEFAULT 0,
    supplier_id    TEXT REFERENCES suppliers(id)
);

-- Which parts fit which machines. Was a string-prefix guess; now a fact per
-- row. `model_pattern` is matched with LIKE so a genuine family can be one
-- row, but an exact fitment is an exact row.
CREATE TABLE IF NOT EXISTS fitments (
    sku           TEXT NOT NULL REFERENCES parts(sku),
    manufacturer  TEXT NOT NULL,
    model_pattern TEXT NOT NULL,
    source        TEXT DEFAULT 'dealer',     -- dealer, manufacturer, observed
    PRIMARY KEY (sku, manufacturer, model_pattern)
);
CREATE INDEX IF NOT EXISTS ix_fit_sku ON fitments(sku);

-- A place parts sit. The warehouse is one. A technician's van is another.
-- Making the van a location rather than a field on the technician is what
-- lets "you already have that one in the van" be an ordinary query.
CREATE TABLE IF NOT EXISTS stock_locations (
    id       TEXT PRIMARY KEY,
    kind     TEXT NOT NULL CHECK (kind IN ('warehouse','van','consignment')),
    label    TEXT NOT NULL,
    mobile   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stock (
    location_id TEXT NOT NULL REFERENCES stock_locations(id),
    sku         TEXT NOT NULL REFERENCES parts(sku),
    on_hand     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (location_id, sku)
);
CREATE INDEX IF NOT EXISTS ix_stock_sku ON stock(sku);

-- ===================================================================
-- WHO FIXES THINGS
-- ===================================================================

CREATE TABLE IF NOT EXISTS technicians (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    phone        TEXT,
    home_base    TEXT,
    lat          REAL,
    lon          REAL,
    van_location TEXT REFERENCES stock_locations(id),

    -- A2P 10DLC blocks US business SMS from this deployment's number, so a
    -- briefing sent by text comes back error 30034 undelivered. And a
    -- technician cannot share a phone number with a customer, because desk.py
    -- routes on exactly that fact. Email is the crew's own identity.
    email        TEXT,

    active       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS technician_skills (
    technician_id TEXT NOT NULL REFERENCES technicians(id),
    family        TEXT NOT NULL,
    PRIMARY KEY (technician_id, family)
);

-- ===================================================================
-- WORK
-- ===================================================================

-- The call itself, which previously was not recorded anywhere at all.
CREATE TABLE IF NOT EXISTS calls (
    id           TEXT PRIMARY KEY,
    from_e164    TEXT NOT NULL,
    contact_id   TEXT REFERENCES contacts(id),   -- null when we do not know them
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    intent       TEXT,                            -- service, order, product, supplier
    transcript   TEXT,
    outcome      TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls_from ON calls(from_e164);

-- WHAT needs doing. Not when. One work order, potentially several visits.
CREATE TABLE IF NOT EXISTS work_orders (
    id               TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL REFERENCES accounts(id),
    site_id          TEXT NOT NULL REFERENCES sites(id),
    asset_id         TEXT REFERENCES assets(id),
    contact_id       TEXT REFERENCES contacts(id),
    opened_from_call TEXT REFERENCES calls(id),
    reported_symptom TEXT NOT NULL,
    error_code       TEXT,
    status           TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open','scheduled','in_progress','closed','cancelled')),
    opened_at        TEXT NOT NULL,
    closed_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_wo_asset  ON work_orders(asset_id);
CREATE INDEX IF NOT EXISTS ix_wo_status ON work_orders(status);

-- WHEN and WHERE a technician turns up. The thing I originally left out.
-- `seq` is 1 for the first attempt, 2 for the return trip, and counting those
-- is how first-visit-fix rate is measured at all.
CREATE TABLE IF NOT EXISTS visits (
    id             TEXT PRIMARY KEY,
    work_order_id  TEXT NOT NULL REFERENCES work_orders(id),
    seq            INTEGER NOT NULL,
    technician_id  TEXT REFERENCES technicians(id),
    promised_window TEXT,
    promised_at    TEXT,
    arrived_at     TEXT,
    completed_at   TEXT,
    outcome        TEXT CHECK (outcome IN
                     ('fixed','parts_missing','needs_specialist','no_access','cancelled')),
    found_cause    TEXT,
    labor_hours    REAL,
    tech_note      TEXT,
    UNIQUE (work_order_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_visits_wo ON visits(work_order_id);

-- Parts held for a specific visit. Held against the visit, not the job,
-- because a return trip needs its own parts.
CREATE TABLE IF NOT EXISTS reservations (
    sku          TEXT NOT NULL REFERENCES parts(sku),
    location_id  TEXT NOT NULL REFERENCES stock_locations(id),
    visit_id     TEXT NOT NULL REFERENCES visits(id),
    qty          INTEGER NOT NULL DEFAULT 1,
    reserved_at  TEXT NOT NULL,
    released_at  TEXT,
    PRIMARY KEY (sku, location_id, visit_id)
);
CREATE INDEX IF NOT EXISTS ix_res_open ON reservations(sku, released_at);

-- What was actually fitted, per visit.
CREATE TABLE IF NOT EXISTS parts_used (
    visit_id TEXT NOT NULL REFERENCES visits(id),
    sku      TEXT NOT NULL REFERENCES parts(sku),
    qty      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (visit_id, sku)
);

-- ===================================================================
-- THE CORPUS
-- ===================================================================

-- A closed, searchable account of one fault and what actually fixed it.
-- Written when a visit completes. This is the only table in the database
-- that cannot be re-downloaded from anywhere, and it is the whole product.
CREATE TABLE IF NOT EXISTS repairs (
    id               TEXT PRIMARY KEY,
    visit_id         TEXT REFERENCES visits(id),
    asset_id         TEXT REFERENCES assets(id),
    manufacturer     TEXT NOT NULL,
    model_number     TEXT NOT NULL,
    family           TEXT,
    reported_symptom TEXT,          -- the caller's words
    error_code       TEXT,
    found_cause      TEXT NOT NULL, -- the technician's words
    tech_note        TEXT,
    parts_consumed   TEXT,          -- comma separated SKUs, denormalised for search
    labor_hours      REAL,
    first_visit_fix  INTEGER,
    closed_on        TEXT NOT NULL,
    technician_id    TEXT REFERENCES technicians(id),
    embedding        BLOB           -- filled when retrieval moves off TF-IDF
);
CREATE INDEX IF NOT EXISTS ix_rep_asset ON repairs(asset_id);
CREATE INDEX IF NOT EXISTS ix_rep_model ON repairs(manufacturer, model_number);

-- ===================================================================
-- COMMERCIAL
-- ===================================================================

CREATE TABLE IF NOT EXISTS promotions (
    id        TEXT PRIMARY KEY,
    headline  TEXT NOT NULL,
    detail    TEXT,
    starts    TEXT,
    ends      TEXT NOT NULL,
    terms     TEXT
);

CREATE TABLE IF NOT EXISTS promotion_parts (
    promotion_id TEXT NOT NULL REFERENCES promotions(id),
    sku          TEXT NOT NULL REFERENCES parts(sku),
    PRIMARY KEY (promotion_id, sku)
);

CREATE TABLE IF NOT EXISTS supplier_offers (
    id           TEXT PRIMARY KEY,
    supplier_id  TEXT REFERENCES suppliers(id),
    call_id      TEXT REFERENCES calls(id),
    offering     TEXT NOT NULL,
    price_quoted TEXT,
    lead_time    TEXT,
    logged_at    TEXT NOT NULL,
    status       TEXT DEFAULT 'for buyer review',
    committed    INTEGER DEFAULT 0 CHECK (committed = 0)
);

-- ===================================================================
-- VIEWS
-- ===================================================================

-- Free stock: on hand minus anything held for a visit that has not been
-- released. One definition, used everywhere, so "is it available" can never
-- mean two different things in two different places.
CREATE VIEW IF NOT EXISTS stock_available AS
SELECT s.location_id, s.sku, s.on_hand,
       COALESCE((SELECT SUM(r.qty) FROM reservations r
                 WHERE r.sku = s.sku AND r.location_id = s.location_id
                   AND r.released_at IS NULL), 0) AS held,
       s.on_hand - COALESCE((SELECT SUM(r.qty) FROM reservations r
                 WHERE r.sku = s.sku AND r.location_id = s.location_id
                   AND r.released_at IS NULL), 0) AS free
FROM stock s;

-- The number this product claims to move. Now measurable, because visits
-- are counted rather than assumed.
CREATE VIEW IF NOT EXISTS first_visit_fix AS
SELECT w.id AS work_order_id,
       COUNT(v.id) AS visits,
       MIN(CASE WHEN v.seq = 1 AND v.outcome = 'fixed' THEN 1 ELSE 0 END) AS fixed_first_time
FROM work_orders w JOIN visits v ON v.work_order_id = w.id
WHERE w.status = 'closed'
GROUP BY w.id;
