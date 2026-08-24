-- Reaching out, rather than waiting for the phone to ring.
--
-- Additive only. `outreach_consent` and `outreach_queue` were designed early
-- and designed well: consent, quiet hours and a frequency cap were all in the
-- schema before anything could use them. Nothing here rewrites either. Two
-- columns are added because the queue needs to say what KIND of call this is
-- and what evidence justified it.
--
-- Kind matters because these three are not interchangeable. A federal safety
-- recall outranks a sales call absolutely, and a system that cannot tell them
-- apart will eventually ring somebody about a discount while sitting on an
-- electrocution notice for a machine they own.

PRAGMA foreign_keys = ON;

ALTER TABLE outreach_queue ADD COLUMN kind TEXT;
ALTER TABLE outreach_queue ADD COLUMN evidence TEXT;
ALTER TABLE outreach_queue ADD COLUMN asset_id TEXT REFERENCES assets(id);
ALTER TABLE outreach_queue ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
ALTER TABLE outreach_queue ADD COLUMN priority INTEGER DEFAULT 50;

CREATE INDEX IF NOT EXISTS ix_outreach_kind ON outreach_queue(kind, status);
CREATE INDEX IF NOT EXISTS ix_outreach_dealer ON outreach_queue(dealer_id, status);

-- What this customer already owns, by equipment family, so a suggestion can
-- be for something they do NOT have. Selling somebody a second identical
-- freezer is not a recommendation, it is a catalogue being read aloud.
DROP VIEW IF EXISTS account_families;
CREATE VIEW account_families AS
SELECT s.account_id,
       a.family,
       COUNT(*) AS units
FROM assets a
JOIN sites s ON s.id = a.site_id
WHERE a.retired_on IS NULL
GROUP BY s.account_id, a.family;
