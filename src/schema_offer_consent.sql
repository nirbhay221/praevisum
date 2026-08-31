-- Asking a customer whether we may send them offers, and what they answered.
--
-- Additive.
--
-- WHY A SEPARATE TABLE FROM outreach_consent
--
-- outreach_consent is the PERMISSION: one row per account, read by every
-- outbound path, and the thing that stands between the business and $500 to
-- $1,500 a call. This is the CONVERSATION that led to it: when we asked, what
-- they said on the phone, what they typed back, and whether it is still
-- outstanding.
--
-- Keeping them apart means the permission row stays exactly what it was --
-- small, boring, and written only when a human types a reply -- while the
-- history of asking lives somewhere that can be inspected without touching it.
--
-- It is also what stops us asking twice. A customer who said no once has
-- answered, and asking again tells them their answer was not recorded.

CREATE TABLE IF NOT EXISTS offer_consent_asks (
    id               TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL REFERENCES accounts(id),
    contact_id       TEXT REFERENCES contacts(id),
    dealer_id        TEXT,
    phone            TEXT,

    -- texted   the question has gone out and we are waiting
    -- agreed   they replied yes. outreach_consent now carries the permission
    -- refused  they replied no. Never ask again
    -- unclear  they replied something that is not an answer
    state            TEXT NOT NULL DEFAULT 'texted',

    asked_on         TEXT,
    asked_via        TEXT,
    -- What they said on the CALL, which is why we texted. Not consent itself.
    said_on_the_call TEXT,
    from_call        TEXT,

    answered_on      TEXT,
    -- Their reply, verbatim. This is the evidence the consent row points at.
    answer           TEXT
);
CREATE INDEX IF NOT EXISTS ix_asks_account ON offer_consent_asks(account_id);
CREATE INDEX IF NOT EXISTS ix_asks_phone   ON offer_consent_asks(phone, state);
