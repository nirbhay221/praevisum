-- Calls that never connected, and conversations that deserve finishing.
--
-- Additive.
--
-- WHY MISSED CALLS NEED A TABLE AT ALL
--
-- The call row is written inside the media stream's `start` event. A caller
-- who hangs up before the stream connects, or whose stream fails, produces no
-- row anywhere: the call did not happen as far as this system is concerned.
--
-- For a service desk that is the worst blindness available. A kitchen with a
-- dead freezer that rang, did not get through, and rang a competitor instead
-- is the single most expensive event this business has, and it was invisible.
--
-- Twilio knows. It posts a status for every call including the ones that never
-- reached us: no-answer, busy, failed, canceled. That verdict had nowhere to
-- land, so this is where it lands.

PRAGMA foreign_keys = ON;

-- Twilio's own identifier, so a status callback can find the call the media
-- stream created. Matching on phone number and a time window was the
-- alternative and it is guesswork the moment two people ring at once.
ALTER TABLE calls ADD COLUMN twilio_sid TEXT;
ALTER TABLE calls ADD COLUMN connected INTEGER DEFAULT 1;

CREATE INDEX IF NOT EXISTS ix_calls_sid ON calls(twilio_sid);


-- Something we owe somebody, and what we already know so they do not have to
-- say it twice.
--
-- Separate from outreach_queue on purpose. That queue is for calls WE decided
-- to make: recalls, predictions, offers, all governed by marketing consent
-- because we are the ones initiating. These are the other thing entirely, a
-- conversation the customer started that got cut off, or a job of theirs we
-- said we would check on. Mixing them would put a dropped service call behind
-- a discount offer in the same priority order, and subject it to a written
-- consent rule that exists for marketing.
CREATE TABLE IF NOT EXISTS followups (
    id           TEXT PRIMARY KEY,
    dealer_id    TEXT REFERENCES dealers(id),
    -- A closed set on purpose: followup.render() dispatches on this and
    -- refuses to send a kind it has no wording for, so an unknown value here
    -- would be a message nobody wrote. `review_ask` was added when the loop
    -- was extended past "is it holding now?", and SQLite cannot alter a CHECK,
    -- so adding a kind means the rebuild in scripts/allow_review_ask.py.
    kind         TEXT NOT NULL
                 CHECK (kind IN ('missed_call','dropped_call','after_visit',
                                 'escalation','review_ask',
                                 'delivery_check_in',
                                 'offer_consent')),

    account_id   TEXT REFERENCES accounts(id),
    contact_id   TEXT REFERENCES contacts(id),
    phone        TEXT NOT NULL,

    from_call    TEXT REFERENCES calls(id),
    work_order_id TEXT REFERENCES work_orders(id),

    -- What they had already told us before the line went. Written so the
    -- follow-up can pick up mid-sentence rather than starting again, because
    -- making somebody repeat a model number they already read out twice is
    -- how a desk loses them for good.
    context      TEXT,

    due_after    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','sent','skipped','answered')),
    sent_at      TEXT,
    sent_via     TEXT,           -- whichever channel they used to reach us
    reply        TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_followups_due ON followups(status, due_after);
CREATE INDEX IF NOT EXISTS ix_followups_dealer ON followups(dealer_id, status);

-- One open follow-up per call. A redelivered status webhook must not queue a
-- second message to somebody who is already going to get one.
CREATE UNIQUE INDEX IF NOT EXISTS ix_followups_once
    ON followups(kind, phone, COALESCE(from_call, work_order_id, ''));
