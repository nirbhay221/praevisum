-- Who pays, and who is allowed to do the work.
--
-- Additive. Three gaps that were all invisible from inside the system because
-- nothing ever asked the question.
--
-- WARRANTY
--
-- There was no warranty anywhere: no table, no column, no tool. So the desk
-- would quote four hundred dollars for a control board on a machine that was
-- eleven months old and covered, and be confidently wrong in the direction
-- that costs a customer money and costs us the relationship.
--
-- It is also the one thing a customer is most likely to know and we were not.
--
-- CERTIFICATION IS NOT SKILL
--
-- `technician_skills` records that somebody works on reach-in freezers. It
-- does not record that they hold EPA Section 608, which is legally required
-- in the United States to open a refrigerant circuit at all.
--
-- The briefing already tells a technician that R-290 is flammable and
-- charge-limited. It could not tell anyone whether the person being sent was
-- licensed to touch it, which is a legal exposure rather than a preference.
--
-- CUSTOMER AVAILABILITY
--
-- The diary knew when a technician was free and nobody ever asked when the
-- customer could be there. A restaurant is not sitting waiting: they have a
-- lunch service, and a window offered across it is a window that gets
-- refused, or worse, accepted and missed.

PRAGMA foreign_keys = ON;

ALTER TABLE assets ADD COLUMN warranty_until TEXT;
ALTER TABLE assets ADD COLUMN warranty_terms TEXT;
ALTER TABLE assets ADD COLUMN warranty_provider TEXT;

CREATE INDEX IF NOT EXISTS ix_assets_warranty ON assets(warranty_until);


-- What a technician is legally allowed to do, as against what they are good at.
--
-- EPA 608 has types, and they are not interchangeable: Type I is small
-- appliances, Type II high pressure, Type III low pressure, Universal all
-- three. A Type I certification does not permit work on a walk-in.
CREATE TABLE IF NOT EXISTS technician_certs (
    technician_id TEXT NOT NULL REFERENCES technicians(id),
    cert          TEXT NOT NULL,     -- EPA608-I, EPA608-II, EPA608-III, EPA608-UNIVERSAL
    number        TEXT,
    expires_on    TEXT,              -- null where the certification does not expire

    PRIMARY KEY (technician_id, cert)
);

CREATE INDEX IF NOT EXISTS ix_tech_certs ON technician_certs(cert);


-- When the customer said they could be there.
--
-- Stored rather than held in the conversation, because the diary is consulted
-- again when a slot is re-negotiated and a window the customer already ruled
-- out must not be offered back to them.
CREATE TABLE IF NOT EXISTS site_availability (
    id         INTEGER PRIMARY KEY,
    site_id    TEXT NOT NULL REFERENCES sites(id),

    -- 0 is Monday, matching Python. Null means every day.
    weekday    INTEGER,
    from_min   INTEGER NOT NULL,     -- minutes from midnight
    to_min     INTEGER NOT NULL,

    note       TEXT,                 -- "closed for lunch service", in their words
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_site_availability ON site_availability(site_id);
