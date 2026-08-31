-- Closing the loop between what we told an engineer to take and what they
-- actually fitted.
--
-- WHAT WAS MISSING
--
-- `build_briefing` works out `load_these` -- the parts this fault usually
-- needs, filtered to the ones that physically fit -- and sends it to the
-- engineer. It was computed fresh every time and never written down.
--
-- So the single most useful number in field service could not be calculated.
-- We told somebody to take four parts. They fitted one. Nobody knew, because
-- the advice existed only inside the message that carried it. The corpus
-- learns WHAT fixed a fault, which is why `commonly_needed` works at all; it
-- has never had any way to learn whether OUR OWN ADVICE was any good.
--
-- The published field-service position is blunt about why this matters: the
-- primary cause of a return trip is not having the right part on the van, and
-- shops moving first-time fix from 65% to 85% cut second visits by more than
-- half in a quarter. You cannot move a number you do not record.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS parts_recommended (
    visit_id  TEXT NOT NULL REFERENCES visits(id),
    sku       TEXT NOT NULL,

    -- The evidence we had AT THE TIME, kept with the advice rather than
    -- recomputed later. Recomputing would judge a past recommendation against
    -- facts it could not have known, which flatters it.
    because   TEXT,
    likelihood REAL,

    told_on   TEXT NOT NULL,
    PRIMARY KEY (visit_id, sku)
);

CREATE INDEX IF NOT EXISTS ix_recommended_visit ON parts_recommended(visit_id);


-- What a visit actually cost us, at cost price, once it closed.
--
-- `parts_used` records what was fitted and `parts.unit_cost` records what it
-- cost, and nothing has ever multiplied them. So a model that eats parts and
-- a model that does not looked identical on the books, and a complaint had no
-- number attached to it at all -- which is the number that decides whether a
-- product is worth stocking.
--
-- Stored rather than computed on the fly BECAUSE COST PRICES MOVE. What a
-- gasket cost us in March is what that March visit cost, and recalculating it
-- against today's price rewrites history to match the present.
CREATE TABLE IF NOT EXISTS visit_cost (
    visit_id     TEXT PRIMARY KEY REFERENCES visits(id),
    work_order_id TEXT,
    dealer_id    TEXT,

    -- Split, because they behave differently. Parts are a direct loss against
    -- the machine; labour is a loss against the schedule.
    parts_cost   REAL NOT NULL DEFAULT 0,
    labour_hours REAL NOT NULL DEFAULT 0,
    labour_cost  REAL NOT NULL DEFAULT 0,

    -- Denormalised so a cost survives the asset being retired or sold, for
    -- the same reason complaints carry make and model outright.
    manufacturer TEXT,
    model_number TEXT,
    family       TEXT,

    -- The complaint this visit answers, when there is one. This is the join
    -- the whole table exists for: what this complaint has actually cost us.
    complaint_id TEXT,

    costed_on    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_visit_cost_model
    ON visit_cost(manufacturer, model_number);
CREATE INDEX IF NOT EXISTS ix_visit_cost_complaint
    ON visit_cost(complaint_id);
