-- Whether the parts actually left the building.
--
-- Additive.
--
-- WHY
--
-- This project opens with "a technician drives an hour and doesn't have the
-- part. The company already knew which part it was." The desk works the part
-- out, holds it, and texts the briefing. Nothing then checks it was picked up.
--
-- `reservations` records `reserved_at` and `released_at`, which is a claim on
-- stock rather than a fact about a van. A held part and a loaded part are not
-- the same thing, and the difference is the entire failure this system exists
-- to prevent.
--
-- The field-service literature is blunt about it: in audits where first-time
-- fix sat below 75%, the cause was a technician leaving the depot without
-- confirmed parts, and making that confirmation mandatory rather than optional
-- moves the number within two weeks. It is a preparation problem, not a skill
-- problem.

PRAGMA foreign_keys = ON;

-- When the technician said they had it. Null means nobody has confirmed.
ALTER TABLE reservations ADD COLUMN picked_at TEXT;

CREATE INDEX IF NOT EXISTS ix_reservations_picked
    ON reservations(visit_id, picked_at);
