-- What a job costs, and which lines the warranty pays for.
--
-- Additive.
--
-- THE MONEY QUESTION HAD NO ANSWER
--
-- A visit recorded labor_hours after the fact. Nothing anywhere held a labour
-- rate, a call-out charge or an out-of-hours premium: grep found zero
-- references to any of them. So the first question a customer asks, what will
-- this cost me, met the standing rule that there are no prices beyond what a
-- tool returned, and became "I will have to confirm and follow up". Every
-- time. A desk that cannot answer the money question is a desk they stop
-- trusting to answer any question.
--
-- COVERAGE IS PER LINE, NOT PER MACHINE
--
-- This is the part a flag on the asset cannot express, and the published
-- warranties are unambiguous about it:
--
--   Wear items are excluded from every one of them. Door gaskets, light
--   bulbs and shelf pins are chargeable on a machine that is otherwise fully
--   covered, and the door gasket is one of the commonest calls we take.
--
--   Compressor cover outlasts parts and labour cover almost everywhere. A
--   six and a half year old Traulsen has a covered compressor and nothing
--   else covered.
--
--   Traulsen's compressor cover is the part only: "all installation,
--   recharging, and repair costs shall be the responsibility of the Owner".
--   So the compressor is free and the four hours to fit it are not.
--
-- Which means a single covered/not-covered boolean gets the answer wrong in
-- both directions, and the direction it gets wrong is the one that costs
-- somebody money they did not expect to spend.
--
-- WHY TERMS ARE A TABLE AND NOT A DICT
--
-- They are published facts with a source and a date, they differ per brand
-- and per series, and they change: Traulsen's six year term applies to units
-- invoiced from January 2023 and not before. A row can carry the URL it came
-- from and the day it was read, so a number a customer disputes can be
-- checked rather than defended.

PRAGMA foreign_keys = ON;

-- What each dealer charges for an hour. Null means fall back to the federal
-- wage figure for this trade in this metro, which is a defensible starting
-- point rather than an invented one.
ALTER TABLE dealers ADD COLUMN labour_rate REAL;
ALTER TABLE dealers ADD COLUMN call_out_fee REAL;


-- Published manufacturer warranty terms. Real, sourced, and dated.
--
-- `series` is a LIKE pattern against the model number, because the terms
-- genuinely split that way: Beverage-Air's CF and CT lines get one year where
-- everything else gets three, and Avantco runs one, two and three year terms
-- across three groups of prefixes. A '%' row is the brand default.
CREATE TABLE IF NOT EXISTS warranty_terms (
    manufacturer   TEXT NOT NULL,
    series         TEXT NOT NULL DEFAULT '%',   -- LIKE pattern on model_number

    parts_years    REAL,
    labour_years   REAL,
    compressor_years REAL,

    -- Traulsen ships a replacement compressor and bills the owner for fitting
    -- it. Without this the quote is wrong by several hundred dollars in the
    -- direction the customer notices.
    compressor_labour_covered INTEGER NOT NULL DEFAULT 1,

    -- Registration conditions we cannot verify from here. Never used to deny
    -- cover, only to warn: Beverage-Air requires registration within ten days
    -- of installation, and whether the customer did that is their record.
    condition_note TEXT,

    source_url     TEXT,
    read_on        TEXT,

    PRIMARY KEY (manufacturer, series)
);


-- Components no warranty covers, on any machine, at any age.
--
-- A list rather than a column on `parts` because it has to answer for a part
-- we have never stocked, named by a technician in a sentence.
CREATE TABLE IF NOT EXISTS wear_items (
    pattern TEXT PRIMARY KEY,        -- matched against a part name, lowercased
    why     TEXT NOT NULL,
    source_url TEXT
);


-- What we told somebody it would cost.
--
-- Recorded because a quote given on a call is the thing most likely to be
-- argued about later, and because the review pass can then compare what was
-- quoted against what the visit actually billed and learn from the gap.
CREATE TABLE IF NOT EXISTS quotes (
    id          TEXT PRIMARY KEY,
    dealer_id   TEXT REFERENCES dealers(id),
    call_id     TEXT,
    asset_id    TEXT REFERENCES assets(id),

    hours       REAL,
    hourly_rate REAL,
    rate_source TEXT,               -- so the number can be checked, not defended

    total       REAL NOT NULL,
    covered_total REAL NOT NULL DEFAULT 0,   -- what the warranty absorbed
    after_hours INTEGER NOT NULL DEFAULT 0,

    quoted_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_quotes_asset ON quotes(asset_id);
CREATE INDEX IF NOT EXISTS ix_quotes_call  ON quotes(call_id);

CREATE TABLE IF NOT EXISTS quote_lines (
    quote_id TEXT NOT NULL REFERENCES quotes(id),
    seq      INTEGER NOT NULL,
    what     TEXT NOT NULL,
    amount   REAL NOT NULL,
    charged  INTEGER NOT NULL,       -- 0 where the warranty pays for it
    why      TEXT,
    PRIMARY KEY (quote_id, seq)
);
