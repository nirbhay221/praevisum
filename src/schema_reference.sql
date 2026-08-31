-- Public reference data. What machines exist in the world, and which of them
-- have been recalled.
--
-- This is the one thing every dealer shares, because nobody owns it: it is
-- federal certification data and safety notices. A refrigeration company and
-- an IT company look at the same catalogue and keep entirely separate repair
-- histories. See schema_tenant.sql for the half that is never shared.
--
-- These two tables spent a while existing only in the live database, created
-- ad-hoc by an early loader. schema.sql still carries a comment claiming
-- src/db.py creates them, which it does not. That drift meant `assets` had a
-- foreign key to a table no schema file defined, so rebuilding from source
-- produced a database that could not take a single asset. Written down here
-- so a rebuild produces the database that is actually running.

PRAGMA foreign_keys = ON;

-- Certified equipment, from EPA and Energy Star datasets.
CREATE TABLE IF NOT EXISTS equipment (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,          -- 'energystar'
    dataset       TEXT NOT NULL,          -- 'Commercial Refrigerators and Freezers'
    category      TEXT NOT NULL,          -- our own grouping, e.g. 'refrigeration'
    brand         TEXT NOT NULL,
    model_number  TEXT NOT NULL,
    product_type  TEXT,
    defrost_type  TEXT,
    refrigerant   TEXT,                   -- R-290 and R-600a are flammable
    capacity      TEXT,
    daily_kwh     REAL,

    -- An ice machine is not rated the way a freezer is. ENERGY STAR publishes
    -- a harvest rate and an energy figure per 100 lb of ice, so daily_kwh is
    -- computed from the two by scripts/backfill_ice_machines. Without these
    -- columns every ice machine in the catalogue was invisible to the tool
    -- that recommends equipment, because it required daily_kwh and no ice
    -- machine has one published.
    --
    -- Harvest rate is the sizing question people actually ask: a restaurant
    -- wants pounds a day, never cubic feet.
    ice_lbs_day      REAL,
    water_gal_100lbs REAL,

    certified_on  TEXT,
    raw           TEXT,                   -- full original record

    -- Does a human being drive to this machine? A commercial freezer yes, a
    -- ceiling fan no. Filters the catalogue down to equipment this product
    -- has any business talking about.
    site_visit    INTEGER DEFAULT 1,

    -- Model number with dashes, spaces and case stripped. A model number read
    -- down a phone line never arrives clean, and matching on the raw string
    -- fails on almost every real call.
    model_norm    TEXT,

    UNIQUE(source, dataset, brand, model_number)
);

CREATE INDEX IF NOT EXISTS ix_equip_norm  ON equipment(model_norm);
CREATE INDEX IF NOT EXISTS ix_equip_brand ON equipment(brand);

-- Published safety recalls. Brands and models are stored as published, which
-- is to say as free text, because that is how they are published.
CREATE TABLE IF NOT EXISTS recalls (
    id            INTEGER PRIMARY KEY,
    recall_number TEXT,
    recall_date   TEXT,
    title         TEXT,
    hazard        TEXT,
    remedy        TEXT,
    brands        TEXT,                   -- comma separated, as published
    models        TEXT,
    url           TEXT,
    UNIQUE(recall_number)
);
