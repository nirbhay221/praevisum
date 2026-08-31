-- Who the customer is to us, and what a warranty claim has to prove.
--
-- Additive.
--
-- THE HOLE THIS CLOSES
--
-- register_asset takes the install date from whatever the caller says on the
-- phone, and covers() then treated that date as OUR RECORD. So anyone could
-- ring, say the machine went in last year, and be quoted zero. The system had
-- no way to tell the difference between a date we wrote down when we sold the
-- machine and a date somebody told us ninety seconds ago.
--
-- That is not a warranty, it is an honour system with a database attached.
--
-- WHAT A DATE IS WORTH DEPENDS ENTIRELY ON WHERE IT CAME FROM
--
--   sold_by_us       we installed it, we have the paperwork, cover is ours to
--                    grant and nobody has to prove anything
--   customer_stated  they told us on the phone. Might be exactly right. It is
--                    a CLAIM, and a claim needs evidence before it becomes a
--                    discount
--   plate            read off the rating plate in a photo. Better than a
--                    memory, still not a purchase record
--   unknown          we have nothing
--
-- HOW A CLAIM IS SETTLED
--
-- Not by the model, and not on the call. The customer either shows the
-- paperwork to the technician who turns up, or sends a photograph of it to
-- one of the channels we already answer. It is then a person who decides,
-- and the quote is credited rather than never having been charged.
--
-- Charging and crediting is the right way round. Quoting zero on an unproven
-- claim and then invoicing when it falls through is how a customer stops
-- believing us; quoting the real number and taking it off when they produce
-- the paperwork costs them nothing and surprises nobody.

PRAGMA foreign_keys = ON;

-- Where the install date came from. Null on everything that existed before
-- this column, which is honest: we genuinely do not know for those.
ALTER TABLE assets ADD COLUMN installed_source TEXT;

-- Where a customer can send a photograph of their paperwork. Real channels
-- the desk already answers, because telling somebody to post a letter is the
-- same as telling them not to bother.
ALTER TABLE dealers ADD COLUMN proof_email TEXT;
ALTER TABLE dealers ADD COLUMN proof_whatsapp TEXT;
ALTER TABLE dealers ADD COLUMN proof_telegram TEXT;

-- What a first visit costs somebody with no account, as a multiple of the
-- ordinary rate. Not a punishment: there are no credit terms, no service
-- agreement, no history of the site, and the money is collected on the day.
ALTER TABLE dealers ADD COLUMN new_customer_rate REAL;


-- A customer says their machine is under warranty and we have no record of
-- selling it to them.
--
-- The claim is opened on the call, the visit is quoted and booked as
-- chargeable, and the credit happens when a person has seen the paperwork.
-- Nothing here decides anything on its own.
CREATE TABLE IF NOT EXISTS warranty_claims (
    id         TEXT PRIMARY KEY,
    dealer_id  TEXT REFERENCES dealers(id),
    account_id TEXT REFERENCES accounts(id),
    asset_id   TEXT REFERENCES assets(id),
    call_id    TEXT,
    quote_id   TEXT,

    -- What the customer says, in their words, kept separate from anything we
    -- hold. This is the claim, not a fact.
    claimed_installed_on TEXT,
    claimed_terms        TEXT,
    would_credit         REAL,       -- what it is worth to them if it stands

    state      TEXT NOT NULL DEFAULT 'awaiting_proof'
               CHECK (state IN ('awaiting_proof','evidence_received',
                                'accepted','rejected','expired')),

    evidence_channel TEXT,           -- whatsapp, telegram, email, on_site
    evidence_ref     TEXT,           -- message id, file, or the visit it was shown at
    evidence_at      TEXT,

    -- Who decided, because a model must not. A technician who saw the
    -- paperwork on the doorstep counts; the desk does not.
    decided_by   TEXT,
    decided_at   TEXT,
    decided_note TEXT,

    opened_at  TEXT NOT NULL,
    expires_on TEXT               -- after which it is simply a chargeable job
);
CREATE INDEX IF NOT EXISTS ix_claims_asset ON warranty_claims(asset_id);
CREATE INDEX IF NOT EXISTS ix_claims_state ON warranty_claims(state);
