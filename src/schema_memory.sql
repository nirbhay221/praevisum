-- What a customer has told us, kept.
--
-- Additive.
--
-- WHY THIS EXISTS
--
-- recall.py said this about itself:
--
--     "Sessions are written back on call end, so a conversation that happens
--      today is retrievable tomorrow. That is the loop closing on the
--      conversational side, the way close_work_order closes it on the repair
--      side."
--
-- It was a dict in the process. It died on every restart, which means every
-- deploy, so the loop it claimed to close was open and the docstring was
-- describing something that had never happened.
--
-- That is the worst class of defect in this project. The README carries an
-- honest status table, the tests assert refusals, and the standing rule is
-- that nothing may claim a capability beyond what is recorded. A comment
-- claiming a closed loop that is not closed breaks the one thing everything
-- else here is built on.
--
-- KEYED ON THE PHONE NUMBER, NOT THE CONTACT
--
-- A number is what every channel has in common and what survives a contact
-- record being merged, renamed or created provisionally on a first call. A
-- caller who rings from the same mobile is the same person to remember,
-- whether or not we have worked out who they are yet.
--
-- THEIR WORDS, NOT A SUMMARY
--
-- The same reason the repair corpus keeps a technician's own phrasing: it is
-- how they will describe the same thing next time, and it is what retrieval
-- is searched with. A summary is our vocabulary, and matching our own
-- vocabulary back to us finds nothing we did not already know.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS caller_memory (
    id         INTEGER PRIMARY KEY,
    phone      TEXT NOT NULL,
    dealer_id  TEXT,

    -- Which conversation it came from. A phone call or a message thread, so
    -- not a foreign key for the same reason decisions.call_id is not.
    from_call  TEXT,

    said       TEXT NOT NULL,
    at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_caller_memory_phone
    ON caller_memory(phone, at DESC);


-- Whether a photograph of the rating plate was needed, and whether it worked.
--
-- The signal behind "lead with the photo for this customer". Somebody who has
-- needed one on every call should not be asked to read a masked model number
-- out loud a fourth time, and somebody who reads it cleanly should never be
-- asked for a picture.
CREATE TABLE IF NOT EXISTS plate_reads (
    id         INTEGER PRIMARY KEY,
    phone      TEXT NOT NULL,
    from_call  TEXT,

    confirmed  INTEGER NOT NULL DEFAULT 0,   -- did the catalogue recognise it
    make       TEXT,
    model      TEXT,
    at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_plate_reads_phone ON plate_reads(phone, at DESC);
