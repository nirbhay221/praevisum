-- What the outside world says, cached.
--
-- Additive. Cached because ratings move slowly, a phone call must never wait
-- on somebody else's API, and a month-old rating is as good as a fresh one.
-- It also means the system keeps working, and the demo keeps working, when
-- there is no key and no network.
--
-- Stored SEPARATELY from anything we know. Never joined into a single score
-- with our own fault rate: our numbers can be justified out loud and a blended
-- one cannot.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS outside_reviews (
    manufacturer  TEXT NOT NULL,
    model_number  TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'bestbuy',

    rating        REAL,          -- null means we looked and found nothing
    review_count  INTEGER DEFAULT 0,
    matched_name  TEXT,          -- what their catalogue called it

    fetched_at    TEXT NOT NULL,
    raw           TEXT,

    PRIMARY KEY (manufacturer, model_number, source)
);
