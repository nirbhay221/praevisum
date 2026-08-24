-- What customers tell us about the things they bought.
--
-- The recommendation side of this product already had a good idea and almost
-- no evidence. It ranked machines by faults we had been out to, divided by how
-- many our customers own. That is the right shape, but service calls only
-- capture the failures serious enough to send a van. Everything else a
-- customer says about a machine, that it is loud, that the door seal is
-- flimsy, that the parts cost a fortune, that it trips the breaker on a hot
-- day, was heard on a phone call and then thrown away.
--
-- Those are the sentences somebody about to spend four thousand dollars
-- actually wants. So they get written down.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS complaints (
    id            TEXT PRIMARY KEY,
    dealer_id     TEXT REFERENCES dealers(id),
    account_id    TEXT REFERENCES accounts(id),

    -- The specific machine when we know it, but the make and model are stored
    -- outright rather than only through the asset. A complaint about a model
    -- has to outlive the individual unit being retired, sold or replaced,
    -- otherwise the evidence disappears exactly when the customer replaces the
    -- machine they were complaining about.
    asset_id      TEXT REFERENCES assets(id),
    manufacturer  TEXT NOT NULL,
    model_number  TEXT NOT NULL,
    family        TEXT,

    from_call     TEXT REFERENCES calls(id),

    -- Their words, not a category we chose for them. The category is a coarse
    -- bucket on top; `what` is what they actually said and is the thing worth
    -- reading back to the next customer.
    what          TEXT NOT NULL,
    category      TEXT,          -- reliability, noise, design, running_cost,
                                 -- parts_cost, support, install
    severity      TEXT,          -- minor, major, unusable

    raised_at     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',  -- open, resolved, withdrawn

    -- Set when this complaint turned out to be the early warning of a repair
    -- that followed. The reason complaints are worth recording at all: the
    -- customer notices weeks before they ring, so a complaint is a leading
    -- indicator and a service call is a lagging one. Null for the majority
    -- that never become a job, which is honest, because most grumbles do not.
    predicted_repair TEXT REFERENCES repairs(id)
);

CREATE INDEX IF NOT EXISTS ix_complaints_model
    ON complaints(manufacturer, model_number);
CREATE INDEX IF NOT EXISTS ix_complaints_dealer ON complaints(dealer_id);
CREATE INDEX IF NOT EXISTS ix_complaints_account ON complaints(account_id);

-- Complaints per model, alongside how many of that model our customers own.
-- The denominator is the whole point: three complaints about a model we sold
-- forty of is a different fact from three about a model we sold four of, and
-- without the count they read identically.
DROP VIEW IF EXISTS model_complaints;
CREATE VIEW model_complaints AS
SELECT manufacturer,
       model_number,
       COUNT(*)                                             AS complaints,
       COUNT(DISTINCT COALESCE(asset_id, id))               AS units_complaining,
       SUM(CASE WHEN severity = 'unusable' THEN 1 ELSE 0 END) AS severe,
       GROUP_CONCAT(DISTINCT category)                      AS categories
FROM complaints
WHERE status <> 'withdrawn'
GROUP BY manufacturer, model_number;

-- How many of each model we have actually put into service, which is the
-- honest denominator for both complaints and service calls.
DROP VIEW IF EXISTS model_supplied;
CREATE VIEW model_supplied AS
SELECT a.manufacturer,
       a.model_number,
       COUNT(*)                        AS units,
       COUNT(DISTINCT s.account_id)    AS customers,
       MIN(a.installed_on)             AS first_installed
FROM assets a
JOIN sites s ON s.id = a.site_id
WHERE a.retired_on IS NULL
GROUP BY a.manufacturer, a.model_number;
