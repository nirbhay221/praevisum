-- Businesses we have never met, and the rules for approaching one.
--
-- WHY THIS IS A SEPARATE TABLE FROM accounts
--
-- Everything in outreach.py is keyed on account_id and starts from a consent
-- row. A prospect has neither, and pretending otherwise would mean writing a
-- fake account for every business we have merely heard of, which then shows up
-- in every count of who our customers are.
--
-- WHAT THE LAW ACTUALLY PERMITS HERE
--
-- The Telemarketing Sales Rule broadly exempts calls from a marketer to a
-- BUSINESS, and the national Do Not Call registry does not reach them. That is
-- the entire legal basis for this file existing.
--
-- It stops at the handset. The FCC treats an AI-generated voice as an
-- artificial or prerecorded voice, and the TCPA treats every WIRELESS number
-- as residential no matter whose desk it sits on. There is no business
-- exemption for a mobile. So an AI voice may ring a published business
-- landline and may not ring a mobile, and since most small restaurants run on
-- a mobile, the honest consequence is that most prospects are unreachable by
-- this desk and must be left alone.
--
-- Unknown line type is treated as mobile. Guessing in the permissive
-- direction is how the fines happen.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS prospects (
    id            TEXT PRIMARY KEY,
    dealer_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    kind          TEXT,                   -- restaurant, cafe, office, hotel
    address       TEXT,
    phone_e164    TEXT,
    line_type     TEXT,                   -- landline | mobile | voip | unknown
    lat           REAL,
    lon           REAL,
    source        TEXT,                   -- where we learned of them
    found_on      TEXT NOT NULL,

    -- The reason to ring, in the words the evidence gave us. A prospect with
    -- no reason is a cold call, and a cold call is what this is trying not to
    -- be.
    signal        TEXT,
    signal_kind   TEXT,                   -- public_complaint | heat | recall | new
    signal_score  REAL DEFAULT 0,
    signal_seen   TEXT,                   -- the quoted evidence, verbatim

    approached_on TEXT,
    outcome       TEXT,
    UNIQUE(dealer_id, phone_e164)
);

CREATE INDEX IF NOT EXISTS ix_prospect_score
    ON prospects(dealer_id, signal_score DESC);

-- The internal do-not-call list, which is a separate obligation from the
-- federal registry and survives the business relationship. A request has to be
-- honoured within 10 business days and the record kept for 4 years, so rows
-- here are never deleted: that is the point of them.
CREATE TABLE IF NOT EXISTS do_not_call (
    e164        TEXT PRIMARY KEY,
    asked_on    TEXT NOT NULL,
    asked_by    TEXT,                     -- who told us, if we know
    heard_on    TEXT,                     -- the call it was said on
    keep_until  TEXT NOT NULL,            -- asked_on + 4 years
    note        TEXT
);

-- What a lookup told us about a number, so we pay for it once. Line type is
-- the gate on the whole feature, and re-asking on every run is both a bill and
-- a rate limit.
CREATE TABLE IF NOT EXISTS line_type_cache (
    e164        TEXT PRIMARY KEY,
    line_type   TEXT NOT NULL,
    carrier     TEXT,
    checked_on  TEXT NOT NULL,
    source      TEXT
);
