-- A photograph a customer sent about a job, and what we could read in it.
--
-- Additive.
--
-- WHY THE PICTURE WAS BEING THROWN AWAY
--
-- An inbound photo already goes through a vision model, and what it looks for
-- is the RATING PLATE: make and model, so the desk can identify the machine.
-- That is useful and it is not what a customer photographs when something is
-- wrong. They send the puddle, the frost, the cracked gasket, the error code
-- on the display.
--
-- That picture was read, answered, and dropped. So the engineer arrived
-- knowing the model number and nothing about what they were walking into,
-- which is the opposite of why the desk asks for a photo: to put the right
-- part on the van and save a second visit.
--
-- The bytes are NOT stored. This keeps what the model read, the channel it
-- arrived on and when, which is what a briefing needs, without this database
-- becoming a photo library it was never designed to be.

CREATE TABLE IF NOT EXISTS job_photos (
    id             TEXT PRIMARY KEY,
    work_order_id  TEXT REFERENCES work_orders(id),
    account_id     TEXT REFERENCES accounts(id),
    asset_id       TEXT REFERENCES assets(id),
    dealer_id      TEXT,

    arrived_at     TEXT NOT NULL,
    channel        TEXT,              -- whatsapp, sms
    from_number    TEXT,
    media_type     TEXT,

    -- What the vision model made of it, in words an engineer can act on.
    what_it_shows  TEXT,
    -- Anything it read off a plate, when there happened to be one in shot.
    manufacturer   TEXT,
    model_number   TEXT
);
CREATE INDEX IF NOT EXISTS ix_job_photos_wo ON job_photos(work_order_id);
CREATE INDEX IF NOT EXISTS ix_job_photos_ac ON job_photos(account_id);
