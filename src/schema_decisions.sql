-- Why the desk did what it did, kept.
--
-- Additive.
--
-- WHY THIS EXISTS
--
-- The reasoning was made visible before it was made durable. `events.py` holds
-- the last sixty events per dealer in a deque and loses them on restart, so
-- the arithmetic behind a decision scrolled past a dashboard nobody was
-- watching and was gone.
--
-- That is the wrong half to skip. A live feed answers "what is happening now",
-- which is a demo question. The question a dealer actually has is "why did you
-- put a defrost heater in that van three weeks ago", and until now the honest
-- answer was that nobody knows any more.
--
-- WHY THE NUMBERS ARE STORED SEPARATELY FROM THE SENTENCE
--
-- `line` is what a person reads. `numbers` is what a query can aggregate. If
-- only the sentence were kept, the answer to "how often were we right about
-- the condenser" would mean parsing English back into floats, which is how a
-- record stops being evidence.
--
-- NOTHING HERE IS AN OPINION
--
-- Every row is a value some decision already produced. This table cannot
-- disagree with what the desk did, because it never computes anything.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    dealer_id  TEXT,

    -- Which conversation this belongs to. Null when the decision was not part
    -- of one: a restock sweep and a nightly outreach scan both reason, and
    -- both are worth keeping.
    --
    -- Deliberately NOT a foreign key into `calls`. A phone call has a row
    -- there; a WhatsApp thread does not and should not, because it is not a
    -- call. Both are conversations and both produce reasoning worth keeping,
    -- so this holds whichever identifier the channel uses and joins to `calls`
    -- only when the value happens to be one.
    call_id    TEXT,

    kind       TEXT NOT NULL,   -- distribution, van_load, send, market, settled
    subject    TEXT,            -- the sku, the cause, the make it is about
    verdict    TEXT,            -- carry, skip, send, offer_first, and so on

    -- The rendered sentence, exactly as it appeared on the live feed, so the
    -- record and the dashboard can never tell different stories.
    line       TEXT NOT NULL,

    -- The values behind it, as JSON. Kept apart from the sentence so a
    -- question like "how often did we carry a part we did not need" is a
    -- query rather than an exercise in parsing English.
    numbers    TEXT,

    at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_decisions_call ON decisions(call_id);
CREATE INDEX IF NOT EXISTS ix_decisions_kind ON decisions(dealer_id, kind, at);
