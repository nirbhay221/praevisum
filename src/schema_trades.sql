-- What a trade costs, per trade.
--
-- Additive.
--
-- WHY THIS EXISTS
--
-- This service answers several different businesses' phones. The database has
-- always known that: two dealers, two numbers, two trades, separate
-- technicians, separate parts, separate repair corpora, every query scoped to
-- dealer_id.
--
-- Everything built on top of it was refrigeration-shaped. The labour rate was
-- a constant naming occupation 49-9021, Heating, Air Conditioning and
-- Refrigeration Mechanics. Quoting an IT job at a refrigeration mechanic's
-- wage is not a rounding error, it is quoting the wrong trade, and the desk
-- said it with the same confidence as everything else.
--
-- THE WAGES ARE REAL AND THEY COME FROM DIFFERENT PLACES
--
-- BLS publishes refrigeration mechanics at metro level for this dealer's own
-- city. It does NOT publish computer user support specialists for the same
-- metro: that series does not exist. So one figure is local and the other is
-- state-wide, and the row says which rather than pretending they are the same
-- kind of number.
CREATE TABLE IF NOT EXISTS trade_rates (
    trade         TEXT PRIMARY KEY,     -- refrigeration, it

    occupation    TEXT NOT NULL,        -- the SOC code, so it can be checked
    occupation_name TEXT,
    hourly_wage   REAL NOT NULL,
    series_id     TEXT,                 -- the exact BLS series
    geography     TEXT,                 -- metro, state, national
    year          INTEGER,

    -- What the shop charges as a multiple of the wage. Different trades carry
    -- different overheads: a refrigeration van, its stock, the EPA
    -- certification and the fuel is not the same business as a technician
    -- with a toolkit and a bench.
    multiplier    REAL NOT NULL DEFAULT 2.6,
    call_out      REAL
);
