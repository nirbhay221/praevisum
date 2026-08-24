-- Consent, made specific enough to be legal.
--
-- Additive. `outreach_consent` was designed early and designed well, with
-- quiet hours and a frequency cap in the schema before anything could use
-- them. It recorded ONE thing too coarsely: that consent exists.
--
-- The FCC's 2024 Declaratory Ruling makes an AI-generated voice an "artificial
-- or prerecorded voice" under the TCPA, and that is still the position. For a
-- marketing call it requires PRIOR EXPRESS WRITTEN consent, at $500 to $1,500
-- per violation. "They said yes on a service call" is oral, and oral is not
-- enough for an offer.
--
-- The B2B position is narrower than it sounds too: a business's published
-- landline is exempt, a decision-maker's personal mobile is not, even for
-- business talk. So the line type has to be on record or the exemption cannot
-- honestly be claimed.
--
-- None of this makes a safety recall a marketing call. That distinction is
-- carried in code, not inferred here.

PRAGMA foreign_keys = ON;

-- oral, written, or none. Only written satisfies the marketing standard.
ALTER TABLE outreach_consent ADD COLUMN consent_form TEXT;
ALTER TABLE outreach_consent ADD COLUMN evidence_ref TEXT;

-- landline, mobile, or unknown. Unknown is treated as mobile, because
-- guessing in the permissive direction is how the fines happen.
ALTER TABLE phones ADD COLUMN line_type TEXT;
